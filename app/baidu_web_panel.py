"""Direct scan experiment; users never enter developer application credentials."""
from __future__ import annotations
import asyncio
import time
import json
from pathlib import Path
from uuid import uuid4
import flet as ft
from app.baidu_panel import BaiduPanel
from app.live_download_panel import LiveDownloadMixin
from app.live_browser import LiveBrowserMixin
from app.remember_login import RememberLoginMixin
from app.session_vault import SessionStoreError
from app.providers.baidu_web import BaiduWebApi, WebDirectory
from app.providers.baidu_api import BaiduApiError


class BaiduWebPanel(RememberLoginMixin, LiveBrowserMixin, LiveDownloadMixin, BaiduPanel):
    def __init__(self,page,on_session_change=None,client=None,diagnostic=None,on_transfer_change=None,session_store=None):
        self.on_transfer_change = on_transfer_change
        self.started = False
        self.action_lock = asyncio.Lock()
        self.init_remember_login("baidu", client, session_store)
        super().__init__(page,on_session_change,client)
        self.api = BaiduWebApi(self.client, diagnostic)
        from app.parallel_download import DownloadGate
        self.download_connections = 1
        self._range_disabled_generation = None
        self.download_gate = DownloadGate(4)
        self.download_available = True
        self.init_live_downloads()
        self.directory_title = "我的百度网盘"
        self.directory_success = "登录成功，网盘目录读取成功"
        self.directory_failure = "登录成功，但目录读取失败："
        self.directory_error_hint = "扫码登录已完成，目录接口尚需实测；可以点“重新读取”重试。"
        self.begin_button.content = "获取二维码"
        self.cancel_button.content = "退出登录"
        self.status.value = "使用百度网盘 App 扫码，连接自己的账号。"

    def make_download(self, item, destination, client, resolver, validate, progress, **kwargs):
        from app.baidu_adaptive_download import BaiduAdaptiveDownload
        policy=getattr(self,'download_policy',None)
        connections=policy() if policy else self.download_connections
        generation=self.generation
        return BaiduAdaptiveDownload(item, destination, client, resolver, validate, progress,
            connections=connections, gate=self.download_gate,
            allow_parallel=lambda:self._range_disabled_generation!=generation,
            on_fallback=lambda:setattr(self,'_range_disabled_generation',generation), **kwargs)

    @staticmethod
    def record_diagnostic(event):
        # Fixed schema: never include response bodies, URLs, account names or credentials.
        safe = {key: event[key] for key in
                ("stage", "http_status", "response_bytes", "parsed", "service_code", "format",
                 "session_cookie", "outcome", "entry_count", "has_fragment")
                if key in event}
        safe["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        path = __import__('app.runtime_paths',fromlist=['work_dir']).work_dir() / 'web-login-diagnostics.jsonl'
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(safe) + "\n")

    def build(self):
        self.retry_button = ft.TextButton(content="重试连接", on_click=self.retry_connection, visible=False)
        self.account_text = ft.Text("", size=12, selectable=True)
        return self.build_live_browser("百度网盘",account_extra=[self.account_text])

    async def begin(self,e=None):
        async with self.action_lock:
            if self.closed:
                return
            await self._clear_session()
            self.session_notice.value = ("登录成功后会加密保存，下次打开自动恢复。" if self.remember_login.value
                                         else "不记住登录；关闭窗口后需要重新扫码。")
            self.started = True
            self.cancel_button.disabled = False
            self.begin_button.disabled = False
            self.status_message("正在从百度获取登录二维码…")
            self.task = asyncio.create_task(self.authorize_web())

    async def authorize_web(self):
        generation = self.generation
        confirmed = False
        try:
            # The QR deadline applies only while waiting for phone confirmation.
            async with asyncio.timeout(300):
                grant = await self.api.create_qr()
                if self.closed or generation != self.generation:
                    return
                self.qr.content = ft.Container(ft.Image(src=grant.image, width=240, height=240,
                    fit=ft.BoxFit.CONTAIN), padding=18, bgcolor="#FFFFFF", border_radius=12)
                self.qr.visible = True
                self.status_message("请用百度网盘 App 扫码，并在手机上确认登录")
                while True:
                    await asyncio.sleep(2)
                    state = await self.api.poll(grant.sign)
                    if self.closed or generation != self.generation:
                        return
                    if state.state == "scanned":
                        self.status_message("已扫码，请在手机上确认登录")
                    elif state.state == "cancelled":
                        raise BaiduApiError("qr_cancelled", "已在手机上取消登录，可刷新二维码重试")
                    elif state.state == "confirmed":
                        confirmed = True
                        confirmation = state.confirmation
                        break
            self.qr.visible = False
            self.qr.content = None
            self.status_message("手机已确认，正在连接你的百度网盘…")
            async with asyncio.timeout(60):
                await self.api.finish_login(confirmation)
            if not self.closed and generation == self.generation:
                await self.show_connected_directory()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            if not self.closed and generation == self.generation:
                self.status_message("手机已确认，但连接超时，请刷新二维码重试" if confirmed
                                    else "二维码已过期，点击“刷新二维码”重新获取", "#C05020")
        except (BaiduApiError, ValueError) as exc:
            if not self.closed and generation == self.generation:
                self.status_message(str(exc), "#C05020")
        except Exception:
            if not self.closed and generation == self.generation:
                self.status_message("登录未完成，请刷新二维码后重试", "#C05020")
        finally:
            if generation == self.generation:
                self.qr.visible = False
                self.qr.content = None
                self.begin_button.disabled = False
                self.retry_button.visible = self.api.session_candidate and self.reader is None
                self.update()

    async def show_connected_directory(self):
        self.save_remembered()
        generation = self.generation
        self.reader = WebDirectory(self.api)
        self.download_destination = self.new_download_destination("baidu")
        self.qr.visible = False
        self.qr.content = None
        self.retry_button.visible = False
        self.session_changed()
        try:
            async with asyncio.timeout(30):
                await self.load_directory("/", 0)
        except TimeoutError:
            if not self.closed and generation == self.generation:
                self.status_message("已登录，目录读取超时，请点击“重新读取”重试", "#C05020")
        if self.closed or generation != self.generation:
            return
        # Capacity/membership are refreshed separately by the desktop service.
        # Do not delay login or expose subscription information in the UI.

    async def retry_connection(self,e=None):
        async with self.action_lock:
            if self.closed or (self.task and not self.task.done()):
                return
            self.retry_button.disabled=True
            self.status_message("正在使用本次扫码会话重新连接…")
            self.task=asyncio.create_task(self.resume_connection())

    async def resume_connection(self):
        generation=self.generation
        try:
            async with asyncio.timeout(60):
                if self._restoring_saved:
                    await self.api.restore_login()
                else:
                    await self.api.resume_login()
            if not self.closed and generation==self.generation:
                await self.show_connected_directory()
        except asyncio.CancelledError:
            raise
        except BaiduApiError as exc:
            if not self.closed and generation==self.generation:
                self.status_message(str(exc),"#C05020")
        except Exception:
            if not self.closed and generation==self.generation:
                self.status_message("连接暂未完成，可稍后重试","#C05020")
        finally:
            if generation==self.generation:
                self.retry_button.disabled=False
                self.retry_button.visible=self.api.session_candidate and self.reader is None
                self.update()

    async def cancel(self,e=None):
        async with self.action_lock:
            await self._clear_session(e)

    async def _clear_session(self,e=None):
        if not self._closing:
            try:
                self.forget_remembered()
                self.session_notice.value = '已清除保存的登录信息。'
            except SessionStoreError as error:
                self.session_notice.value = str(error)
        self._restoring_saved = False
        # Clear before the base callback paints or rebuilds the account view.
        self.account_text.value = ""
        self.retry_button.visible = False
        await super().cancel(e)
        if isinstance(self.api,BaiduWebApi): self.api.reset()
        self.started=False
        self.account_text.value = ""
        self.retry_button.visible=False
        self.retry_button.disabled=False
        self.update()
