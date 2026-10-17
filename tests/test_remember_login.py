"""Login persistence lifecycle with real vaults, mock HTTP, and no real accounts."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
from app.providers.baidu_web import WebQr,QrState
from app.providers.quark_web import QuarkQr,QuarkQrState
import httpx
from app.session_vault import SessionVault
from app.quark_panel import QuarkPanel
from app.baidu_web_panel import BaiduWebPanel
from test_quark_panel import PageStub

ROOT=Path(__file__).resolve().parents[1]

class RememberTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs')
        self.panels=[]

    async def asyncTearDown(self):
        for panel in self.panels: await panel.shutdown()
        self.tmp.cleanup()

    def seed(self,provider):
        vault=SessionVault(self.tmp.name)
        vault.load(provider)
        cookies=httpx.Cookies()
        cookies.set('BDUSS' if provider=='baidu' else '__pus','owned-fixture',
                    domain='.baidu.com' if provider=='baidu' else '.quark.cn',path='/')
        vault.save(provider,cookies.jar)

    def panel(self,provider,handler=None):
        calls=[]
        async def handle(request):
            calls.append(request)
            self.assertNotIn('getqrcode',request.url.path.lower())
            self.assertNotIn('getTokenForQrcodeLogin',request.url.path)
            if handler: return await handler(request)
            if provider=='baidu':
                self.assertIn('BDUSS=owned-fixture',request.headers.get('cookie',''))
                return httpx.Response(200,json={'errno':0,'list':[]})
            self.assertIn('__pus=owned-fixture',request.headers.get('cookie',''))
            return httpx.Response(200,json={'status':200,'code':0,'data':{'list':[]}})
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        cls=BaiduWebPanel if provider=='baidu' else QuarkPanel
        panel=cls(PageStub(),client=client,session_store=SessionVault(self.tmp.name),diagnostic=lambda _:None)
        if provider=='baidu': panel.api.account_label=AsyncMock(return_value='测试账号')
        self.panels.append(panel)
        return panel,calls

    async def test_close_reopen_restores_both_accounts_without_scanning(self):
        for provider in ('baidu','quark'):
            self.seed(provider)
            panel,calls=self.panel(provider)
            await panel.startup(); await panel.task
            self.assertTrue(panel.api.authenticated)
            self.assertIsNotNone(panel.reader)
            self.assertFalse(panel.qr.visible)
            self.assertEqual(len(calls),1)
            await panel.shutdown()
            self.assertEqual(len(panel.client.cookies),0)
            self.assertGreater(len(SessionVault(self.tmp.name).load(provider)),0)
            second,calls=self.panel(provider)
            await second.startup(); await second.task
            self.assertTrue(second.api.authenticated)
            self.assertEqual(len(calls),1)
            await second.cancel()
            self.assertEqual(len(SessionVault(self.tmp.name).load(provider)),0)
            await second.shutdown()
            self.assertEqual(len(SessionVault(self.tmp.name).load(provider)),0)

    async def test_transient_network_failure_keeps_saved_session_for_retry(self):
        for provider in ('baidu','quark'):
            self.seed(provider)
            offline=True
            async def handle(request):
                if offline: raise httpx.ConnectError('private network details')
                return httpx.Response(200,json={'errno':0,'list':[]} if provider=='baidu' else {'status':200,'code':0,'data':{'list':[]}})
            panel,calls=self.panel(provider,handle)
            await panel.startup(); await panel.task
            self.assertFalse(panel.api.authenticated)
            self.assertTrue(panel.retry_button.visible)
            self.assertGreater(len(SessionVault(self.tmp.name).load(provider)),0)
            self.assertNotIn('private',panel.status.value)
            offline=False
            await panel.retry_connection(); await panel.task
            self.assertTrue(panel.api.authenticated)
            self.assertFalse(panel.qr.visible)

    async def test_server_expiry_clears_saved_cookie_but_no_retry_loop(self):
        for provider in ('baidu','quark'):
            self.seed(provider)
            async def handle(request):
                if request.url.path=='/disk/main': return httpx.Response(200,text='')
                return httpx.Response(200,json={'errno':-6} if provider=='baidu' else {'status':401,'code':31001})
            panel,calls=self.panel(provider,handle)
            await panel.startup(); await panel.task
            self.assertFalse(panel.api.authenticated)
            self.assertIn('登录已失效',panel.status.value)
            self.assertEqual(len(SessionVault(self.tmp.name).load(provider)),0)
            self.assertLessEqual(len(calls),2)
            self.assertFalse(panel.retry_button.visible)

    async def test_baidu_restores_optional_landing_token_before_expiring(self):
        self.seed('baidu')
        async def handle(request):
            if request.url.path=='/disk/main': return httpx.Response(200,text='{"bdstoken":"fixture-csrf"}')
            return httpx.Response(200,json={'errno':0,'list':[]} if request.url.params.get('bdstoken')=='fixture-csrf' else {'errno':-6})
        panel,calls=self.panel('baidu',handle)
        await panel.startup(); await panel.task
        self.assertTrue(panel.api.authenticated)
        self.assertEqual(len(calls),3)

    async def test_uncheck_forgets_immediately_and_close_does_not_save(self):
        self.seed('quark')
        panel,_=self.panel('quark')
        await panel.startup(); await panel.task
        panel.remember_login.value=False
        await panel.remember_changed()
        self.assertTrue(panel.api.authenticated)
        await panel.shutdown()
        vault=SessionVault(self.tmp.name)
        self.assertFalse(list(vault.load('quark')))
        self.assertFalse(vault.preferences['quark'])
        second,_=self.panel('quark')
        second.begin=AsyncMock()
        await second.startup()
        self.assertFalse(second.remember_login.value)
        second.begin.assert_awaited_once()

    async def test_closing_old_window_does_not_restore_logout(self):
        self.seed('quark')
        old,_=self.panel('quark')
        await old.startup(); await old.task
        new,_=self.panel('quark')
        await new.startup(); await new.task
        await new.cancel()
        await old.shutdown()
        self.assertEqual(len(SessionVault(self.tmp.name).load('quark')),0)
        self.assertIn('未保存',old.session_notice.value)

    async def test_close_during_restore_keeps_last_saved_session(self):
        self.seed('quark')
        started=asyncio.Event()
        async def handle(request):
            started.set(); await asyncio.Event().wait()
        panel,_=self.panel('quark',handle)
        await panel.startup()
        await asyncio.wait_for(started.wait(),2)
        await panel.shutdown()
        self.assertGreater(len(SessionVault(self.tmp.name).load('quark')),0)
        self.assertTrue(panel.client.is_closed)

    async def test_new_scan_is_saved_immediately_before_window_closes(self):
        for provider in ('baidu','quark'):
            panel,_=self.panel(provider)
            panel.api.create_qr=AsyncMock(return_value=WebQr('fixture',b'fixture') if provider=='baidu' else QuarkQr('fixture','https://su.quark.cn/?token=fixture'))
            panel.api.poll=AsyncMock(return_value=QrState('confirmed','fixture') if provider=='baidu' else QuarkQrState('confirmed','fixture'))
            async def finish(confirmation):
                panel.client.cookies.set('BDUSS' if provider=='baidu' else '__pus','owned-fixture',
                    domain='.baidu.com' if provider=='baidu' else '.quark.cn',path='/')
                panel.api.session_candidate=True
                await panel.api._verify_directory()
            panel.api.finish_login=finish
            with patch('asyncio.sleep',new=AsyncMock()):
                await panel.begin(); await panel.task
            self.assertTrue(panel.api.authenticated)
            self.assertEqual(len(SessionVault(self.tmp.name).load(provider)),1)
            self.assertIn('已记住',panel.session_notice.value)
