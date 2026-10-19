import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from app.layout_preferences import clean, DEFAULTS
from app.workspace import Workspace
from app.cloud_operations import CloudOperationError
from desktop.productivity import Productivity

ROOT = Path(__file__).resolve().parents[1]


class LayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_layout_roundtrip_and_invalid_input(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as tmp:
            root = Path(tmp)
            w = Workspace(root)
            w.settings.update(clean({'sidebar_width':280,'show_date':False,'size_width':120}))
            await w.save()
            saved = Workspace(root)
            self.assertEqual(saved.settings['sidebar_width'],280)
            self.assertFalse(saved.settings['show_date'])
            self.assertEqual(saved.settings['size_width'],120)
            (root/'preferences.json').write_text(json.dumps({'sidebar_width':999999,'size_width':True,'show_date':'false'}),encoding='utf-8')
            saved = Workspace(root)
            for k in ('sidebar_width','size_width','show_date'):
                self.assertEqual(saved.settings[k],DEFAULTS[k])

    def test_unrelated_paths_or_credentials_never_enter_layout(self):
        self.assertEqual(clean({'cookie':'private','download_dir':'C:/private','sidebar_width':180}),{'sidebar_width':180})

    def test_portable_layout_validated_without_private_fields(self):
        from desktop.settings_portability import clean as portable
        self.assertEqual(portable({'sidebar_width':240,'show_date':False,'cookie':'secret'}),{'sidebar_width':240,'show_date':False})
        with self.assertRaises(CloudOperationError):
            portable({'sidebar_width':9000},True)
        with self.assertRaises(CloudOperationError):
            portable({'show_date':'false'},True)


class SearchStopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stamp = 'A'
        self.tools = Productivity(NS(features=NS(ops={'baidu':NS(stamp=lambda:self.stamp)})))
        self.addAsyncCleanup(self.tools.close)

    async def test_stop_retains_results_and_joins_scan(self):
        row = self.tools.begin('baidu','A','search')
        row['rows'] = [{'id':'found','parent':'/'}]
        waiting = asyncio.create_task(asyncio.sleep(30))
        self.tools.tasks[row['token']] = waiting
        result = await self.tools.action({'action':'tools_stop','token':row['token']})
        self.assertTrue(waiting.cancelled())
        self.assertEqual(result['status'],'stopped')
        self.assertEqual(self.tools.jobs[row['token']]['rows'],[{'id':'found','parent':'/'}])
        await self.tools.action({'action':'tools_cancel','token':row['token']})
        self.assertNotIn(row['token'],self.tools.jobs)

    async def test_stop_rejects_other_account_and_mutation(self):
        row = self.tools.begin('baidu','A','search')
        self.stamp = 'B'
        with self.assertRaises(CloudOperationError):
            await self.tools.action({'action':'tools_stop','token':row['token']})
        row = self.tools.begin('baidu','B','operation')
        with self.assertRaises(CloudOperationError):
            await self.tools.action({'action':'tools_stop','token':row['token']})
