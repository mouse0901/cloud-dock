"""Native, in-memory proof of Baidu device authorization and directory access."""
from __future__ import annotations
import asyncio
import os
from pathlib import Path, PurePosixPath
from functools import partial

ROOT = Path(__file__).resolve().parent
os.environ["FLET_VIEW_PATH"] = str(ROOT / "runtime" / "flet")
os.environ["FLET_APP_STORAGE_DATA"] = str(ROOT / "data")
os.environ["FLET_APP_STORAGE_TEMP"] = str(ROOT / "work")
os.environ["TEMP"] = os.environ["TMP"] = str(ROOT / "work")

import flet as ft
from app.baidu_panel import BaiduPanel as LoginProbe


async def main(page: ft.Page):
    page.title = "云屿 · 百度扫码登录验证"
    page.window.icon = str(ROOT / "assets" / "branding" / "cloud-dock.ico")
    page.window.width, page.window.height = 860, 860
    page.window.min_width, page.window.min_height = 700, 680
    page.padding = 26
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(font_family="Microsoft YaHei UI", color_scheme_seed="#2563EB")
    probe = LoginProbe(page)
    page.window.prevent_close = True
    async def window_event(e):
        if e.type == ft.WindowEventType.CLOSE:
            await probe.shutdown()
            await page.window.destroy()
    page.window.on_event = window_event
    page.on_disconnect = probe.shutdown
    probe.mount()


if __name__ == "__main__":
    ft.run(main)
