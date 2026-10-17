from __future__ import annotations
import asyncio
import re
import time
from pathlib import PurePosixPath
from app.providers.baidu_api import BaiduApiError


def application_root(name: str) -> str:
    name = name.strip()
    if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9 _-]{1,12}", name):
        raise ValueError("请填写与百度控制台完全相同的应用名称，1至12字")
    return "/apps/" + name


def within_application(path: str, root: str) -> bool:
    if not isinstance(path, str) or not path.startswith("/") or "\\" in path or "\x00" in path:
        return False
    if any(part in {".", ".."} for part in path.split("/")):
        return False
    return path == root or path.startswith(root + "/")


class DeviceAuthorizer:
    """Task cancellation stops polling. The clock/sleeper are injectable for tests."""
    def __init__(self, api, *, clock=time.monotonic, sleep=asyncio.sleep):
        self.api, self.clock, self.sleep = api, clock, sleep

    async def authorize(self, app_key, secret_key, on_grant, on_wait):
        started = self.clock()
        grant = await self.api.device_code(app_key)
        deadline = started + grant.expires_in
        await on_grant(grant)
        # Baidu docs require more than 5 seconds between polls.
        interval = max(6, grant.interval)
        while True:
            remaining = deadline - self.clock()
            if remaining <= interval:
                if remaining > 0:
                    await self.sleep(remaining)
                raise BaiduApiError("expired_token", "二维码已过期，请重新获取")
            await self.sleep(interval)
            if self.clock() >= deadline:
                raise BaiduApiError("expired_token", "二维码已过期，请重新获取")
            try:
                return await self.api.exchange_device(app_key, secret_key, grant.device_code)
            except BaiduApiError as exc:
                if exc.code == "authorization_pending":
                    await on_wait("等待扫码并在手机上确认授权")
                elif exc.code == "slow_down":
                    interval += 5
                    await on_wait("百度要求放慢轮询，正在等待授权")
                else:
                    raise


class AuthorizedDirectory:
    """The login probe only reads the newly created application's default directory."""
    def __init__(self, api, tokens, app_name):
        self.api, self.tokens = api, tokens
        self.root = application_root(app_name)

    async def list_page(self, path, page=0):
        if not within_application(path, self.root) or page < 0:
            raise ValueError("仅可读取当前应用的授权目录")
        files = await self.api.list_files(self.tokens.access_token, path, page * 50, 50)
        if any(not within_application(f.path, self.root) or str(PurePosixPath(f.path).parent) != path for f in files):
            raise BaiduApiError("invalid_response", "目录响应包含不属于当前目录的项目，已停止展示")
        return files
