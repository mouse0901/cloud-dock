"""Baidu web download requests bound to the user's already-verified session.

Protocol references and live verification limits are in
docs/BAIDU-DOWNLOAD-RESEARCH.md. No external parsing service is used.
"""
from __future__ import annotations
import base64
import json
import re
from app.baidu_download import download_url
from app.direct_download import DownloadSource, DownloadError
from app.providers.baidu_api import BaiduApiError


def web_signature(key, value):
    """Fixed RC4 transform used by the web protocol; never execute server JS."""
    if (not isinstance(key, str) or not isinstance(value, str)
            or not 1 <= len(key) <= 4096 or not 1 <= len(value) <= 4096
            or not key.isascii() or not value.isascii()):
        raise BaiduApiError("download_signature", "百度下载参数格式发生变化，请稍后重试")
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + ord(key[i % len(key)])) % 256
        state[i], state[j] = state[j], state[i]
    i = j = 0
    output = bytearray()
    for char in value:
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        output.append(ord(char) ^ state[(state[i] + state[j]) % 256])
    return base64.b64encode(output).decode("ascii")


def checked_download_url(value):
    # Upgrade only a provider-issued URL to TLS, never allow an HTTP transfer.
    if isinstance(value, str) and value.startswith("http://"):
        value = "https://" + value[7:]
    return download_url(value)


class BaiduWebDownloads:
    @staticmethod
    def validate_download_url(value):
        return download_url(value)

    def _web_params(self):
        params = {"app_id": 250528, "web": 1, "clienttype": 0, "channel": "chunlei"}
        if self._bdstoken:
            params["bdstoken"] = self._bdstoken
        return params

    def _check_service(self, body, stage):
        if body.get("errno") not in (0, "0"):
            code = body.get("errno")
            safe_code = str(code) if re.fullmatch(r"-?\d{1,10}", str(code)) else "unknown"
            if safe_code == "-6":
                message = "百度登录会话已失效，请重新扫码"
            elif safe_code == "-9":
                message = "网盘文件已不存在，请刷新目录"
            else:
                message = "百度暂未允许这次下载请求（错误码 " + safe_code + "）"
            raise BaiduApiError(stage, message)

    async def account_label(self):
        if not self.authenticated:
            return "未登录"
        params = self._web_params()
        params["fields"] = json.dumps(["username", "is_vip", "is_svip", "is_evip"])
        response = await self.request("https://pan.baidu.com/api/gettemplatevariable", params)
        body = self.parse_response(response, "account")
        self._check_service(body, "account")
        result = body.get("result")
        if not isinstance(result, dict):
            raise BaiduApiError("account", "账号信息暂时无法读取")
        if str(result.get("is_svip")) == "1" or str(result.get("is_evip")) == "1":
            level = "SVIP"
        elif str(result.get("is_vip")) == "1":
            level = "VIP"
        elif str(result.get("is_vip")) == "0" and str(result.get("is_svip")) == "0":
            level = "普通账号"
        else:
            level = "会员状态待确认"
        name = result.get("username")
        name = name[:48] if isinstance(name, str) else ""
        return (name + " · " if name else "") + level

    def new_download_resolver(self):
        # One version pin per queued transfer: old partials stay protected while
        # a fresh task can download a file that changed after a directory refresh.
        versions = {}
        async def resolve(item):
            return await self.download_source(item, versions=versions)
        return resolve

    async def download_source(self, item, *, versions=None):
        try:
            return await self._download_source(item, {} if versions is None else versions)
        except BaiduApiError as exc:
            raise DownloadError(exc.code, str(exc)) from None

    async def _download_source(self, item, versions):
        if not self.authenticated:
            raise BaiduApiError("web_session", "请先扫码登录百度网盘")
        if (item.provider != "baidu" or item.is_dir
                or not re.fullmatch(r"\d{1,25}", item.id)
                or not item.path.startswith("/") or "\\" in item.path
                or any(ord(c) < 32 for c in item.path)
                or any(p in ("", ".", "..") for p in item.path[1:].split("/"))):
            raise BaiduApiError("download_item", "请选择当前目录中的单个文件")
        params = self._web_params()
        params.update({"target": json.dumps([item.path], ensure_ascii=False),
                       "dlink": 1, "blocks": 0})
        response = await self.request("https://pan.baidu.com/api/filemetas", params)
        body = self.parse_response(response, "metadata")
        self._check_service(body, "download_metadata")
        rows = body.get("info")
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise BaiduApiError("download_metadata", "百度返回的文件信息不完整")
        metadata = rows[0]
        if metadata.get("errno", 0) not in (0, "0"):
            self._check_service(metadata, "download_metadata")
        try:
            if (str(metadata["fs_id"]) != item.id or metadata["path"] != item.path
                    or str(metadata["isdir"]) != "0" or type(metadata["size"]) is not int
                    or metadata["size"] != item.size or item.size < 0):
                raise ValueError()
            version = (metadata["size"], metadata.get("server_mtime"), metadata.get("md5"))
            previous = versions.get(item.id)
            if previous is not None and previous != version:
                raise BaiduApiError("download_changed", "网盘文件已变化，请刷新目录后重新下载")
            versions[item.id] = version
        except (KeyError, TypeError, ValueError):
            raise BaiduApiError("download_changed", "网盘文件信息与列表不符，请刷新目录") from None
        link = metadata.get("dlink")
        if not isinstance(link, str) or not link:
            link = await self._signed_download_link(item.id)
        link = checked_download_url(link)
        self.emit({"stage": "download_source", "outcome": "ready"})
        # Baidu's returned md5 may be an opaque/transformed identity. Retain it
        # above for change detection; do not claim it is a raw content checksum.
        return DownloadSource(link, item.size, {
            "User-Agent": UA_DOWNLOAD, "Referer": "https://pan.baidu.com/"})

    async def _signed_download_link(self, file_id):
        params = self._web_params()
        params["fields"] = json.dumps(["sign1", "sign3", "timestamp", "bdstoken"])
        response = await self.request("https://pan.baidu.com/api/gettemplatevariable", params)
        body = self.parse_response(response, "download_parameters")
        self._check_service(body, "download_parameters")
        result = body.get("result")
        if not isinstance(result, dict):
            raise BaiduApiError("download_parameters", "百度没有返回下载参数")
        stamp = result.get("timestamp")
        if not re.fullmatch(r"\d{1,16}", str(stamp)):
            raise BaiduApiError("download_parameters", "百度下载时间参数无效")
        sign = web_signature(result.get("sign3"), result.get("sign1"))
        token = result.get("bdstoken")
        if isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", token):
            self._bdstoken = token
        params = self._web_params()
        params.update({"type": "dlink", "fidlist": json.dumps([int(file_id)]),
                       "sign": sign, "timestamp": str(stamp)})
        response = await self.request("https://pan.baidu.com/api/download", params)
        body = self.parse_response(response, "download_link")
        self._check_service(body, "download_link")
        rows = body.get("dlink")
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise BaiduApiError("download_link", "百度尚未返回可用下载地址")
        row = rows[0]
        if str(row.get("fs_id")) != file_id or not isinstance(row.get("dlink"), str):
            raise BaiduApiError("download_link", "下载地址与所选文件不匹配")
        return row["dlink"]


# PCS download endpoints require their download client identifier; the browser
# UA used for login/listing is rejected for some existing files (31326/119).
UA_DOWNLOAD = "pan.baidu.com"
