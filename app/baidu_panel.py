"""Reusable native Baidu connection panel; never persists credentials."""
from __future__ import annotations
import asyncio
from pathlib import Path, PurePosixPath
from uuid import uuid4
from functools import partial
import flet as ft
import httpx
from app.http_pool import client as pooled_client
from app.baidu_download import download_file
from app.domain import human_size
from app.providers.baidu_api import BaiduApi, BaiduApiError
from app.baidu_auth import DeviceAuthorizer, AuthorizedDirectory, application_root


class BaiduPanel:
    def __init__(self, page, on_session_change=None, client=None):
        self.page = page
        self.on_session_change = on_session_change
        self.list_generation = 0
        self.download_available = True
        self.download_task = None
        self.download_destination = None
        self.client = client if client is not None else pooled_client()
        self.api = BaiduApi(self.client)
        self.task = None
        self.reader = None
        self.closed = False
        self.generation = 0
        self.path, self.page_index = "", 0
        self.app_name = ft.TextField(label="应用名称", value="云屿", helper="与控制台中的名称完全一致", text_size=14)
        self.app_key = ft.TextField(label="AppKey", helper="不是 AppId，也不是 SignKey", text_size=14, password=True, can_reveal_password=True)
        self.secret = ft.TextField(label="SecretKey", password=True, can_reveal_password=True, text_size=14,
                                   helper="只在本窗口输入，不发送给聊天，不写入项目文件")
        self.begin_button = ft.Button(content="获取百度授权二维码", on_click=self.begin)
        self.cancel_button = ft.TextButton(content="取消并清除本次会话", on_click=self.cancel, disabled=True)
        self.status = ft.Text("等待填写应用信息", size=15, color=ft.Colors.ON_SURFACE)
        self.qr = ft.Container(visible=False)
        self.results = ft.Column(spacing=10, visible=False)
        self.path_text = ft.Text("", size=13, selectable=True)
        self.page_text = ft.Text("", size=12)
        self.files = ft.Column(spacing=3)
        self.parent_button = ft.TextButton(content="上级目录", on_click=self.parent)
        self.prev_button = ft.TextButton(content="上一页", on_click=partial(self.paginate, -1))
        self.next_button = ft.TextButton(content="下一页", on_click=partial(self.paginate, 1))
        self.refresh_button = ft.TextButton(content="重新读取", on_click=self.refresh)
        self.download_status = ft.Text("", size=12, selectable=True)
        self.download_cancel = ft.TextButton(content="取消下载", visible=False, on_click=self.cancel_download)
        self.form = ft.Column([ft.Text("开发者联调设置 · 正式用户无需填写应用密钥", size=12), self.app_name, self.app_key, self.secret], spacing=12)
        self.view = self.build()

    def build(self):
        return ft.Column([
            ft.Text("百度网盘", size=27, weight=ft.FontWeight.W_700),
            ft.Text("登录自己的账号，浏览已授权的文件。", size=13, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Container(ft.Text(
                "当前联调通道只读取 /apps/应用名称；授权目录选择将在发布版接入。"
                "\n支持目录浏览与单文件下载；关闭窗口或退出账号会清除本机会话。", size=12, color="#1E3A5F"),
                padding=14, bgcolor="#EFF6FF", border_radius=10),
            self.form,
            ft.Row([self.begin_button, self.cancel_button], wrap=True),
            self.status, self.qr, self.results,
        ], spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)

    def mount(self):
        self.page.add(self.view)

    def session_changed(self):
        if not self.closed and self.on_session_change:
            self.on_session_change()

    def update(self):
        if not self.closed:
            self.page.update()

    def status_message(self, message, color=ft.Colors.ON_SURFACE):
        self.status.value, self.status.color = message, color or ft.Colors.ON_SURFACE
        self.update()

    async def begin(self, e):
        if self.task and not self.task.done():
            return
        name = (self.app_name.value or "").strip()
        key, secret = (self.app_key.value or "").strip(), (self.secret.value or "").strip()
        try:
            application_root(name)
            if not key or not secret:
                raise ValueError("请先从控制台复制 AppKey 和 SecretKey")
            if any(len(v) > 256 or any(c.isspace() for c in v) for v in (key, secret)):
                raise ValueError("应用凭证包含空白或长度异常，请检查复制内容")
        except ValueError as exc:
            self.status_message(str(exc), "#B91C1C")
            return
        self.generation += 1
        self.reader = None
        self.results.visible = self.qr.visible = False
        self.begin_button.disabled = True
        self.cancel_button.disabled = False
        self.app_name.disabled = self.app_key.disabled = True
        self.secret.value = ""
        self.secret.disabled = True
        self.status_message("正在向百度申请设备码")
        self.task = asyncio.create_task(self.authorize(name, key, secret))

    async def authorize(self, name, key, secret):
        async def show_grant(grant):
            self.qr.content = ft.Column([
                ft.Text("请用百度网盘 App、百度 App 或微信扫码，在百度页面确认授权", size=13),
                ft.Container(ft.Image(src=grant.qrcode_url, width=230, height=230, fit=ft.BoxFit.CONTAIN,
                    error_content=ft.Text("二维码图片未能加载，可使用下方用户码完成授权", color="#B91C1C")),
                    padding=12, bgcolor="#FFFFFF", border_radius=8),
                ft.Text(f"二维码有效期 {grant.expires_in} 秒，等待确认期间请保持窗口打开", size=12),
                ft.Text("备用方式：在浏览器打开 https://openapi.baidu.com/device", size=12, selectable=True),
                ft.Text("输入用户码：" + grant.user_code, size=14, selectable=True),
                ft.Text("授权范围为 basic 和 netdisk，具体权限以百度授权页面为准。", size=12, color="#64748B")
            ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
            self.form.visible = False
            self.qr.visible = True
            self.status_message("等待扫码并在手机上确认授权")

        async def waiting(message):
            self.status_message(message)

        try:
            tokens = await DeviceAuthorizer(self.api).authorize(key, secret, show_grant, waiting)
            self.qr.visible = False
            self.qr.content = None
            self.reader = AuthorizedDirectory(self.api, tokens, name)
            self.download_destination = Path(__file__).resolve().parents[1] / "downloads" / "baidu" / uuid4().hex
            self.session_changed()
            self.form.visible = False
            self.status_message("扫码授权成功，正在读取应用目录", "#15803D")
            await self.load_directory(self.reader.root, 0)
        except asyncio.CancelledError:
            raise
        except BaiduApiError as exc:
            self.status_message(str(exc), "#B91C1C")
        except Exception:
            # Sensitive HTTP requests and payloads must not reach logs/tracebacks.
            self.status_message("授权过程出现异常，请取消本次会话后重试", "#B91C1C")
        finally:
            self.qr.visible = False
            self.qr.content = None
            self.form.visible = self.reader is None
            self.begin_button.disabled = self.reader is not None
            self.app_name.disabled = self.app_key.disabled = self.secret.disabled = self.reader is not None
            self.update()

    def download_controls(self):
        return [self.download_status, self.download_cancel]

    def set_directory_busy(self, value):
        for control in (self.parent_button, self.prev_button, self.next_button, self.refresh_button):
            control.disabled = value

    def directory_controls(self):
        return [
            ft.Text(getattr(self, "directory_title", "真实百度网盘 · 授权目录"), size=18, weight=ft.FontWeight.W_600),
            self.path_text,
            ft.Row([self.parent_button, self.refresh_button, self.prev_button, self.page_text, self.next_button], wrap=True),
            self.files,
            *self.download_controls(),
        ]

    def render_directory_items(self, rows):
        self.files.controls = [
            ft.ListTile(
                leading=ft.Icon(ft.Icons.FOLDER_OUTLINED if item.is_dir else ft.Icons.INSERT_DRIVE_FILE_OUTLINED),
                title=ft.Text(item.name, size=13),
                subtitle=ft.Text("文件夹" if item.is_dir else human_size(item.size), size=11),
                trailing=None if item.is_dir or not self.download_available else ft.IconButton(ft.Icons.DOWNLOAD_ROUNDED,
                    tooltip="下载到本机（保存在项目 downloads 内）", on_click=partial(self.start_download, item)),
                on_click=partial(self.open_folder, item.path) if item.is_dir else None,
            ) for item in rows
        ] or [ft.Text("此页没有文件。目录读取已成功。", size=13)]

    async def load_directory(self, path, page_index):
        reader, generation = self.reader, self.generation
        if reader is None:
            return
        self.list_generation += 1
        request_id = self.list_generation
        def current():
            return (not self.closed and generation == self.generation and request_id == self.list_generation and reader is self.reader)
        self._page_has_more = False
        self.set_directory_busy(True)
        self.path, self.page_index = path, page_index
        self.path_text.value = path
        self.files.controls.clear()
        self.results.controls = self.directory_controls()
        self.results.visible = True
        self.update()
        try:
            rows = await reader.list_page(path, page_index)
            if not current():
                return
            self.render_directory_items(rows)
            self.page_text.value = f"第 {page_index + 1} 页 · 本页 {len(rows)} 项"
            self.status_message(getattr(self, "directory_success", "扫码授权成功，真实目录读取成功"), "#15803D")
            self._page_has_more = len(rows) >= 50
            self.next_button.disabled = not self._page_has_more
        except (BaiduApiError, ValueError) as exc:
            if not current():
                return
            self.files.controls = [ft.Text(
                getattr(self, "directory_error_hint",
                    "如果提示目录不存在，请在百度网盘中检查“我的应用数据”下是否已有对应应用目录。"
                    "\n授权成功并不代表全盘访问已开放，也不代表所有文件接口都有权限。"), size=12)]
            self.page_text.value = "目录尚未读取成功"
            self.status_message(getattr(self, "directory_failure", "扫码授权成功，但目录读取失败：") + str(exc), "#B45309")
            self.next_button.disabled = True
        except Exception:
            if not current():
                return
            self.status_message("扫码授权成功，但目录响应无法处理，请稍后重试", "#B45309")
            self.next_button.disabled = True
        finally:
            if current():
                self.set_directory_busy(False)
                self.next_button.disabled = not hasattr(self, "_page_has_more") or not self._page_has_more
                self.parent_button.disabled = path == reader.root
                self.prev_button.disabled = page_index == 0
                self.refresh_button.disabled = False
                self.update()

    async def open_folder(self, path, e=None):
        await self.load_directory(path, 0)

    async def parent(self, e):
        if self.reader and self.path != self.reader.root:
            await self.load_directory(str(PurePosixPath(self.path).parent), 0)

    async def paginate(self, delta, e=None):
        await self.load_directory(self.path, max(0, self.page_index + delta))

    async def refresh(self, e):
        await self.load_directory(self.path, self.page_index)

    async def start_download(self, item, e=None):
        if not self.reader:
            return
        if self.download_task and not self.download_task.done():
            self.download_status.value = "已有一个文件正在下载，请等待完成或取消。"
            self.update()
            return
        self.download_task = asyncio.create_task(self.run_download(item))

    async def run_download(self, item):
        reader, generation, destination = self.reader, self.generation, self.download_destination
        if reader is None or destination is None:
            return
        self.download_cancel.visible = True
        self.download_status.value = "正在获取文件下载地址…"
        self.update()
        async def progress(done, total):
            if generation == self.generation and not self.closed:
                self.download_status.value = f"正在下载：{human_size(done)} / {human_size(total)}"
                self.update()
        try:
            saved = await download_file(reader, item, destination, progress)
            if generation == self.generation and not self.closed:
                self.download_status.value = "下载完成（字节数核对通过）：" + str(saved)
        except asyncio.CancelledError:
            if generation == self.generation and not self.closed:
                self.download_status.value = "下载已取消。"
            raise
        except (BaiduApiError, ValueError) as exc:
            if generation == self.generation and not self.closed:
                self.download_status.value = str(exc)
        except Exception:
            if generation == self.generation and not self.closed:
                self.download_status.value = "下载未完成，请重新尝试。"
        finally:
            self.download_cancel.visible = False
            self.update()

    async def cancel_download(self, e=None):
        if self.download_task and not self.download_task.done():
            self.download_task.cancel()
            await asyncio.gather(self.download_task, return_exceptions=True)
        self.download_task = None

    async def cancel(self, e=None):
        self.generation += 1
        self.list_generation += 1
        await self.cancel_download()
        self.download_destination = None
        self.download_status.value = ""
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = None
        self.reader = None
        self.path, self.page_index = "", 0
        self.path_text.value = self.page_text.value = ""
        self.client.cookies.clear()
        self.secret.value = self.app_key.value = ""
        self.form.visible = True
        self.app_name.disabled = self.app_key.disabled = self.secret.disabled = False
        self.begin_button.disabled = False
        self.cancel_button.disabled = True
        self.qr.visible = self.results.visible = False
        self.qr.content = None
        self.files.controls.clear()
        self.status_message("已退出账号，本机登录凭证和目录内容已清除。")
        self.session_changed()

    async def shutdown(self, e=None):
        if self.closed:
            return
        self.closed = True
        await self.cancel()
        await self.client.aclose()
