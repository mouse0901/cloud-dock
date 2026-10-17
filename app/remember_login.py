"""Separate closing the app from deliberately signing out of an account."""
import asyncio
import flet as ft
from app.session_vault import SessionVault, SessionStoreError


class RememberLoginMixin:
    def init_remember_login(self, provider, client, store):
        self.session_provider = provider
        # Injected HTTP clients are test/embedding contexts: no automatic access
        # to a real user's saved account unless a store is explicitly supplied.
        self.session_store = store if store is not None else (SessionVault() if client is None else None)
        self._closing = False
        self._restoring_saved = False
        self.remember_login = ft.Checkbox(label='记住登录（当前 Windows 账号）',value=True,
                                          on_change=self.remember_changed)
        self.session_notice = ft.Text('登录信息加密保存在本机；关闭窗口保留，退出账号时清除。',size=11,
                                     color=ft.Colors.ON_SURFACE_VARIANT)

    def save_remembered(self):
        if not self.api.authenticated:
            return
        self._restoring_saved = False
        if self.session_store is None or not self.remember_login.value:
            return
        try:
            self.session_store.save(self.session_provider,self.client.cookies.jar)
            self.session_notice.value = '已记住登录；下次打开会自动检查并恢复。退出账号可清除。'
        except SessionStoreError as error:
            self.session_notice.value = '本次仍可使用，但未保存登录：' + str(error)

    def forget_remembered(self, *, replace=True):
        if self.session_store is None:
            return
        self.session_store.forget(self.session_provider,remember=bool(self.remember_login.value),replace=replace)

    async def remember_changed(self, e=None):
        try:
            if not self.remember_login.value:
                self.forget_remembered()
                self.session_notice.value = '不记住登录；已清除本机保存的登录信息。'
            elif self.api.authenticated:
                self.save_remembered()
            elif self.session_store is not None:
                self.forget_remembered()
                self.session_notice.value = '登录成功后将加密保存，下次打开自动恢复。'
        except SessionStoreError as error:
            self.remember_login.value = self.session_store.preferences.get(self.session_provider,True)
            self.session_notice.value = "保存设置未能更改：" + str(error)
        self.update()

    async def startup(self, e=None, *, saved_only=False):
        scan = False
        async with self.action_lock:
            if self.closed or self.started:
                return
            jar = None
            if self.session_store is not None:
                try:
                    jar = self.session_store.load(self.session_provider)
                    self.remember_login.value = self.session_store.preferences.get(self.session_provider,True)
                except SessionStoreError as error:
                    self.session_notice.value = str(error)
            if not jar or not self.remember_login.value:
                scan = True
            else:
                for cookie in jar:
                    self.client.cookies.jar.set_cookie(cookie)
                self.started = True
                self._restoring_saved = True
                self.begin_button.content = '重新扫码'
                self.cancel_button.disabled = False
                self.status_message('正在检查已保存的登录…')
                self.task = asyncio.create_task(self._restore_saved_login())
        if scan and not saved_only:
            await self.begin()

    async def _restore_saved_login(self):
        generation = self.generation
        try:
            async with asyncio.timeout(60):
                await self.api.restore_login()
            if not self.closed and generation == self.generation:
                await self.show_connected_directory()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not self.closed and generation == self.generation:
                expired = getattr(error,'code',None) in ('web_session','quark_session')
                if expired:
                    try:
                        self.forget_remembered(replace=False)
                    except SessionStoreError as store_error:
                        self.session_notice.value = str(store_error)
                    self.api.reset()
                    self._restoring_saved = False
                    self.status_message('保存的登录已失效，请点击“重新扫码”登录。','#C05020')
                else:
                    self.status_message('暂时未能验证已保存的登录，可点“重试连接”，无需重新扫码。','#C05020')
        finally:
            if not self.closed and generation == self.generation:
                self.retry_button.visible = self.api.session_candidate and self.reader is None
                self.begin_button.disabled = False
                self.update()

    async def shutdown(self, e=None):
        if self.closed:
            return
        self.save_remembered()
        self._closing = True
        await super().shutdown(e)
