"""Run the production Quark panel alone; preserves other running app sessions."""
import asyncio
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['FLET_APP_STORAGE_DATA'] = str(ROOT / 'data')
os.environ['FLET_APP_STORAGE_TEMP'] = str(ROOT / 'work')
os.environ['FLET_VIEW_PATH'] = str(ROOT / 'runtime/flet')
os.environ['TEMP'] = os.environ['TMP'] = str(ROOT / 'work')

import flet as ft
from app.quark_panel import QuarkPanel

async def main(page: ft.Page):
    page.title = '云屿 · 夸克提速验证'
    page.window.width, page.window.height = 1100, 820
    page.window.min_width, page.window.min_height = 900, 680
    page.window.icon = str(ROOT / 'assets/branding/cloud-dock.ico')
    page.theme = ft.Theme(font_family='Microsoft YaHei UI', color_scheme_seed='#2563EB')
    page.padding = 26
    panel = QuarkPanel(page)
    page.add(panel.build())
    page.window.prevent_close = True
    async def disconnect(event=None):
        await panel.shutdown()
    async def window_event(event):
        if event.type == ft.WindowEventType.CLOSE:
            await disconnect()
            await page.window.destroy()
    page.on_disconnect = disconnect
    page.window.on_event = window_event
    await panel.startup()

if __name__ == '__main__':
    ft.run(main, assets_dir=str(ROOT / 'assets'))
