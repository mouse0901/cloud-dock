from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
import httpx
import json
from app.domain import CloudFile


class BaiduApiError(RuntimeError):
    def __init__(self, code, message):
        self.code = str(code)
        super().__init__(message)


@dataclass(frozen=True, repr=False)
class DeviceGrant:
    device_code: str
    user_code: str
    verification_url: str
    qrcode_url: str
    interval: int
    expires_in: int


@dataclass(frozen=True, repr=False)
class Tokens:
    access_token: str
    refresh_token: str
    expires_in: int


OAUTH_MESSAGES = {
    "authorization_pending": "等待你在手机上确认授权",
    "slow_down": "百度要求降低轮询频率，正在延长等待时间",
    "access_denied": "你已拒绝本次授权，可重新获取二维码",
    "authorization_declined": "你已拒绝本次授权，可重新获取二维码",
    "expired_token": "二维码已过期，请重新获取",
    "invalid_grant": "授权码已失效，请重新获取二维码",
    "invalid_client": "应用凭证未通过验证，请核对 AppKey 和 SecretKey",
    "invalid_client_id": "AppKey 无效，请使用控制台中自己的应用凭证",
    "invalid_client_secret": "SecretKey 无效，请检查是否复制完整",
    "invalid_request": "授权请求参数无效，请检查应用凭证与接入设置",
    "unsupported_grant_type": "当前应用不支持这个授权模式",
}
ERRNO_MESSAGES = {
    -6: "登录授权无效，请重新扫码",
    -9: "文件或目录不存在，请检查路径与应用目录是否已创建",
    -7: "没有当前文件或目录的访问权限",
    20016: "登录已过期，请重新扫码",
    20017: "登录凭证已失效，请重新扫码",
    20020: "当前路径不在应用获准访问的目录范围内",
    31360: "下载链接已过期，请重新点击下载",
    20011: "应用已达到可授权用户数量限制",
    20012: "应用接口调用频率或次数超限，请稍后重试",
    20013: "应用没有此接口权限，请检查控制台权限",
}


def official_device_url(value: str, *, qr=False) -> str:
    """Accept only Baidu's documented HTTPS device endpoints."""
    if not isinstance(value, str):
        raise ValueError("授权地址格式无效")
    url = urlsplit(value)
    allowed_path = url.path.startswith("/device/qrcode/") if qr else url.path.rstrip("/") == "/device"
    if (url.scheme != "https" or url.hostname != "openapi.baidu.com"
            or url.port not in (None, 443) or url.username or url.password
            or url.fragment or not allowed_path):
        raise ValueError("授权接口返回了不受支持的地址")
    return value


class BaiduApi:
    """Baidu OAuth and read-only directory calls; no credential persistence."""
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _get(self, url, params):
        try:
            response = await self.client.get(
                url, params=params, headers={"User-Agent": "pan.baidu.com"},
                timeout=httpx.Timeout(20, connect=10), follow_redirects=False)
        except httpx.HTTPError:
            # Never expose request URLs, tokens, secrets, or exception causes.
            raise BaiduApiError("network", "无法连接百度服务，请检查网络后重试") from None
        try:
            body = response.json()
        except ValueError:
            raise BaiduApiError("invalid_response", "接口返回了无法识别的数据") from None
        if not isinstance(body, dict):
            raise BaiduApiError("invalid_response", "接口返回了无法识别的数据")
        # OAuth pending/slow_down can arrive with HTTP 400; preserve their state.
        if body.get("error"):
            raw = str(body["error"])
            code = raw if raw in OAUTH_MESSAGES else "oauth_error"
            raise BaiduApiError(code, OAUTH_MESSAGES.get(code, "百度拒绝了授权请求，请核对应用信息与权限"))
        if not 200 <= response.status_code < 300:
            raise BaiduApiError(response.status_code, f"百度接口请求失败（HTTP {response.status_code}）")
        if body.get("errno", 0) not in (0, "0"):
            try:
                errno = int(body["errno"])
            except (ValueError, TypeError):
                errno = "api_error"
            raise BaiduApiError(errno, ERRNO_MESSAGES.get(errno, "接口拒绝了当前操作，请检查账号授权与应用权限"))
        return body

    async def device_code(self, app_key: str) -> DeviceGrant:
        if not app_key.strip():
            raise ValueError("缺少 AppKey")
        body = await self._get("https://openapi.baidu.com/oauth/2.0/device/code",
            {"response_type": "device_code", "client_id": app_key.strip(), "scope": "basic,netdisk"})
        try:
            device, user = body["device_code"], body["user_code"]
            if not isinstance(device, str) or not device or not isinstance(user, str) or not user:
                raise ValueError()
            interval, expires = max(5, int(body["interval"])), int(body["expires_in"])
            if not 1 <= expires <= 86400 or interval > expires:
                raise ValueError()
            verification = official_device_url(body["verification_url"])
            qr = official_device_url(body["qrcode_url"], qr=True)
            return DeviceGrant(device, user, verification, qr, interval, expires)
        except (KeyError, TypeError, ValueError):
            raise BaiduApiError("invalid_response", "百度未返回有效的设备码或二维码地址") from None

    async def exchange_device(self, app_key, secret_key, device_code) -> Tokens:
        if not all(isinstance(v, str) and v.strip() for v in (app_key, secret_key, device_code)):
            raise ValueError("应用凭证或设备码为空")
        body = await self._get("https://openapi.baidu.com/oauth/2.0/token",
            {"grant_type": "device_token", "code": device_code, "client_id": app_key, "client_secret": secret_key})
        try:
            access, refresh, expires = body["access_token"], body["refresh_token"], int(body["expires_in"])
            if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh or expires <= 0:
                raise ValueError()
            return Tokens(access, refresh, expires)
        except (KeyError, TypeError, ValueError):
            raise BaiduApiError("invalid_response", "授权响应缺少有效凭证，请重新授权") from None

    async def list_files(self, access_token, path="/", start=0, limit=50) -> list[CloudFile]:
        if start < 0 or not 1 <= limit <= 1000:
            raise ValueError("分页参数无效")
        body = await self._get("https://pan.baidu.com/rest/2.0/xpan/file",
            {"method": "list", "access_token": access_token, "dir": path,
             "start": start, "limit": limit, "order": "name"})
        try:
            rows = body["list"]
            if not isinstance(rows, list):
                raise ValueError()
            return [CloudFile(str(x["fs_id"]), "baidu", x["path"], bool(x["isdir"]),
                int(x.get("size", 0)), datetime.fromtimestamp(x["server_mtime"]).strftime("%Y-%m-%d %H:%M")
                if x.get("server_mtime") else "未知") for x in rows]
        except (KeyError, TypeError, ValueError, OSError, OverflowError):
            raise BaiduApiError("invalid_response", "百度返回的目录数据不完整") from None

    async def download_info(self, access_token, item):
        if item.is_dir or item.provider != "baidu" or not item.id.isascii() or not item.id.isdigit():
            raise ValueError("请选择一个真实百度文件")
        body = await self._get("https://pan.baidu.com/rest/2.0/xpan/multimedia",
            {"method": "filemetas", "access_token": access_token,
             "fsids": json.dumps([int(item.id)]), "dlink": 1})
        try:
            rows = body["list"]
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError()
            row = rows[0]
            if (row.get("errno", 0) not in (0, "0") or str(row["fs_id"]) != item.id
                    or row["path"] != item.path or int(row["isdir"]) != 0):
                raise ValueError()
            size, link = int(row["size"]), row["dlink"]
            if size < 0 or not isinstance(link, str) or not link:
                raise ValueError()
            return DownloadInfo(link, size)
        except (KeyError, TypeError, ValueError):
            raise BaiduApiError("invalid_response", "文件信息与所选文件不匹配，已停止下载") from None


@dataclass(frozen=True, repr=False)
class DownloadInfo:
    url: str
    size: int
