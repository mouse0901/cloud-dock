"""Single-file native download. No transfer simulation or automatic retries."""
from __future__ import annotations
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from uuid import uuid4
import httpx
from app.baidu_auth import within_application
from app.providers.baidu_api import BaiduApiError


def download_url(value):
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        trusted = any(host == domain or host.endswith("." + domain)
                      for domain in ("baidu.com", "baidupcs.com"))
        if (parsed.scheme != "https" or not trusted or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError()
        return value
    except (TypeError, ValueError):
        raise BaiduApiError("download_address", "下载地址不属于支持的百度 HTTPS 地址，已停止请求") from None


def local_filename(name):
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .")
    if not clean:
        clean = "download"
    if clean.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1,10)], *[f"LPT{i}" for i in range(1,10)]}:
        clean = "_" + clean
    parsed = Path(clean)
    return parsed.stem[:150] + "-" + uuid4().hex[:10] + parsed.suffix[:20]


async def download_file(reader, item, destination, on_progress):
    # Validate before metadata lookup, not only before downloading the bytes.
    if item.is_dir or item.provider != "baidu" or not within_application(item.path, reader.root):
        raise ValueError("仅支持下载当前账号应用目录内的单个文件")
    info = await reader.api.download_info(reader.tokens.access_token, item)
    url = str(httpx.URL(download_url(info.url)).copy_merge_params(
        {"access_token": reader.tokens.access_token}))
    folder = Path(destination).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / local_filename(item.name)
    part = folder / (target.name + ".part")
    if target.parent != folder or part.parent != folder:
        raise ValueError("下载目录无效")
    total = 0
    prefix = bytearray()
    try:
        for redirect_count in range(6):
            download_url(url)  # Validate every redirect before sending credentials.
            async with reader.api.client.stream("GET", url,
                    headers={"User-Agent": "pan.baidu.com", "Accept-Encoding": "identity"},
                    timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location or redirect_count == 5:
                        raise BaiduApiError("download_redirect", "下载地址跳转失败，请重新尝试")
                    url = download_url(urljoin(url, location))
                    # Retain auth only after the new host has passed validation.
                    url = str(httpx.URL(url).copy_merge_params({"access_token": reader.tokens.access_token}))
                    continue
                if response.status_code != 200:
                    raise BaiduApiError("download_http", f"下载失败（HTTP {response.status_code}），请重新尝试")
                with part.open("xb") as output:
                    async for chunk in response.aiter_bytes(256 * 1024):
                        total += len(chunk)
                        if total > info.size:
                            raise BaiduApiError("download_size", "下载内容与网盘文件大小不符，未保留不完整文件")
                        if len(prefix) < 65536:
                            prefix.extend(chunk[:65536-len(prefix)])
                        output.write(chunk)
                        await on_progress(total, info.size)
                if total != info.size:
                    raise BaiduApiError("download_size", "下载未完整结束，请重新下载")
                if total <= 65536 and "json" in response.headers.get("content-type", "").lower():
                    try:
                        payload = json.loads(prefix)
                    except (ValueError, UnicodeDecodeError):
                        payload = None
                    if (isinstance(payload, dict) and payload.get("errno") not in (None, 0, "0")
                            and ("request_id" in payload or "show_msg" in payload)):
                        raise BaiduApiError("download_api", "百度返回了下载错误，未保存为文件")
                # Windows rename fails if the destination already exists.
                part.rename(target)
                return target
        raise BaiduApiError("download_redirect", "下载跳转次数过多")
    except httpx.HTTPError:
        raise BaiduApiError("download_network", "下载连接中断，请重试") from None
    except OSError:
        raise BaiduApiError("download_disk", "无法写入下载目录，请检查磁盘空间与目录权限") from None
    finally:
        part.unlink(missing_ok=True)
