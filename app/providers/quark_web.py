"""Quark web session adapter derived from publicly served official JavaScript.

Evidence and limits: docs/QUARK-PROTOCOL-RESEARCH.md. The adapter uses in-memory cookies; the owning panel can encrypt them for reuse.
Share tokens remain in memory. No browser profile is read; no save/delete API is called.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx

from app.domain import CloudFile

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36")
# Download capability depends on the client profile. Keep this identity the
# same for link acquisition and the actual stream. Login/directory keep UA.
# Protocol evidence: docs/QUARK-DOWNLOAD-ROUTE-REVIEW.md (GeZi + QuarkPanTool).
DOWNLOAD_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 "
               "Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch")
PAN = "https://pan.quark.cn"
DRIVE = "https://drive-pc.quark.cn/1/clouddrive"
SHARE = "https://drive-h.quark.cn/1/clouddrive"
UOP = "https://uop.quark.cn/cas/ajax"
API_HOSTS = frozenset({"pan.quark.cn", "drive-pc.quark.cn", "drive-h.quark.cn", "uop.quark.cn"})
PARAMS = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
MAX_RESPONSE = 4 * 1024 * 1024


class QuarkApiError(Exception):
    def __init__(self, code: str, message: str, *, service_code=None, http_status=None):
        self.service_code = service_code if type(service_code) is int else None
        self.http_status = http_status if type(http_status) is int else None
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, repr=False)
class QuarkQr:
    token: str
    url: str
    expires_in: int = 300


@dataclass(frozen=True, repr=False)
class QuarkQrState:
    state: str
    confirmation: str = ""


@dataclass(repr=False)
class QuarkShare:
    id: str
    token: str
    session_id: str
    title: str = "夸克分享"
    file_tokens: dict[str, str] = field(default_factory=dict)
    files: dict[str, CloudFile] = field(default_factory=dict)
    directories: dict[str, str] = field(default_factory=lambda: {"/": "0"})


@dataclass(frozen=True, repr=False)
class QuarkDownload:
    url: str
    headers: dict[str, str]
    size: int
    name: str
    file_id: str
    md5: str | None = None


def _opaque(value, *, maximum=4096):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise QuarkApiError("quark_response", "夸克返回的认证或文件信息不完整，请重试")
    return value


def _fid(value):
    value = str(value) if isinstance(value, int) and not isinstance(value, bool) else value
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
        raise QuarkApiError("quark_response", "夸克返回了无法识别的文件标识")
    return value


def valid_path(path):
    return (isinstance(path, str) and path.startswith("/") and "\\" not in path
            and not any(ord(c) < 32 for c in path)
            and (path == "/" or all(p not in ("", ".", "..") for p in path[1:].split("/"))))


def checked_url(value, *, download=False):
    """Only provider-controlled HTTPS hosts; never follow arbitrary redirects."""
    try:
        p = urlsplit(value)
        host = (p.hostname or "").lower()
        allowed = (host == "quark.cn" or host.endswith(".quark.cn")) if download else host in API_HOSTS
        if (not isinstance(value, str) or not allowed or p.scheme != "https" or p.port not in (None, 443)
                or p.username or p.password or p.fragment or "\\" in value
                or any(ord(c) < 33 for c in value)):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise QuarkApiError("quark_address", "夸克返回了尚未验证的服务地址，已停止请求") from None


def parse_share_link(text, passcode=""):
    """Accept a copied share sentence but never fetch a user-supplied host."""
    if not isinstance(text, str) or len(text) > 8192:
        raise QuarkApiError("quark_share_input", "请输入有效的夸克分享链接")
    candidates = re.findall(r"https://[^\s<>\"'，。；]+", text)
    match = None
    for candidate in candidates:
        try:
            p = urlsplit(candidate)
            if p.hostname != "pan.quark.cn" or p.port not in (None, 443) or p.username or p.password:
                continue
            m = re.fullmatch(r"/s/([A-Za-z0-9_-]{6,64})/?", p.path)
            if not m:
                continue
            match = m[1]
            if not passcode:
                passcode = parse_qs(p.query).get("pwd", [""])[0]
            break
        except ValueError:
            continue
    if match is None:
        raise QuarkApiError("quark_share_input", "链接应为 https://pan.quark.cn/s/ 开头的夸克分享")
    if not passcode:
        m = re.search(r"(?:提取码|提取密码|密码)\s*[:：]?\s*([A-Za-z0-9]{4,8})(?![A-Za-z0-9])", text)
        if m:
            passcode = m[1]
    if not isinstance(passcode, str) or not re.fullmatch(r"[A-Za-z0-9]{0,32}", passcode):
        raise QuarkApiError("quark_share_input", "提取码格式不正确")
    return match, passcode


class QuarkWebApi:
    def __init__(self, client: httpx.AsyncClient, diagnostic=None):
        self.client = client
        self.diagnostic = diagnostic
        self.authenticated = False
        self.session_candidate = False
        self.session_id = ""
        self._initial_root = None
        self._directories = {"/": "0"}
        self._files = {}
        self._generation = 0

    def reset(self):
        self._generation += 1
        self.client.cookies.clear()
        self.authenticated = False
        self.session_candidate = False
        self.session_id = ""
        self._initial_root = None
        self._directories = {"/": "0"}
        self._files.clear()

    def emit(self, event):
        if self.diagnostic:
            # Only these non-sensitive metadata values may leave this adapter.
            allowed = {k: v for k, v in event.items() if k in {
                "stage", "route", "http_status", "response_bytes", "parsed", "service_code", "outcome", "entry_count",
                "part_number", "oss_code"}}
            try:
                self.diagnostic(allowed)
            except Exception:
                pass

    async def _request(self, method, url, stage, *, params=None, data=None, user_agent=UA, route=None):
        checked_url(url)
        generation = self._generation
        try:
            async with self.client.stream(method, url, params=params, json=data,
                    headers={"User-Agent": user_agent, "Referer": PAN + "/", "Origin": PAN},
                    follow_redirects=False, timeout=httpx.Timeout(25, connect=10)) as response:
                if response.is_redirect:
                    raise QuarkApiError("quark_redirect", "夸克服务要求跳转，暂未完成请求")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE:
                        raise QuarkApiError("quark_response", "夸克响应过大，已停止处理")
                if generation != self._generation:
                    raise asyncio.CancelledError()
                event = {"stage": stage, "http_status": response.status_code, "response_bytes": len(raw)}
                if route is not None:
                    event["route"] = route
                try:
                    import json
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise ValueError()
                    event["parsed"] = True
                    code = body.get("code", body.get("status"))
                    if type(code) is int:
                        event["service_code"] = code
                except (ValueError, UnicodeError, RecursionError):
                    event["parsed"] = False
                    raise QuarkApiError("quark_response", "夸克返回的数据暂时无法识别，请重试") from None
                finally:
                    self.emit(event)
                if response.status_code in (401, 403):
                    if response.status_code == 401:
                        self.authenticated = False
                    raise QuarkApiError("quark_session" if response.status_code == 401 else "quark_denied",
                                        "登录会话已失效，请重新扫码" if response.status_code == 401 else "夸克暂未允许该请求，请在官方页面检查账号状态")
                if response.status_code >= 400:
                    self._raise_service_error(body, stage, response.status_code)
                return body
        except httpx.TimeoutException:
            raise QuarkApiError("quark_network", "夸克服务响应超时，请重试") from None
        except httpx.HTTPError:
            raise QuarkApiError("quark_network", "暂时无法连接夸克服务，请检查网络") from None

    @staticmethod
    def _raise_service_error(body, stage="", http_status=None, message="夸克未能完成请求，请稍后重试"):
        code = body.get("code")
        status = body.get("status")
        if http_status == 401 or status == 401:
            kind, text = "quark_session", "登录会话已失效，请重新扫码"
        elif http_status in (403, 412) or status in (403, 412):
            kind, text = "quark_denied", "夸克暂未允许该请求，请在官方页面检查账号状态"
        elif http_status == 429 or status == 429:
            kind, text = "quark_rate_limit", "夸克请求过于频繁，请稍后重试"
        elif stage == "download_link" and code == 23018:
            # Context matters: do not mislabel this download error as a broken
            # share or insufficient storage, and never trigger a save operation.
            kind, text = "quark_download_restricted", "夸克返回文件大小限制（代码 23018）；当前客户端取链请求仍未通过，请使用官方客户端核对该文件"
        else:
            kind, text = "quark_service", message
            if type(code) is int:
                text += f"（服务代码 {code}）"
        raise QuarkApiError(kind, text, service_code=code, http_status=http_status)

    @staticmethod
    def _success(body, message="夸克未能完成请求，请稍后重试", *, stage=""):
        if body.get("status") != 200 or body.get("code", 0) not in (0, "0"):
            QuarkWebApi._raise_service_error(body, stage, message=message)
        return body.get("data")

    @staticmethod
    def _uop_params():
        return {"client_id": "532", "v": "1.2", "request_id": str(uuid4())}

    async def create_qr(self):
        body = await self._request("GET", UOP + "/getTokenForQrcodeLogin", "qr", params=self._uop_params())
        try:
            if body.get("status") != 2000000:
                raise ValueError()
            token = _opaque(body["data"]["members"]["token"])
        except (KeyError, TypeError, ValueError):
            raise QuarkApiError("quark_qr", "没有收到有效的夸克二维码，请重新获取") from None
        url = "https://su.quark.cn/4_eMHBJ?" + urlencode({"token": token, "client_id": "532", "ssb": "weblogin",
            "uc_param_str": "", "uc_biz_str": "S:custom|OPT:SAREA@0|OPT:IMMERSIVE@1|OPT:BACK_BTN_STYLE@0"})
        return QuarkQr(token, url)

    async def poll(self, token):
        params = self._uop_params()
        params["token"] = _opaque(token)
        body = await self._request("GET", UOP + "/getServiceTicketByQrcodeToken", "poll", params=params)
        status = body.get("status")
        if status == 50004001:
            return QuarkQrState("waiting")
        if status == 50004002:
            return QuarkQrState("expired")
        if status != 2000000:
            raise QuarkApiError("quark_qr_status", "夸克扫码尚未完成，请刷新二维码后重试")
        try:
            return QuarkQrState("confirmed", _opaque(body["data"]["members"]["service_ticket"]))
        except (KeyError, TypeError):
            raise QuarkApiError("quark_response", "手机确认信息不完整，请重新扫码") from None

    def has_drive_session(self):
        cookie = self.client.build_request("GET", DRIVE + "/file/sort").headers.get("cookie", "")
        return any(re.fullmatch(r"__(?:pus|puus)=[^;]+", part.strip()) for part in cookie.split(";"))

    async def finish_login(self, confirmation):
        self.authenticated = False
        try:
            body = await self._request("GET", PAN + "/account/info", "finish",
                                       params={"st": _opaque(confirmation), "lw": "scan"})
            if body.get("success") is not True or not isinstance(body.get("data"), dict):
                raise QuarkApiError("quark_session", "夸克尚未完成登录，请重新扫码")
            if not self.has_drive_session():
                raise QuarkApiError("quark_session", "手机已确认，但尚未收到有效网盘会话")
            self.session_candidate = True
            await self._verify_directory()
        except asyncio.CancelledError:
            self.reset()
            raise
        except Exception:
            if not self.session_candidate:
                self.reset()
            raise

    async def restore_login(self):
        # Cookie presence is only a candidate; the file service verifies it.
        self.session_candidate = self.has_drive_session()
        await self.resume_login()

    async def resume_login(self):
        if not self.session_candidate or not self.has_drive_session():
            self.reset()
            raise QuarkApiError("quark_session", "当前会话已失效，请重新扫码")
        try:
            await self._verify_directory()
        except asyncio.CancelledError:
            self.reset()
            raise

    async def _verify_directory(self):
        self.authenticated = False
        self._initial_root = None
        rows = await self._fetch_files("/", 0)
        self.authenticated = True
        self.session_id = str(uuid4())
        self._initial_root = rows
        self.emit({"stage": "session", "outcome": "verified", "entry_count": len(rows)})

    def _require_login(self):
        if not self.authenticated:
            raise QuarkApiError("quark_session", "请先扫码登录夸克网盘")

    def _require_share(self, share):
        self._require_login()
        if not isinstance(share, QuarkShare) or share.session_id != self.session_id:
            raise QuarkApiError("quark_share_session", "分享所属会话已结束，请重新打开分享")

    def _decode_files(self, data, path, parent_id, directories, files, share=None):
        try:
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise ValueError()
            result, staged_dirs, staged_files, staged_tokens = [], {}, {}, {}
            for row in data["list"]:
                fid, name = _fid(row["fid"]), row["file_name"]
                if (not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name
                        or any(ord(c) < 32 for c in name)):
                    raise ValueError()
                # Share roots can expose the owner's original parent id even
                # though sharepage/detail is requested with pdir_fid=0.
                # Nested share directories and own-drive listings must still
                # match the directory we actually requested.
                if "pdir_fid" in row and _fid(row["pdir_fid"]) != parent_id and (share is None or parent_id != "0"):
                    raise ValueError()
                directory = row.get("dir")
                if type(directory) not in (bool, int) or directory not in (True, False, 0, 1):
                    raise ValueError()
                size = row.get("size", 0)
                if type(size) is not int or size < 0:
                    raise ValueError()
                modified = row.get("updated_at", 0)
                if type(modified) not in (int, float):
                    raise ValueError()
                if modified > 10**11:
                    modified /= 1000
                remote = ("" if path == "/" else path) + "/" + name
                item = CloudFile(fid, "quark", remote, bool(directory), size,
                                 datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M") if modified else "未知",
                                 str(row.get("updated_at",0))+":"+str(row.get("md5","")),
                                 row.get("thumbnail", "") if isinstance(row.get("thumbnail", ""),str) else "")
                result.append(item)
                staged_files[fid] = item
                if directory:
                    staged_dirs[remote] = fid
                if share is not None and row.get("share_fid_token"):
                    staged_tokens[fid] = _opaque(row["share_fid_token"])
            directories.update(staged_dirs)
            files.update(staged_files)
            if share is not None:
                share.file_tokens.update(staged_tokens)
            self.emit({"stage": "directory_ready", "entry_count": len(result)})
            return result
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise QuarkApiError("quark_response", "目录响应不完整，已停止展示") from None

    async def _fetch_files(self, path, page):
        if not valid_path(path) or type(page) is not int or page < 0 or path not in self._directories:
            raise QuarkApiError("quark_path", "请从已打开的目录逐层进入")
        parent_id = self._directories[path]
        params = dict(PARAMS, pdir_fid=parent_id, _page=page+1, _size=50, _fetch_total=1,
                      _fetch_sub_dirs=0, _sort="file_type:asc,file_name:asc", fetch_all_file=1, fetch_risk_file_name=1)
        body = await self._request("GET", DRIVE + "/file/sort", "directory", params=params)
        data = self._success(body, "夸克尚不能读取该目录，请重新扫码或重试")
        return self._decode_files(data, path, parent_id, self._directories, self._files)

    async def list_files(self, path="/", page=0):
        self._require_login()
        if path == "/" and page == 0 and self._initial_root is not None:
            rows, self._initial_root = self._initial_root, None
            return rows
        return await self._fetch_files(path, page)

    async def acquire_share(self, text, passcode=""):
        self._require_login()
        share_id, passcode = parse_share_link(text, passcode)
        body = await self._request("POST", SHARE + "/share/sharepage/token", "share_token", params=PARAMS,
                                  data={"pwd_id": share_id, "passcode": passcode, "support_visit_limit_private_share": True})
        data = self._success(body, "分享可能已失效或提取码不正确，请检查链接")
        try:
            token = _opaque(data["stoken"])
        except (KeyError, TypeError):
            raise QuarkApiError("quark_response", "没有收到有效的分享信息") from None
        return QuarkShare(share_id, token, self.session_id)

    async def list_share(self, share, parent_id="0", path="/", page=0):
        self._require_share(share)
        parent_id = _fid(parent_id)
        if (not valid_path(path) or type(page) is not int or page < 0
                or share.directories.get(path) != _fid(parent_id)):
            raise QuarkApiError("quark_path", "请从已打开的分享目录逐层进入")
        params = dict(PARAMS, ver=2, pwd_id=share.id, stoken=share.token, pdir_fid=parent_id, force=0,
                      _page=page+1, _size=50, _fetch_total=1, _sort="file_type:asc,file_name:asc", fetch_share_full_path=0)
        body = await self._request("GET", SHARE + "/share/sharepage/detail", "share_directory", params=params)
        data = self._success(body, "分享目录暂时无法读取，请检查链接是否有效")
        return self._decode_files(data, path, parent_id, share.directories, share.files, share)

    async def get_download(self, file: CloudFile, *, share: QuarkShare | None = None):
        self._require_login()
        if file.provider != "quark" or file.is_dir:
            raise QuarkApiError("quark_download_file", "当前仅支持下载单个夸克文件")
        body_data = {"fids": [file.id]}
        if share is None:
            if self._files.get(file.id) != file:
                raise QuarkApiError("quark_download_file", "请先刷新文件所在目录")
        else:
            self._require_share(share)
            if share.files.get(file.id) != file or not share.file_tokens.get(file.id):
                raise QuarkApiError("quark_download_file", "分享文件凭证不完整，请重新打开分享")
            body_data.update({"pwd_id": share.id, "stoken": share.token, "fids_token": [share.file_tokens[file.id]]})
        # GeZi uses entry=ft for share direct links. Own-file requests retain the
        # basic PC entry. A client profile is used for both link and byte transfer;
        # never cycle through unrelated platform profiles after a service refusal.
        params = dict(PARAMS)
        route = "desktop"
        if share is not None:
            params["entry"] = "ft"
            route = "desktop_share"
        body = await self._request("POST", DRIVE + "/file/download", "download_link",
            params=params, data=body_data, user_agent=DOWNLOAD_UA, route=route)
        data = self._success(body, "夸克未提供这个文件的下载地址", stage="download_link")
        try:
            if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
                raise ValueError()
            row = data[0]
            if _fid(row["fid"]) != file.id or row.get("file_name") != file.name:
                raise ValueError()
            size = row.get("size", file.size)
            if type(size) is not int or size != file.size:
                raise ValueError()
            url = checked_url(row["download_url"], download=True)
            # DirectDownload uses this same client; its CookieJar applies domain
            # scope to each request/redirect. Never flatten cookies into a ticket.
            headers = {"User-Agent": DOWNLOAD_UA, "Referer": PAN + "/", "Origin": PAN}
            md5 = row.get("md5") or None
            if md5 is not None and (not isinstance(md5, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", md5)):
                raise ValueError()
            return QuarkDownload(url, headers, size, file.name, file.id, md5)
        except (KeyError, TypeError, ValueError):
            raise QuarkApiError("quark_response", "下载地址与所选文件不一致，已停止下载") from None


    @staticmethod
    def validate_download_url(url):
        return checked_url(url, download=True)

    async def download_source(self, item):
        from app.direct_download import DownloadSource
        ticket = await self.get_download(item)
        # Quark web metadata MD5 is advisory: real downloads and other clients
        # observe mismatches. Keep it for version checks, do not claim a raw
        # content digest contract. See QUARK-PROTOCOL-RESEARCH.md.
        return DownloadSource(ticket.url, ticket.size, ticket.headers, ticket.md5,
                              md5_is_content=False)

    async def share_download_source(self, item, share):
        from app.direct_download import DownloadSource
        ticket = await self.get_download(item, share=share)
        # Quark web metadata MD5 is advisory: real downloads and other clients
        # observe mismatches. Keep it for version checks, do not claim a raw
        # content digest contract. See QUARK-PROTOCOL-RESEARCH.md.
        return DownloadSource(ticket.url, ticket.size, ticket.headers, ticket.md5,
                              md5_is_content=False)


class QuarkDirectory:
    root = "/"

    def __init__(self, api):
        self.api = api

    async def list_page(self, path, page=0):
        return await self.api.list_files(path, page)
