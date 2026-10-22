"""Quark login, account files, and read-only share browsing in the existing shell."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import time
from uuid import uuid4

import httpx
from app.http_pool import client as pooled_client
import flet as ft
import qrcode
from qrcode.image.svg import SvgPathFillImage

from app.baidu_panel import BaiduPanel
from app.direct_download import DownloadError
from app.parallel_download import ParallelDownload, DownloadGate, DEFAULT_CONNECTIONS
from app.live_download_panel import LiveDownloadMixin
from app.live_browser import LiveBrowserMixin
from app.remember_login import RememberLoginMixin
from app.session_vault import SessionStoreError
from app.providers.baidu_api import BaiduApiError
from app.providers.quark_web import QuarkApiError, QuarkWebApi


class QuarkPanelDirectory:
    """Bridge provider errors and a successful prefetched share page to BaiduPanel."""
    root = "/"

    def __init__(self, api, share=None, initial_root=None):
        self.api = api
        self.share = share
        self.initial_root = initial_root

    async def list_page(self, path, page=0):
        if path == "/" and page == 0 and self.initial_root is not None:
            result, self.initial_root = self.initial_root, None
            return result
        try:
            if self.share is None:
                return await self.api.list_files(path, page)
            parent_id = self.share.directories.get(path)
            if parent_id is None:
                raise QuarkApiError("quark_path", "请从已打开的分享目录逐层进入")
            return await self.api.list_share(self.share, parent_id=parent_id, path=path, page=page)
        except QuarkApiError as error:
            raise BaiduApiError(error.code, str(error)) from None


def render_qr_svg(payload):
    """Local rendering only; QR payloads never leave the device for image conversion."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(image_factory=SvgPathFillImage).to_string()


class QuarkPanel(RememberLoginMixin, LiveBrowserMixin, LiveDownloadMixin, BaiduPanel):
    def __init__(self, page, on_session_change=None, client=None, on_transfer_change=None, diagnostic=None, session_store=None):
        self.action_lock = asyncio.Lock()
        self.started = False
        self.share = None
        self._view_generation = 0
        self._share_tasks = set()
        self.on_transfer_change = on_transfer_change
        self.download_reader_is_view = True
        self.init_remember_login("quark", client, session_store)
        super().__init__(page, on_session_change, client)
        self.api = QuarkWebApi(self.client, diagnostic or self.record_diagnostic)
        # Login/API keep their existing transport. Only bulk file bytes use
        # a direct pool; no system proxy setting is modified. CookieJar is shared
        # by reference, preserving scoped updates and immediate logout clearing.
        self.download_client = client if client is not None else pooled_client(
            trust_env=False, cookies=self.client.cookies.jar,
            limits=httpx.Limits(max_connections=DEFAULT_CONNECTIONS, max_keepalive_connections=DEFAULT_CONNECTIONS))
        self.download_gate = DownloadGate(DEFAULT_CONNECTIONS)
        self.download_diagnostic = diagnostic or self.record_diagnostic
        self.init_live_downloads()
        self.download_available = True
        self.directory_title = "我的夸克网盘"
        self.directory_success = "夸克目录读取成功"
        self.directory_failure = "夸克目录读取失败："
        self.directory_error_hint = "请点击“重新读取”重试；如果会话过期，请重新扫码登录。"
        self.begin_button.content = "获取二维码"
        self.cancel_button.content = "退出登录"
        self.status.value = "点击“获取二维码”，用夸克 App 扫码登录。"
        self.form.visible = False

    @staticmethod
    def record_diagnostic(event):
        # Enforce both field names and value shapes, never save a URL/body/token/name.
        safe = {}
        for key in ("stage", "route", "outcome"):
            value = event.get(key)
            if isinstance(value, str) and re.fullmatch(r"[a-z_]{1,40}", value):
                safe[key] = value
        value = event.get("oss_code")
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_]{1,64}", value):
            safe["oss_code"] = value
        for key in ("http_status", "response_bytes", "service_code", "entry_count", "part_number"):
            value = event.get(key)
            if type(value) is int and -10**9 <= value <= 10**9:
                safe[key] = value
        for key in ("transferred_bytes", "peak_connections", "active_connections",
                    "retry_count", "elapsed_ms", "bytes_per_second",
                    "average_bytes_per_second", "active_elapsed_ms"):
            value = event.get(key)
            if type(value) is int and 0 <= value <= 2**63-1:
                safe[key] = value
        for key in ("parsed", "session_cookie"):
            value = event.get(key)
            if type(value) is bool:
                safe[key] = value
        safe["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        path = __import__('app.runtime_paths',fromlist=['work_dir']).work_dir() / 'quark-diagnostics.jsonl'
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(safe) + "\n")
        except OSError:
            # A diagnostics write failure must not prevent a login.
            pass

    def build(self):
        self.retry_button = ft.TextButton(content="重试连接", on_click=self.retry_connection, visible=False)
        self.share_link = ft.TextField(label="夸克分享链接", hint_text="粘贴分享链接或完整分享文字", expand=True,
                                       on_submit=self.open_share, disabled=True)
        self.share_password = ft.TextField(label="提取码（可选）", width=200, password=True,
                                           can_reveal_password=True, on_submit=self.open_share, disabled=True)
        self.share_open = ft.Button(content="打开分享", on_click=self.open_share, disabled=True)
        self.my_files_button = ft.TextButton(content="返回我的文件", on_click=self.show_my_files, disabled=True)
        self.share_form = ft.Column([ft.Row([self.share_link,self.share_password]),
            ft.Row([self.share_open,self.my_files_button],wrap=True)],visible=False,spacing=10)
        return self.build_live_browser("夸克网盘",extra=[self.share_form],header_actions=[
            ft.TextButton(content="打开分享",icon=ft.Icons.LINK_ROUNDED,on_click=self.toggle_share_form)])

    async def toggle_share_form(self,e=None):
        self.share_form.visible = not self.share_form.visible
        self.update()

    def _share_controls(self, connected, busy=False):
        self.share_link.disabled = self.share_password.disabled = not connected
        self.share_open.disabled = not connected or busy
        self.my_files_button.disabled = not connected

    async def begin(self, e=None):
        async with self.action_lock:
            if self.closed:
                return
            await self._clear_session()
            self.session_notice.value = ("登录成功后会加密保存，下次打开自动恢复。" if self.remember_login.value
                                         else "不记住登录；关闭窗口后需要重新扫码。")
            self.started = True
            self.begin_button.content = "刷新二维码"
            self.cancel_button.disabled = False
            self.begin_button.disabled = False
            self.status_message("正在获取夸克登录二维码…")
            self.task = asyncio.create_task(self.authorize_web())

    async def authorize_web(self):
        generation = self.generation
        confirmed = False
        try:
            async with asyncio.timeout(300):
                grant = await self.api.create_qr()
                if self.closed or generation != self.generation:
                    return
                self.qr.content = ft.Container(ft.Image(src=render_qr_svg(grant.url), width=250, height=250,
                    fit=ft.BoxFit.CONTAIN), padding=12, bgcolor="#FFFFFF", border_radius=12)
                self.qr.visible = True
                self.status_message("请用夸克 App 扫码，并在手机上确认登录")
                deadline = time.monotonic() + min(grant.expires_in, 300)
                while time.monotonic() < deadline:
                    await asyncio.sleep(2)
                    state = await self.api.poll(grant.token)
                    if self.closed or generation != self.generation:
                        return
                    if state.state == "confirmed":
                        confirmed = True
                        confirmation = state.confirmation
                        break
                    if state.state == "expired":
                        raise TimeoutError()
                    if state.state == "cancelled":
                        raise QuarkApiError("quark_cancelled", "已在手机上取消登录，可刷新二维码重试")
                    if state.state == "scanned":
                        self.status_message("已扫码，请在手机上确认登录")
                else:
                    raise TimeoutError()
            self.qr.visible = False
            self.qr.content = None
            self.status_message("手机已确认，正在连接你的夸克网盘…")
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
        except (QuarkApiError, ValueError) as error:
            if not self.closed and generation == self.generation:
                self.status_message(str(error), "#C05020")
        except Exception:
            if not self.closed and generation == self.generation:
                self.status_message("夸克登录未完成，请刷新二维码后重试", "#C05020")
        finally:
            if generation == self.generation:
                self.qr.visible = False
                self.qr.content = None
                self.begin_button.disabled = False
                self.retry_button.visible = self.api.session_candidate and self.reader is None
                self.update()

    async def show_connected_directory(self):
        self.save_remembered()
        self._view_generation += 1
        self.share = None
        self.reader = QuarkPanelDirectory(self.api)
        self.directory_title = "我的夸克网盘"
        self.directory_success = "登录成功，夸克目录读取成功"
        self.download_destination = self.new_download_destination("quark")
        self.qr.visible = False
        self.qr.content = None
        self.retry_button.visible = False
        self._share_controls(True)
        self.session_changed()
        generation = self.generation
        try:
            async with asyncio.timeout(30):
                await self.load_directory("/", 0)
        except TimeoutError:
            if not self.closed and generation == self.generation:
                self.status_message("已登录，目录读取超时，请点击“重新读取”重试", "#C05020")

    async def retry_connection(self, e=None):
        async with self.action_lock:
            if self.closed or (self.task and not self.task.done()):
                return
            self.retry_button.disabled = True
            self.status_message("正在使用本次扫码会话重新连接…")
            self.task = asyncio.create_task(self.resume_connection())

    async def resume_connection(self):
        generation = self.generation
        try:
            async with asyncio.timeout(60):
                if self._restoring_saved:
                    await self.api.restore_login()
                else:
                    await self.api.resume_login()
            if not self.closed and generation == self.generation:
                await self.show_connected_directory()
        except asyncio.CancelledError:
            raise
        except QuarkApiError as error:
            if not self.closed and generation == self.generation:
                self.status_message(str(error), "#C05020")
        except Exception:
            if not self.closed and generation == self.generation:
                self.status_message("夸克连接尚未完成，可稍后重试", "#C05020")
        finally:
            if generation == self.generation:
                self.retry_button.disabled = False
                self.retry_button.visible = self.api.session_candidate and self.reader is None
                self.update()

    async def open_share(self, e=None):
        if self.closed:
            return
        if not self.api.authenticated:
            self.status_message("请先扫码登录夸克网盘", "#C05020")
            return
        self._view_generation += 1
        self.list_generation += 1
        view_generation, generation = self._view_generation, self.generation
        current_task = asyncio.current_task()
        self._share_tasks.add(current_task)
        def current():
            return not self.closed and generation == self.generation and view_generation == self._view_generation
        self._share_controls(True, busy=True)
        self.set_directory_busy(True)
        self.status_message("正在读取分享…")
        try:
            share = await self.api.acquire_share(self.share_link.value or "", self.share_password.value or "")
            if not current():
                return
            rows = await self.api.list_share(share)
            if not current():
                return
            # Commit the new view only after a successful first page.
            self.share = share
            self.reader = QuarkPanelDirectory(self.api, share, initial_root=rows)
            self.directory_title = "夸克分享文件"
            self.directory_success = "分享读取成功，可选择文件下载"
            view_generation = self._view_generation + 1
            await self.load_directory("/", 0)
        except asyncio.CancelledError:
            raise
        except QuarkApiError as error:
            if current():
                self.status_message(str(error), "#C05020")
        except Exception:
            if current():
                self.status_message("分享暂时无法读取，请检查链接后重试", "#C05020")
        finally:
            self._share_tasks.discard(current_task)
            if current():
                self._share_controls(self.api.authenticated)
                self.parent_button.disabled = self.reader is None or self.path == self.reader.root
                self.prev_button.disabled = self.page_index == 0
                self.refresh_button.disabled = self.reader is None
                # Keep the last rendered next-page state; do not invent a page count.
                self.next_button.disabled = getattr(self, "_quark_next_disabled", True)
                self.update()

    async def load_directory(self, path, page_index):
        # Folder navigation also supersedes an outstanding share-open request.
        self._view_generation += 1
        self._share_controls(self.api.authenticated)
        reader, generation = self.reader, self.generation
        expected_request = self.list_generation + 1
        await super().load_directory(path, page_index)
        if (not self.closed and self.reader is reader and self.generation == generation
                and self.list_generation == expected_request):
            self._quark_next_disabled = self.next_button.disabled

    async def show_my_files(self, e=None):
        if self.closed or not self.api.authenticated:
            return
        self._view_generation += 1
        self.list_generation += 1
        self.share = None
        self.reader = QuarkPanelDirectory(self.api)
        self.directory_title = "我的夸克网盘"
        self.directory_success = "夸克目录读取成功"
        self._share_controls(True)
        await self.load_directory("/", 0)

    def make_download(self, item, destination, client, resolver, validate, progress, **kwargs):
        return ParallelDownload(item, destination, self.download_client, resolver,
                                validate, progress, connections=DEFAULT_CONNECTIONS, gate=self.download_gate,
                                diagnostic=self.download_diagnostic, **kwargs)

    async def shutdown(self, e=None):
        try:
            await super().shutdown(e)
        finally:
            if self.download_client is not self.client:
                await self.download_client.aclose()

    def get_download_resolver(self):
        api, share = self.api, self.share
        async def resolve(item):
            try:
                if share is None:
                    return await api.download_source(item)
                return await api.share_download_source(item, share)
            except QuarkApiError as error:
                # Provider messages are deliberately sanitized; preserve them in
                # the generic engine instead of exposing raw transport errors.
                raise DownloadError(error.code, str(error)) from None
        return resolve

    async def cancel(self, e=None):
        async with self.action_lock:
            await self._clear_session(e)

    async def _clear_session(self, e=None):
        if not self._closing:
            try:
                self.forget_remembered()
                self.session_notice.value = '已清除保存的登录信息。'
            except SessionStoreError as error:
                self.session_notice.value = str(error)
        self._restoring_saved = False
        self._view_generation += 1
        current_task = asyncio.current_task()
        pending = [task for task in self._share_tasks if task is not current_task and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._share_tasks.clear()
        await super().cancel(e)
        self.api.reset()
        self.started = False
        self.share = None
        self.share_link.value = self.share_password.value = ""
        self._share_controls(False)
        self.retry_button.visible = False
        self.retry_button.disabled = False
        self.form.visible = False
        self.update()
