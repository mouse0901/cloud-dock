"""Real current-user DPAPI, with synthetic cookies only; files stay under work/."""
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import httpx
from app.session_vault import SessionVault,SessionStoreError,SessionConflict,MAGIC,crypt

ROOT=Path(__file__).resolve().parents[1]

class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs')
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)
        self.jar=httpx.Cookies()
        self.jar.set('__pus','fixture-credential-never-print',domain='.quark.cn',path='/')
        self.jar.set('external','excluded-cookie',domain='unrelated.invalid',path='/')
        self.jar.set('BDUSS','baidu-other-provider',domain='.baidu.com',path='/')
        self.vault=SessionVault(self.path)

    def test_real_encryption_roundtrip_domain_scope_and_no_plaintext(self):
        self.vault.save('quark',self.jar.jar)
        raw=(self.path/'quark.session').read_bytes()
        self.assertNotIn(b'fixture-credential',raw)
        self.assertNotIn(b'__pus',raw)
        second=SessionVault(self.path)
        restored=second.load('quark')
        cookies=httpx.Cookies(restored)
        self.assertEqual(cookies.get('__pus'),'fixture-credential-never-print')
        self.assertEqual(len(cookies),1)
        req=httpx.Request('GET','https://unrelated.invalid/',cookies=cookies)
        self.assertNotIn('cookie',req.headers)
        self.assertFalse(list(self.path.glob('*.tmp')))

    def test_provider_swap_and_corruption_are_rejected(self):
        self.vault.save('quark',self.jar.jar)
        raw=(self.path/'quark.session').read_bytes()
        (self.path/'baidu.session').write_bytes(raw)
        with self.assertRaises(SessionStoreError): SessionVault(self.path).load('baidu')
        (self.path/'quark.session').write_bytes(raw[:40]+b'corrupt'+raw[47:])
        with self.assertRaises(SessionStoreError): SessionVault(self.path).load('quark')

    def test_expiry_and_cookie_attributes_survive(self):
        cookie=list(self.jar.jar)[0]
        cookie.secure=True
        cookie.expires=int(time.time())+3600
        cookie.discard=False
        cookie._rest={'HttpOnly':None,'SameSite':'Lax'}
        expired=httpx.Cookies(); expired.set('expired','value',domain='.quark.cn')
        old=list(expired.jar)[0]; old.expires=1; self.jar.jar.set_cookie(old)
        self.vault.save('quark',self.jar.jar)
        restored=list(self.vault.load('quark'))
        self.assertEqual(len(restored),1)
        self.assertTrue(restored[0].secure)
        self.assertFalse(restored[0].discard)
        self.assertEqual(restored[0]._rest,cookie._rest)
        self.assertEqual(restored[0].expires,cookie.expires)
        self.assertNotIn('cookie',httpx.Request('GET','http://pan.quark.cn/',cookies=httpx.Cookies(self.vault.load('quark'))).headers)

    def test_logout_cannot_be_undone_by_stale_window(self):
        self.vault.save('quark',self.jar.jar)
        old=SessionVault(self.path); old.load('quark')
        self.vault.forget('quark')
        with self.assertRaises(SessionConflict): old.save('quark',self.jar.jar)
        self.assertEqual(len(SessionVault(self.path).load('quark')),0)
        with self.assertRaises(SessionConflict): old.forget('quark',replace=False)

    def test_failure_preserves_previous_encrypted_record(self):
        self.vault.save('quark',self.jar.jar)
        original=(self.path/'quark.session').read_bytes()
        with patch('app.session_vault.os.replace',side_effect=PermissionError):
            with self.assertRaises(SessionStoreError): self.vault.save('quark',self.jar.jar)
        self.assertEqual((self.path/'quark.session').read_bytes(),original)
        self.assertFalse(list(self.path.glob('*.tmp')))

    def test_opt_out_persists_without_credentials(self):
        self.vault.save('quark',self.jar.jar)
        self.vault.forget('quark',remember=False)
        second=SessionVault(self.path)
        self.assertFalse(list(second.load('quark')))
        self.assertFalse(second.preferences['quark'])

    def test_decrypted_schema_is_validated(self):
        payload=json.dumps({'version':1,'provider':'quark','cookies':[{'name':'bad'}]}).encode()
        (self.path/'quark.session').write_bytes(MAGIC+crypt(payload,'quark'))
        with self.assertRaises(SessionStoreError): self.vault.load('quark')

    def test_decryption_failure_is_not_a_plaintext_fallback(self):
        with patch('app.session_vault.crypt',side_effect=SessionStoreError('加密不可用')):
            with self.assertRaises(SessionStoreError): self.vault.save('quark',self.jar.jar)
        self.assertFalse((self.path/'quark.session').exists())

    def test_saved_session_decrypts_in_a_fresh_process(self):
        self.vault.save('quark',self.jar.jar)
        script = "from app.session_vault import SessionVault; import sys; jar=SessionVault(sys.argv[1]).load('quark'); assert len(list(jar))==1; print('fresh-process-restore-ok')"
        result=subprocess.run([sys.executable,'-B','-c',script,str(self.path)],cwd=ROOT,
                              capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stdout.strip(),'fresh-process-restore-ok')
