"""Experimental Passport web QR flow, independent of Open Platform OAuth.

Only a fresh client cookie jar is used. No browser profile is read and no
credentials or raw server responses are persisted or included in errors.
"""
from __future__ import annotations
import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit, urljoin
import httpx
from app.domain import CloudFile
from app.providers.baidu_api import BaiduApiError
from app.providers.baidu_web_download import BaiduWebDownloads

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36")


@dataclass(frozen=True, repr=False)
class WebQr:
    sign: str
    image: bytes


@dataclass(frozen=True, repr=False)
class QrState:
    state: str
    confirmation: str = ""


def _normalize_js_strings(value):
    """Convert quoted JS string literals to JSON; never evaluate expressions."""
    output, index = [], 0
    escapes = {"b":"\b", "f":"\f", "n":"\n", "r":"\r", "t":"\t", "v":"\v",
               "\\":"\\", "/":"/", "'":"'", '"':'"'}
    while index < len(value):
        quote = value[index]
        if quote not in ("'", '"'):
            output.append(quote)
            index += 1
            continue
        index += 1
        chars = []
        while index < len(value):
            char = value[index]
            index += 1
            if char == quote:
                output.append(json.dumps("".join(chars), ensure_ascii=True))
                break
            if ord(char) < 32:
                raise ValueError("control character")
            if char != "\\":
                chars.append(char)
                continue
            if index >= len(value):
                raise ValueError("unfinished escape")
            escaped = value[index]
            index += 1
            if escaped in escapes:
                chars.append(escapes[escaped])
            elif escaped in ("u", "x"):
                width = 4 if escaped == "u" else 2
                digits = value[index:index+width]
                if len(digits) != width or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                    raise ValueError("invalid unicode escape")
                chars.append(chr(int(digits, 16)))
                index += width
            elif escaped == "0" and (index == len(value) or not value[index].isdigit()):
                chars.append("\0")
            elif escaped in ("\r", "\n"):
                if escaped == "\r" and index < len(value) and value[index] == "\n":
                    index += 1
            else:
                raise ValueError("unsupported escape")
        else:
            raise ValueError("unterminated string")
    return "".join(output)


def parse_jsonp(text):
    try:
        value = text.strip().lstrip("\ufeff")
        if not value.startswith("{"):
            match = re.fullmatch(r"(?:cloudDockCallback)?\s*\(\s*(\{.*\})\s*\)\s*;?", value, re.S)
            if not match:
                raise ValueError("unsupported wrapper")
            value = match[1]
        try:
            result = json.loads(value)
        except json.JSONDecodeError:
            # Passport qrbdusslogin emits an object with single-quoted fields.
            # Only quoted strings are normalized; functions/operators still fail JSON parsing.
            result = json.loads(_normalize_js_strings(value))
        if not isinstance(result, dict):
            raise ValueError("not an object")
        return result
    except (ValueError, TypeError, RecursionError):
        raise BaiduApiError("web_response", "百度返回的数据暂时无法识别，请刷新二维码") from None


def web_url(value, hosts):
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or url.hostname not in hosts
                or url.port not in (None, 443) or url.username or url.password or url.fragment):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise BaiduApiError("web_address", "登录服务返回了不支持的地址，已停止请求") from None


def valid_path(path):
    return (isinstance(path,str) and path.startswith("/") and "\\" not in path
            and "\x00" not in path and (path == "/" or all(p not in ("",".","..") for p in path[1:].split("/"))))


class BaiduWebApi(BaiduWebDownloads):
    def __init__(self, client, diagnostic=None):
        self.client = client
        self.diagnostic = diagnostic
        self._bdstoken = ""
        self.authenticated = False
        self._initial_root = None
        self.session_candidate = False

    def reset(self):
        self.client.cookies.clear()
        self._bdstoken = ""
        self.authenticated = False
        self._initial_root = None
        self.session_candidate = False

    async def request(self, url, params=None, *, long_poll=False):
        web_url(url, {"passport.baidu.com", "pan.baidu.com"})
        try:
            response = await self.client.get(url, params=params,
                headers={"User-Agent": UA, "Referer": "https://pan.baidu.com/"},
                follow_redirects=False, timeout=httpx.Timeout(40 if long_poll else 20, connect=10))
        except httpx.ReadTimeout:
            if long_poll:
                return None
            raise BaiduApiError("web_network", "百度服务响应超时，请重试") from None
        except httpx.HTTPError:
            raise BaiduApiError("web_network", "暂时无法连接百度服务，请检查网络") from None
        if response.status_code >= 400:
            raise BaiduApiError("web_http", f"百度服务请求未成功（HTTP {response.status_code}）")
        if len(response.content) > 4 * 1024 * 1024:
            raise BaiduApiError("web_response", "登录响应过大，已停止处理")
        return response

    def emit(self, event):
        if self.diagnostic:
            try:
                self.diagnostic(event)
            except Exception:
                pass

    def has_pan_session(self):
        # Let the cookie jar apply domain/path/secure rules for the target service.
        request = self.client.build_request("GET", "https://pan.baidu.com/api/list")
        cookie = request.headers.get("cookie", "")
        return any(re.match(r"^(?:BDUSS|BDUSS_BFESS)=[^;]+$", part.strip())
                   for part in cookie.split(";"))

    def parse_response(self, response, stage):
        labels = {"qr": "获取二维码", "poll": "扫码状态", "finish": "手机确认后的登录", "directory": "目录读取"}
        event = {"stage": stage, "http_status": response.status_code,
                 "response_bytes": len(response.content)}
        try:
            body = parse_jsonp(response.text)
            event["parsed"] = True
            info = body.get("errInfo")
            code = info.get("no") if isinstance(info, dict) else body.get("errno")
            if isinstance(code, (str, int)) and re.fullmatch(r"-?\d{1,8}", str(code)):
                event["service_code"] = str(code)
            return body
        except BaiduApiError:
            event["parsed"] = False
            event["format"] = "html" if response.text.lstrip().startswith("<") else "data"
            raise BaiduApiError("web_response", labels.get(stage, "百度服务") + "返回格式无法识别，请重试") from None
        finally:
            self.emit(event)

    async def create_qr(self):
        response = await self.request("https://passport.baidu.com/v2/api/getqrcode",
            {"lp":"pc","qrloginfrom":"pc","tpl":"netdisk","apiver":"v3","tt":int(time.time()*1000)})
        body = self.parse_response(response, "qr")
        if body.get("errno") not in (0,"0"):
            raise BaiduApiError("qr_unavailable", "百度暂未提供登录二维码，请稍后重试")
        sign, url = body.get("sign"), body.get("imgurl")
        if not isinstance(sign,str) or not sign or not isinstance(url,str):
            raise BaiduApiError("web_response", "没有收到有效二维码，请重新获取")
        if url.startswith("//"): url = "https:" + url
        elif not url.startswith("https://"): url = "https://" + url
        web_url(url, {"passport.baidu.com"})
        image = await self.request(url)
        if (image.status_code != 200 or not image.content.startswith(b"\x89PNG\r\n\x1a\n")
                or len(image.content) > 2*1024*1024):
            raise BaiduApiError("qr_image", "二维码图片未能加载，请刷新")
        return WebQr(sign, image.content)

    async def poll(self, sign):
        response = await self.request("https://passport.baidu.com/channel/unicast",
            {"channel_id":sign,"tpl":"netdisk","apiver":"v3","_sdkFrom":1,"callback":"cloudDockCallback","tt":int(time.time()*1000)},
            long_poll=True)
        if response is None:
            return QrState("waiting")
        body = self.parse_response(response, "poll")
        if body.get("errno") in (1,"1"):
            return QrState("waiting")
        if body.get("errno") not in (0,"0"):
            raise BaiduApiError("qr_status", "二维码状态读取失败，请刷新二维码")
        data = body.get("channel_v")
        if not data:
            return QrState("waiting")
        try:
            if isinstance(data,str): data = json.loads(data)
            if not isinstance(data,dict): raise ValueError()
            status = str(data["status"])
        except (ValueError,TypeError,KeyError):
            raise BaiduApiError("web_response", "扫码状态格式发生变化，请重新获取二维码") from None
        if status == "0":
            token = data.get("v")
            if not isinstance(token,str) or not token:
                raise BaiduApiError("web_response", "手机确认信息不完整，请重新扫码")
            return QrState("confirmed", token)
        if status == "1": return QrState("scanned")
        if status == "2": return QrState("cancelled")
        raise BaiduApiError("qr_status", "收到未知扫码状态，请重新获取二维码")

    async def finish_login(self, confirmation):
        try:
            await self._finish_login(confirmation)
        except asyncio.CancelledError:
            self.reset()
            raise
        except Exception:
            # A phone-approved session stays in this window for a read-only retry.
            # It is never reported as authenticated until the directory verifies.
            if not self.session_candidate:
                self.reset()
            raise

    async def restore_login(self):
        self.session_candidate = self.has_pan_session()
        try:
            await self.resume_login()
        except BaiduApiError as first:
            if first.code != 'web_directory':
                raise
            # The optional CSRF token is reacquired, never stored alongside cookies.
            if await self._try_landing_token():
                try:
                    await self._verify_directory()
                    return
                except BaiduApiError as second:
                    first = second
            if getattr(first,'service_code',None) in (-6,'-6'):
                raise BaiduApiError('web_session','保存的登录已失效，请重新扫码') from None
            raise first

    async def resume_login(self):
        if not self.session_candidate or not self.has_pan_session():
            self.reset()
            raise BaiduApiError("web_session", "当前会话已失效，请刷新二维码重新扫码")
        try:
            await self._verify_directory()
        except asyncio.CancelledError:
            self.reset()
            raise

    async def _finish_login(self, confirmation):
        response = await self.request("https://passport.baidu.com/v3/login/main/qrbdusslogin",
            {"bduss":confirmation,"u":"https://pan.baidu.com/disk/main","loginVersion":"v5",
             "qrcode":1,"tpl":"netdisk","callback":"cloudDockCallback","v":int(time.time()*1000)})
        # The web callback includes display-only JavaScript data that is not
        # necessarily JSON. Never execute it or discard a server-issued cookie
        # only because unrelated display fields cannot be decoded.
        body = None
        try:
            body = self.parse_response(response, "finish")
        except BaiduApiError:
            pass
        info = body.get("errInfo") if isinstance(body, dict) else None
        if isinstance(body, dict) and "errInfo" in body and not isinstance(info, dict):
            raise BaiduApiError("web_response", "登录确认状态不完整，请刷新二维码")
        if isinstance(info, dict) and str(info.get("no")) != "0":
            self.emit({"stage": "session", "session_cookie": self.has_pan_session(),
                       "outcome": "service_rejected"})
            raise BaiduApiError("web_verification", "百度尚未完成登录，可能需要在官方网页完成额外验证")
        has_session = self.has_pan_session()
        self.emit({"stage": "session", "session_cookie": has_session,
                   "outcome": "candidate" if has_session else "missing"})
        if response.status_code != 200 or not has_session:
            raise BaiduApiError("web_session", "手机确认后尚未收到网盘会话，暂不能进入账号")
        self.session_candidate = True
        # A display-page redirect is not authentication evidence. Try the actual
        # directory first; only obtain the page's optional token if needed.
        try:
            await self._verify_directory()
            return
        except BaiduApiError as first_error:
            if first_error.code != "web_directory":
                raise
        if await self._try_landing_token():
            await self._verify_directory()
            return
        raise BaiduApiError("web_directory", "手机已确认，但网盘目录暂未读取成功，可点击“重试连接”")

    async def _try_landing_token(self):
        url = "https://pan.baidu.com/disk/main"
        try:
            for _ in range(3):
                page = await self.request(url)
                if page.is_redirect:
                    raw = page.headers.get("location", "")
                    target = urljoin(url, raw)
                    parts = urlsplit(target)
                    destination = ("pan_https" if parts.hostname == "pan.baidu.com" and parts.scheme == "https"
                                   else "other")
                    self.emit({"stage": "landing_redirect", "outcome": destination,
                               "has_fragment": bool(parts.fragment)})
                    # A fragment is local browser navigation, not an HTTP target.
                    # Unknown hosts, HTTP and userinfo are never followed.
                    target = parts._replace(fragment="").geturl()
                    url = web_url(target, {"pan.baidu.com"})
                    continue
                match = re.search(r'["\']bdstoken["\']\s*:\s*["\']([A-Za-z0-9_-]{8,128})["\']', page.text)
                if match:
                    self._bdstoken = match[1]
                    return True
                break
        except (BaiduApiError, ValueError):
            self.emit({"stage": "landing", "outcome": "skipped"})
        return False

    async def _verify_directory(self):
        self.authenticated = False
        self._initial_root = None
        # The actual file service is authoritative. A cookie alone, or a
        # callback saying success, never marks the account as authenticated.
        rows = await self._fetch_files("/", 0)
        self._initial_root = rows
        self.authenticated = True
        self.emit({"stage": "session", "session_cookie": True,
                   "outcome": "verified", "entry_count": len(rows)})

    async def list_files(self, path, page=0):
        if not self.authenticated:
            raise BaiduApiError("web_session", "请先扫码登录")
        if path == "/" and page == 0 and self._initial_root is not None:
            rows, self._initial_root = self._initial_root, None
            return rows
        return await self._fetch_files(path, page)

    async def _fetch_files(self, path, page=0):
        if not valid_path(path) or page < 0:
            raise ValueError("目录路径无效")
        params={"dir":path,"page":page+1,"num":50,"order":"name","desc":0,
                "showempty":0,"web":1,"channel":"chunlei","clienttype":0}
        if self._bdstoken: params["bdstoken"]=self._bdstoken
        response=await self.request("https://pan.baidu.com/api/list",params)
        body=self.parse_response(response, "directory")
        if body.get("errno") not in (0,"0"):
            error = BaiduApiError("web_directory", "网页登录会话尚不能读取该目录，请重新扫码或稍后重试")
            error.service_code = body.get("errno")
            raise error
        try:
            rows=body["list"]
            if not isinstance(rows,list): raise ValueError()
            result=[]
            for row in rows:
                remote=row["path"]
                if not valid_path(remote) or str(PurePosixPath(remote).parent)!=path:
                    raise ValueError()
                if str(row["isdir"]) not in ("0", "1"):
                    raise ValueError()
                result.append(CloudFile(str(row["fs_id"]),"baidu",remote,str(row["isdir"]) == "1",
                    int(row.get("size",0)),
                    datetime.fromtimestamp(row["server_mtime"]).strftime("%Y-%m-%d %H:%M")
                    if row.get("server_mtime") else "未知",
                    str(row.get("server_mtime",0))+":"+str(row.get("md5","")),
                    next((str(v) for v in (row.get("thumbs") or {}).values() if isinstance(v,str) and v.startswith("https://")), "")))
            self.emit({"stage": "directory_ready", "entry_count": len(result)})
            return result
        except (KeyError,ValueError,TypeError,OverflowError,OSError):
            raise BaiduApiError("web_response", "目录响应不完整，已停止展示") from None


class WebDirectory:
    root = "/"
    def __init__(self, api):
        self.api = api
    async def list_page(self, path, page=0):
        return await self.api.list_files(path,page)
