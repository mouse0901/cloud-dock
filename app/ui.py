from __future__ import annotations
import asyncio
from functools import partial
from pathlib import PurePosixPath
import flet as ft
from app.domain import human_size, FilePage
from app.theme import LIGHT, DARK
from app.workspace import Navigation
from app.local_files import prepare_download_root, open_local_folder


class CloudDock:
    def __init__(self, page, provider, quark, workspace, initial_section="baidu", auto_login=False):
        self.page, self.provider, self.quark, self.workspace = page, provider, quark, workspace
        self.section = initial_section
        self.baidu_panel = None
        self.quark_panel = None
        self.auto_login = auto_login
        self.nav = Navigation()
        self.query, self.category, self.sort = "", "all", "name"
        self.page_index = 0
        self.files = FilePage([], 0, 0, 50)
        self.selected = set()
        self.search_task = None
        self.load_generation = 0
        self.transfer_tab = "active"
        self.progress_refs = {}
        self.running = True

    @property
    def c(self):
        theme = self.workspace.settings["theme"]
        return DARK if theme == "dark" or (theme == "system" and self.page.platform_brightness == ft.Brightness.DARK) else LIGHT

    def text(self, value, size=13, color=None, weight=None, **kw):
        return ft.Text(value, size=size, color=color or self.c["text"], weight=weight, **kw)

    def small(self, value):
        return self.text(value, 11, self.c["muted"])

    def badge(self, value, color=None, bg=None):
        return ft.Container(self.text(value, 10, color or self.c["accent"]), padding=ft.Padding.symmetric(horizontal=8, vertical=4), border_radius=6, bgcolor=bg or self.c["selected"])

    def icon_button(self, icon, tooltip, fn, disabled=False):
        return ft.IconButton(icon, icon_size=19, icon_color=self.c["muted"], tooltip=tooltip, on_click=fn, disabled=disabled, width=36, height=36)

    def action(self, label, icon, fn, filled=False, disabled=False):
        return ft.Button(content=ft.Row([ft.Icon(icon, size=17), ft.Text(label, size=12)], spacing=7, tight=True), on_click=fn, disabled=disabled, height=36,
            style=ft.ButtonStyle(color="#FFFFFF" if filled else self.c["text"], bgcolor=self.c["accent"] if filled else self.c["soft"], shape=ft.RoundedRectangleBorder(radius=8), padding=ft.Padding.symmetric(horizontal=14)))

    def nav_item(self, label, icon, key, suffix=None):
        active = self.section == key
        controls = [ft.Icon(icon, size=20, color=self.c["accent"] if active else self.c["muted"]), self.text(label, 13, self.c["accent"] if active else self.c["text"], ft.FontWeight.W_600 if active else ft.FontWeight.W_400, expand=True)]
        if suffix:
            controls.append(self.small(suffix))
        return ft.Container(ft.Row(controls, spacing=12), height=44, padding=ft.Padding.symmetric(horizontal=12), border_radius=9, bgcolor=self.c["selected"] if active else None, on_click=partial(self.navigate, key))

    def sidebar(self):
        count = sum(t.state in {"running", "queued", "paused"} for t in self.workspace.transfers)
        count += sum(job.state not in {'completed', 'cancelled'} for _, job in self.live_jobs())
        logo = ft.Row([ft.Image(src="branding/cloud-dock.png", width=40, height=40, fit=ft.BoxFit.CONTAIN, tooltip="云屿"), ft.Column([self.text("云屿", 21, weight=ft.FontWeight.W_700), self.small("CLOUD DOCK")], spacing=0)], spacing=11)
        return ft.Column([
            ft.Container(logo, padding=ft.Padding.only(left=8, top=16, bottom=30)),
            self.nav_item("工作空间", ft.Icons.DASHBOARD_OUTLINED, "overview"),
            self.nav_item("传输中心", ft.Icons.SWAP_VERT_ROUNDED, "transfers", str(count) if count else None),
            ft.Container(self.small("我的网盘"), padding=ft.Padding.only(left=12, top=24, bottom=8)),
            self.nav_item("百度网盘", ft.Icons.CLOUD_OUTLINED, "baidu_live", "已连接" if self.baidu_connected else "未连接"),
            self.nav_item("夸克网盘", ft.Icons.LAYERS_OUTLINED, "quark", "已连接" if self.quark_connected else "未连接"),
            ft.Container(expand=True),
            ft.Container(ft.Column([self.small("各自账号 · 独立授权"), self.small("文件保留在原网盘中")], spacing=8), padding=14, bgcolor=self.c["surface"], border_radius=10),
            self.nav_item("设置", ft.Icons.SETTINGS_OUTLINED, "settings"),
            ft.Divider(height=1, color=self.c["line"]),
            ft.Container(ft.Row([ft.Container(self.text("我", 12, self.c["muted"]), alignment=ft.Alignment.CENTER, width=30, height=30, border_radius=15, bgcolor=self.c["line"]), ft.Column([self.text("个人工作空间", 12), self.small("Windows · 本地开发版")], spacing=2)], spacing=10), padding=ft.Padding.only(left=10, top=14, bottom=8))
        ], spacing=5, expand=True)

    def render_shell(self):
        self.page.bgcolor = self.c["bg"]
        self.page.theme_mode = {"light": ft.ThemeMode.LIGHT, "dark": ft.ThemeMode.DARK, "system": ft.ThemeMode.SYSTEM}[self.workspace.settings["theme"]]
        self.page.theme = ft.Theme(color_scheme_seed="#2563EB", font_family="Microsoft YaHei UI", use_material3=True)
        self.page.dark_theme = ft.Theme(color_scheme_seed="#78A4FF", font_family="Microsoft YaHei UI", use_material3=True)
        self.sidebar_host = ft.Container(self.sidebar(), width=218, padding=ft.Padding.symmetric(horizontal=14), bgcolor=self.c["rail"], border=ft.Border.only(right=ft.BorderSide(1, self.c["line"])))
        self.search = ft.TextField(value=self.query, hint_text="搜索示例空间", prefix_icon=ft.Icons.SEARCH_ROUNDED, text_size=12, height=38, width=290, border=ft.NoInputBorder(), border_radius=8, filled=True, bgcolor=self.c["soft"], content_padding=10, on_change=self.on_search, on_submit=self.on_search)
        self.top_label = self.text("我的网盘  /  百度网盘", 12, self.c["muted"])
        top = ft.Container(ft.Row([self.top_label, ft.Container(expand=True), self.search, self.icon_button(ft.Icons.DARK_MODE_OUTLINED if self.c is LIGHT else ft.Icons.LIGHT_MODE_OUTLINED, "切换外观", self.toggle_theme)], spacing=16), height=66, padding=ft.Padding.symmetric(horizontal=30), border=ft.Border.only(bottom=ft.BorderSide(1, self.c["line"])))
        self.body = ft.Container(expand=True, padding=ft.Padding.only(left=30, right=30, top=25, bottom=14))
        self.page.controls.clear()
        self.page.add(ft.Row([self.sidebar_host, ft.Column([top, self.body], spacing=0, expand=True)], spacing=0, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH))
        self.render_page()

    def render_page(self):
        labels = {"baidu_live": "我的网盘  /  百度网盘", "overview": "工作空间", "baidu": "本地演示  /  示例空间", "quark": "我的网盘  /  夸克网盘", "transfers": "工作空间  /  传输中心", "settings": "工作空间  /  设置"}
        self.top_label.value = labels[self.section]
        self.search.visible = self.section == "baidu"
        self.sidebar_host.content = self.sidebar()
        self.progress_refs = {}
        self.body.content = {"baidu_live": self.baidu_page, "baidu": self.files_page, "overview": self.overview_page, "quark": self.quark_page, "transfers": self.transfers_page, "settings": self.settings_page}[self.section]()
        self.page.update()

    @property
    def baidu_connected(self):
        return self.baidu_panel is not None and self.baidu_panel.reader is not None

    def baidu_page(self):
        if self.baidu_panel is None:
            from app.baidu_web_panel import BaiduWebPanel
            self.baidu_panel = BaiduWebPanel(self.page, self.baidu_session_changed, diagnostic=BaiduWebPanel.record_diagnostic, on_transfer_change=self.live_transfers_changed)
        self.configure_live_panel(self.baidu_panel)
        return self.baidu_panel.view

    @property
    def quark_connected(self):
        return self.quark_panel is not None and self.quark_panel.reader is not None

    def configure_live_panel(self,panel):
        panel.set_download_root(self.workspace.settings['download_dir'])
        panel.on_transfer_navigation = partial(self.navigate,'transfers')

    async def restore_saved_accounts(self):
        self.baidu_page()
        self.quark_page()
        await asyncio.gather(self.baidu_panel.startup(saved_only=True),
                             self.quark_panel.startup(saved_only=True))

    def live_jobs(self):
        return [(panel, job) for panel in (self.baidu_panel, self.quark_panel)
                if panel is not None for job in [*getattr(panel, 'jobs', []), *getattr(panel, 'upload_jobs', [])]]

    def live_transfers_changed(self):
        if self.running and hasattr(self, 'sidebar_host'):
            if self.section in ('transfers','overview'):
                self.render_page()
            else:
                self.sidebar_host.content = self.sidebar()
                self.page.update()

    def baidu_session_changed(self):
        if self.running and hasattr(self, "sidebar_host"):
            self.render_page()

    async def start(self):
        self.page.on_keyboard_event = self.keyboard
        self.page.on_platform_brightness_change = self.platform_theme
        self.page.on_disconnect = self.disconnect
        await self.load_files(render=False)
        self.render_shell()
        self.page.run_task(self.transfer_clock)
        if self.auto_login:
            await self.restore_saved_accounts()
            if self.section == "baidu_live" and not self.baidu_panel.started:
                await self.baidu_panel.startup()
            elif self.section == "quark" and not self.quark_panel.started:
                await self.quark_panel.startup()

    async def disconnect(self, e):
        self.running = False
        if self.search_task:
            self.search_task.cancel()
        if self.baidu_panel:
            await self.baidu_panel.shutdown()
        if self.quark_panel:
            await self.quark_panel.shutdown()
        await self.workspace.save()

    async def keyboard(self, e):
        if e.ctrl and e.key.lower() == "f":
            if self.section == "baidu":
                await self.search.focus()
            elif self.section in ("baidu_live","quark"):
                panel = self.baidu_panel if self.section == "baidu_live" else self.quark_panel
                if panel and panel.reader:
                    await panel.browser_search.focus()
        elif e.key == "Escape" and self.section in ("baidu_live","quark"):
            panel = self.baidu_panel if self.section == "baidu_live" else self.quark_panel
            if panel and panel.reader:
                panel.browser_selected.clear()
                panel.render_browser_rows()
                panel.update()
        elif e.key == "Escape" and self.selected:
            self.selected.clear()
            self.render_page()

    async def platform_theme(self, e):
        if self.workspace.settings["theme"] == "system":
            self.render_shell()

    async def navigate(self, section, e=None):
        self.section = section
        self.selected.clear()
        if self.search_task:
            self.search_task.cancel()
        if section == "baidu":
            await self.load_files(render=False)
        self.render_page()
        if self.auto_login and section == "baidu_live" and not self.baidu_panel.started:
            await self.baidu_panel.startup()

        if self.auto_login and section == "quark" and not self.quark_panel.started:
            await self.quark_panel.startup()

    async def load_files(self, render=True):
        self.load_generation += 1
        generation = self.load_generation
        result = await self.provider.list_files(self.nav.current, self.query, self.category, self.page_index, 50, self.sort)
        if generation == self.load_generation:
            self.files = result
            if render and self.section == "baidu":
                self.render_page()

    async def on_search(self, e):
        self.query = e.control.value.strip()
        self.page_index = 0
        self.selected.clear()
        if self.search_task:
            self.search_task.cancel()
        self.search_task = asyncio.create_task(self.debounced_search())

    async def debounced_search(self):
        try:
            await asyncio.sleep(0.25)
            await self.load_files()
        except asyncio.CancelledError:
            pass

    async def visit(self, path, e=None):
        self.nav.visit(path)
        self.query, self.category, self.page_index = "", "all", 0
        self.search.value = ""
        self.selected.clear()
        await self.load_files()

    async def history(self, delta, e=None):
        await self.visit(self.nav.step(delta))

    async def filter_files(self, category, e=None):
        self.category, self.page_index = category, 0
        self.selected.clear()
        await self.load_files()

    async def set_view(self, mode, e=None):
        self.workspace.settings["view"] = mode
        await self.workspace.save()
        self.render_page()

    async def change_sort(self, e):
        self.sort = "modified" if self.sort == "name" else "name"
        await self.load_files()

    async def paginate(self, step, e=None):
        self.page_index = max(0, self.page_index + step)
        self.selected.clear()
        await self.load_files()

    async def select(self, item, e):
        if item.id in self.selected:
            self.selected.remove(item.id)
        else:
            self.selected.add(item.id)
        self.render_page()

    async def select_all(self, e):
        ids = {f.id for f in self.files.items}
        self.selected = set() if ids <= self.selected else ids
        self.render_page()

    def file_icon(self, item, size=23):
        icon, color = {"folder": (ft.Icons.FOLDER_ROUNDED, "#DBAC50"), "document": (ft.Icons.DESCRIPTION_OUTLINED, self.c["accent"]), "image": (ft.Icons.IMAGE_OUTLINED, self.c["green"]), "video": (ft.Icons.VIDEO_FILE_OUTLINED, self.c["purple"]), "other": (ft.Icons.ARCHIVE_OUTLINED, self.c["muted"])}[item.category]
        return ft.Icon(icon, color=color, size=size)

    def title_row(self, title, subtitle, actions=None):
        return ft.Row([ft.Column([self.text(title, 27, weight=ft.FontWeight.W_700), self.text(subtitle, 12, self.c["muted"])], spacing=7, expand=True), *(actions or [])], vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def pills(self):
        return ft.Row([ft.Container(self.text(label, 12, self.c["accent"] if self.category == key else self.c["muted"]), padding=ft.Padding.symmetric(horizontal=13, vertical=7), border_radius=7, bgcolor=self.c["selected"] if self.category == key else None, on_click=partial(self.filter_files, key)) for key, label in [("all", "全部文件"), ("document", "文档"), ("image", "图片"), ("video", "视频"), ("other", "其他")]], spacing=4)

    def file_row(self, item):
        return ft.Container(ft.Row([
            ft.Checkbox(value=item.id in self.selected, width=30, on_change=partial(self.select, item)),
            self.file_icon(item),
            ft.Container(self.text(item.name, 13, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS), on_click=partial(self.open_file, item), expand=True, tooltip="打开文件夹" if item.is_dir else "查看文件信息"),
            self.text("—" if item.is_dir else human_size(item.size), 12, self.c["muted"], width=92),
            self.text(item.modified[:10], 12, self.c["muted"], width=102),
            self.icon_button(ft.Icons.MORE_HORIZ, "文件操作", partial(self.details, item))
        ], spacing=13), height=54, padding=ft.Padding.symmetric(horizontal=12), bgcolor=self.c["selected"] if item.id in self.selected else None, border=ft.Border.only(bottom=ft.BorderSide(1, self.c["line"])))

    def file_card(self, item):
        return ft.Container(ft.Column([
            ft.Row([ft.Checkbox(value=item.id in self.selected, width=30, height=28, on_change=partial(self.select, item)), ft.Container(expand=True), self.icon_button(ft.Icons.MORE_HORIZ, "文件操作", partial(self.details, item))]),
            ft.Container(self.file_icon(item, 42), expand=True, alignment=ft.Alignment.CENTER, on_click=partial(self.open_file, item)),
            ft.Container(self.text(item.name, 12, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS), on_click=partial(self.open_file, item)),
            self.small("文件夹" if item.is_dir else human_size(item.size))
        ], spacing=7), padding=12, bgcolor=self.c["selected"] if item.id in self.selected else self.c["surface"], border=ft.Border.all(1, self.c["line"]), border_radius=11)

    def empty(self, icon, title, subtitle):
        return ft.Container(ft.Column([ft.Container(ft.Icon(icon, size=32, color=self.c["muted"]), padding=20, bgcolor=self.c["soft"], border_radius=20), self.text(title, 17, weight=ft.FontWeight.W_600), self.text(subtitle, 12, self.c["muted"], text_align=ft.TextAlign.CENTER)], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=14), expand=True, alignment=ft.Alignment.CENTER)

    def files_page(self):
        crumbs = [self.icon_button(ft.Icons.CHEVRON_LEFT_ROUNDED, "后退", partial(self.history, -1), self.nav.index == 0), self.icon_button(ft.Icons.CHEVRON_RIGHT_ROUNDED, "前进", partial(self.history, 1), self.nav.index >= len(self.nav.paths) - 1)]
        parts = self.nav.current.strip("/").split("/") if self.nav.current != "/" else []
        crumbs.append(ft.TextButton(content="搜索结果" if self.query else "全部文件", on_click=partial(self.visit, "/"), style=ft.ButtonStyle(color=self.c["text"])))
        if not self.query:
            for i, part in enumerate(parts):
                crumbs += [ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=15, color=self.c["muted"]), ft.TextButton(content=part, on_click=partial(self.visit, "/" + "/".join(parts[:i + 1])), style=ft.ButtonStyle(color=self.c["text"]))]
        crumbs += [ft.Container(expand=True), self.icon_button(ft.Icons.SORT_ROUNDED, "排序：" + ("名称" if self.sort == "name" else "最近修改"), self.change_sort), self.icon_button(ft.Icons.VIEW_LIST_OUTLINED, "列表视图", partial(self.set_view, "list")), self.icon_button(ft.Icons.GRID_VIEW_ROUNDED, "网格视图", partial(self.set_view, "grid"))]
        if self.selected:
            bar = ft.Row([self.text(f"已选 {len(self.selected)} 项", 12, self.c["accent"], expand=True), self.action("加入演示传输", ft.Icons.DOWNLOAD_ROUNDED, self.download_selected, True), self.action("重命名", ft.Icons.EDIT_OUTLINED, self.rename_selected, disabled=len(self.selected) != 1), self.action("删除", ft.Icons.DELETE_OUTLINE, self.delete_selected)], spacing=8)
        else:
            bar = ft.Row([self.pills(), ft.Container(expand=True), self.small("Ctrl + F 搜索")])
        if not self.files.items:
            content = self.empty(ft.Icons.SEARCH_OFF_ROUNDED if self.query else ft.Icons.FOLDER_OPEN_ROUNDED, "没有找到匹配的文件" if self.query else "这里还没有文件", "试试其他关键词或文件类型" if self.query else "可以先创建一个文件夹，整理你的空间")
        elif self.workspace.settings["view"] == "grid":
            content = ft.GridView(controls=[self.file_card(f) for f in self.files.items], expand=True, max_extent=185, child_aspect_ratio=0.95, spacing=12, run_spacing=12, padding=2)
        else:
            head = ft.Container(ft.Row([ft.Checkbox(value=bool(self.files.items) and all(f.id in self.selected for f in self.files.items), width=30, on_change=self.select_all), self.small("名称"), ft.Container(expand=True), ft.Container(self.small("大小"), width=92), ft.Container(self.small("修改时间"), width=102), ft.Container(width=36)], spacing=13), height=40, padding=ft.Padding.symmetric(horizontal=12), bgcolor=self.c["soft"])
            content = ft.Container(ft.Column([head, ft.ListView(controls=[self.file_row(f) for f in self.files.items], expand=True, spacing=0, item_extent=54)], spacing=0), border=ft.Border.all(1, self.c["line"]), border_radius=10, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, expand=True)
        page_count = max(1, (self.files.total + 49) // 50)
        footer = ft.Row([self.small(f"{self.files.total} 个项目" + (" · 搜索范围：百度网盘示例空间" if self.query else " · 仅展示示例数据")), ft.Container(expand=True), self.icon_button(ft.Icons.CHEVRON_LEFT_ROUNDED, "上一页", partial(self.paginate, -1), self.page_index == 0), self.small(f"{self.page_index + 1} / {page_count}"), self.icon_button(ft.Icons.CHEVRON_RIGHT_ROUNDED, "下一页", partial(self.paginate, 1), self.page_index + 1 >= page_count)], spacing=6)
        return ft.Column([self.title_row("百度网盘", "文件有序，随时找到。", [self.badge("示例空间"), self.action("新建文件夹", ft.Icons.CREATE_NEW_FOLDER_OUTLINED, self.new_folder, True)]), ft.Container(height=1), ft.Row(crumbs, spacing=1), bar, content, footer], spacing=15, expand=True)

    def overview_page(self):
        def provider_card(title, subtitle, icon, color, key, badge):
            return ft.Container(ft.Column([ft.Row([ft.Container(ft.Icon(icon, color=color, size=24), width=48, height=48, bgcolor=self.c["soft"], border_radius=12, alignment=ft.Alignment.CENTER), ft.Container(expand=True), self.badge(badge, color=color)]), self.text(title, 19, weight=ft.FontWeight.W_600), self.text(subtitle, 12, self.c["muted"]), ft.Container(height=8), self.action("打开网盘" if badge == "已连接" else "连接账号", ft.Icons.ARROW_FORWARD_ROUNDED, partial(self.navigate, key))], spacing=14), padding=24, border=ft.Border.all(1, self.c["line"]), border_radius=14, bgcolor=self.c["surface"], expand=True)
        return ft.Column([
            self.title_row("你的云端工作空间", "百度与夸克，各自保存，统一浏览。", [self.badge("开发版 0.3")]),
            ft.Container(ft.Row([ft.Column([self.text("两个网盘，一个入口。", 25, weight=ft.FontWeight.W_700), self.text("切换空间，找到文件，在同一个地方查看传输。", 13, self.c["muted"]), ft.Container(height=8), self.action("进入百度网盘", ft.Icons.ARROW_FORWARD_ROUNDED, partial(self.navigate, "baidu_live"), True)], spacing=12, expand=True), ft.Icon(ft.Icons.FILTER_DRAMA_OUTLINED, color=self.c["accent"], size=86)], spacing=20), padding=30, bgcolor=self.c["selected"], border_radius=16),
            ft.Row([provider_card("百度网盘", "直接扫码连接自己的百度账号", ft.Icons.CLOUD_OUTLINED, self.c["accent"], "baidu_live", "已连接" if self.baidu_connected else "未连接"), provider_card("夸克网盘", "扫码登录，浏览分享并直接下载", ft.Icons.LAYERS_OUTLINED, self.c["purple"], "quark", "已连接" if self.quark_connected else "未连接")], spacing=18),
            ft.Container(self.text("当前版本", 14, weight=ft.FontWeight.W_600), padding=ft.Padding.only(top=6)),
            ft.Container(ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=19, color=self.c["muted"]), self.text("已保存的账号会自动恢复。选择文件后加入下载，进度和完成文件统一在传输中心查看。", 12, self.c["muted"], expand=True)]), padding=18, bgcolor=self.c["soft"], border_radius=10)
        ], spacing=23, scroll=ft.ScrollMode.AUTO, expand=True)

    def quark_page(self):
        if self.quark_panel is None:
            from app.quark_panel import QuarkPanel
            self.quark_panel = QuarkPanel(self.page,
                on_session_change=self.baidu_session_changed,
                on_transfer_change=self.live_transfers_changed)
        self.configure_live_panel(self.quark_panel)
        return self.quark_panel.view

    def transfer_row(self, task):
        state_names = {"queued": "等待开始", "running": "演示进行中", "paused": "已暂停", "done": "演示完成", "cancelled": "已取消"}
        color = self.c["green"] if task.state == "done" else self.c["accent"]
        progress = ft.ProgressBar(value=task.progress, color=color, bgcolor=self.c["line"], height=3)
        status = self.text(state_names[task.state], 11, color)
        amount = self.small(f"{round(task.progress * 100)}% · {human_size(task.size)}")
        self.progress_refs[task.id] = (progress, status, amount)
        actions = []
        if task.state in {"queued", "running", "paused"}:
            action = "resume" if task.state == "paused" else "pause"
            actions.append(self.icon_button(ft.Icons.PLAY_ARROW_ROUNDED if action == "resume" else ft.Icons.PAUSE_ROUNDED, "继续" if action == "resume" else "暂停", partial(self.transfer_action, task.id, action)))
            actions.append(self.icon_button(ft.Icons.CLOSE_ROUNDED, "取消演示任务", partial(self.transfer_action, task.id, "cancel")))
        else:
            actions.append(ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE if task.state == "done" else ft.Icons.REMOVE_CIRCLE_OUTLINE, size=20, color=color if task.state == "done" else self.c["muted"]))
        return ft.Container(ft.Row([ft.Container(ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, color=self.c["accent"], size=25), width=46, height=46, bgcolor=self.c["selected"], border_radius=10, alignment=ft.Alignment.CENTER), ft.Column([ft.Row([self.text(task.name, 13, weight=ft.FontWeight.W_600, expand=True), status]), ft.Row([self.small("百度网盘 · 演示任务"), ft.Container(expand=True), amount]), progress], spacing=10, expand=True), *actions], spacing=18), padding=20, border=ft.Border.all(1, self.c["line"]), border_radius=11, bgcolor=self.c["surface"])

    def transfers_page(self):
        active = [t for t in self.workspace.transfers if t.state not in {'done', 'cancelled'}]
        completed = [t for t in self.workspace.transfers if t.state in {'done', 'cancelled'}]
        live = self.live_jobs()
        live_active = [(p, j) for p, j in live if j.state not in {'completed', 'cancelled'}]
        live_completed = [(p, j) for p, j in live if j.state in {'completed', 'cancelled'}]
        tabs = ft.Row([ft.Container(self.text(label, 13, self.c['accent'] if self.transfer_tab == key else self.c['muted']),
            padding=ft.Padding.symmetric(horizontal=15, vertical=9),
            bgcolor=self.c['selected'] if self.transfer_tab == key else None,
            border_radius=8, on_click=partial(self.switch_transfer_tab, key))
            for key, label in [('active', f'进行中  {len(active) + len(live_active)}'),
                               ('completed', f'已结束  {len(completed) + len(live_completed)}')]], spacing=7)
        ended = self.transfer_tab == 'completed'
        tasks = completed if ended else active
        live_visible = live_completed if ended else live_active
        rows = []
        if live_visible:
            rows.append(self.text('上传与下载', 14, weight=ft.FontWeight.W_600))
            for panel in (self.baidu_panel, self.quark_panel):
                if panel is not None and getattr(panel, 'jobs', None):
                    rows.extend(panel.render_live_downloads(completed=ended).controls)
                if panel is not None and getattr(panel, 'upload_jobs', None):
                    rows.extend(panel.render_live_uploads(completed=ended).controls)
        if tasks:
            rows.append(self.text('示例任务 · 不保存真实文件', 13, self.c['muted']))
            rows.extend(self.transfer_row(t) for t in reversed(tasks))
        content = (ft.ListView(rows, spacing=12, expand=True) if rows else
            self.empty(ft.Icons.CLOUD_DOWNLOAD_OUTLINED,
                       '暂时没有传输任务' if not ended else '暂时没有已结束任务',
                       '登录网盘后，可上传本地文件或下载网盘文件'))
        note = '文件在本机与所选网盘之间传输。上传支持多选、取消和失败重试；下载可暂停和继续。退出账号或关闭窗口后，未完成任务需重新创建。'
        return ft.Column([self.title_row('传输中心', '所有网盘的传输，集中在这里。'),
            ft.Container(ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=17, color=self.c['accent']),
                self.text(note, 12, self.c['muted'], expand=True)]),
                padding=14, bgcolor=self.c['selected'], border_radius=9),
            tabs, content], spacing=22, expand=True)

    async def switch_transfer_tab(self, key, e=None):
        self.transfer_tab = key
        self.render_page()

    async def transfer_action(self, task_id, action, e=None):
        self.workspace.transition(task_id, action)
        await self.workspace.save()
        self.render_page()

    async def transfer_clock(self):
        while self.running:
            await asyncio.sleep(0.5)
            if not self.running:
                break
            before = [(t.id, t.state) for t in self.workspace.transfers]
            self.workspace.tick()
            after = [(t.id, t.state) for t in self.workspace.transfers]
            if before != after:
                await self.workspace.save()
                if self.section == "transfers":
                    self.render_page()
                else:
                    self.sidebar_host.content = self.sidebar()
                    self.sidebar_host.update()
            elif self.section == "transfers":
                for t in self.workspace.transfers:
                    refs = self.progress_refs.get(t.id)
                    if refs and t.state == "running":
                        refs[0].value = t.progress
                        refs[2].value = f"{round(t.progress * 100)}% · {human_size(t.size)}"
                        refs[0].update()
                        refs[2].update()

    def setting_row(self, title, subtitle, control):
        return ft.Container(ft.Row([ft.Column([self.text(title, 13, weight=ft.FontWeight.W_500), self.small(subtitle)], spacing=5, expand=True), control], spacing=16), padding=18, border=ft.Border.only(bottom=ft.BorderSide(1, self.c["line"])))

    def choices(self, key, values):
        return ft.Row([ft.Container(self.text(label, 12, self.c["accent"] if self.workspace.settings[key] == value else self.c["muted"]), padding=ft.Padding.symmetric(horizontal=12, vertical=9), border_radius=7, bgcolor=self.c["selected"] if self.workspace.settings[key] == value else self.c["soft"], on_click=partial(self.set_setting, key, value)) for value, label in values], spacing=5)

    def settings_page(self):
        group = ft.Container(self.setting_row("外观", "选择适合你的明暗风格",
            self.choices("theme", [("light", "浅色"), ("dark", "深色"), ("system", "跟随系统")])),
            border=ft.Border.all(1,self.c['line']),border_radius=12)
        demo_tools = ft.ExpansionTile(title=self.text("示例空间 · 开发工具",12),controls=[
            self.setting_row("演示视图","仅用于示例空间",self.choices("view",[("list","列表"),("grid","网格")])),
            self.setting_row("演示并发","仅用于模拟任务",self.choices("concurrency",[(1,"1"),(2,"2"),(3,"3")])),
            self.action("打开示例空间",ft.Icons.FOLDER_OUTLINED,partial(self.navigate,"baidu"))])
        download_group = ft.Container(ft.Column([
            self.text("下载位置",13,weight=ft.FontWeight.W_600),
            ft.Text(self.workspace.settings['download_dir'],size=12,selectable=True),
            self.small("按网盘和账号会话分目录保存；更改位置只影响新加入的任务。"),
            ft.Row([self.action("更改文件夹",ft.Icons.FOLDER_OPEN_OUTLINED,self.choose_download_folder),
                    self.action("打开下载文件夹",ft.Icons.OPEN_IN_NEW_ROUNDED,self.open_download_root)],wrap=True),
        ],spacing=10),padding=18,border=ft.Border.all(1,self.c['line']),border_radius=12)
        return ft.Column([self.title_row("设置", "让工作空间符合你的习惯。"), self.text("通用", 12, self.c["muted"]), group, download_group, self.text("账号", 12, self.c["muted"]), ft.Container(ft.Column([
            self.setting_row("百度网盘", "已连接 · 记住登录可自动恢复" if self.baidu_connected else "扫码连接你自己的账号", self.action("管理账号", ft.Icons.LINK_ROUNDED, partial(self.manage_account,"baidu_live"))),
            self.setting_row("夸克网盘", "已连接 · 支持文件与分享下载" if self.quark_connected else "扫码连接你自己的账号", self.action("管理账号", ft.Icons.LINK_ROUNDED, partial(self.manage_account,"quark")))
        ], spacing=0), border=ft.Border.all(1, self.c["line"]), border_radius=12), self.text("关于云屿", 12, self.c["muted"]), ft.Container(ft.Column([self.text("云屿 Cloud Dock · 0.3.0 本地开发版", 13), self.text("面向 Windows 用户的百度与夸克聚合客户端。\n非百度、夸克官方产品；不提供限速绕过功能。", 12, self.c["muted"]), self.small("登录信息在当前 Windows 账号下加密保存。")], spacing=9), padding=18, bgcolor=self.c["soft"], border_radius=10),demo_tools], spacing=18, horizontal_alignment=ft.CrossAxisAlignment.STRETCH, scroll=ft.ScrollMode.AUTO, expand=True)

    async def manage_account(self,section,e=None):
        await self.navigate(section)
        panel = self.baidu_panel if section == 'baidu_live' else self.quark_panel
        panel.account_details_open = True
        panel.update()

    async def choose_download_folder(self,e=None):
        try:
            selected = await ft.FilePicker().get_directory_path(dialog_title='选择下载位置',
                initial_directory=self.workspace.settings['download_dir'])
            if selected:
                await self.change_download_directory(selected)
        except Exception:
            self.toast('无法选择下载文件夹，请重试。')

    async def change_download_directory(self,path):
        try:
            directory = await asyncio.to_thread(prepare_download_root,path)
        except (OSError,ValueError):
            self.toast('文件夹不可用或没有写入权限，请选择其他位置。')
            return False
        old = self.workspace.settings['download_dir']
        self.workspace.settings['download_dir'] = str(directory)
        try:
            await self.workspace.save()
        except OSError:
            self.workspace.settings['download_dir'] = old
            self.toast('下载位置未能保存，请重试。')
            return False
        for panel in (self.baidu_panel,self.quark_panel):
            if panel is not None:
                self.configure_live_panel(panel)
        self.render_page()
        self.toast('下载位置已更新；已加入的任务仍保存到原位置。')
        return True

    async def open_download_root(self,e=None):
        try:
            directory = await asyncio.to_thread(prepare_download_root,self.workspace.settings['download_dir'])
            await asyncio.to_thread(open_local_folder,directory)
        except (OSError,ValueError):
            self.toast('下载文件夹无法打开，请检查设置中的保存位置。')

    async def set_setting(self, key, value, e=None):
        self.workspace.settings[key] = value
        await self.workspace.save()
        if key == "theme":
            self.render_shell()
        else:
            self.render_page()

    async def toggle_theme(self, e):
        await self.set_setting("theme", "dark" if self.c is LIGHT else "light")

    def toast(self, message):
        self.page.show_dialog(ft.SnackBar(content=ft.Text(message, color="#FFFFFF", size=12), bgcolor="#263548", duration=2500))

    async def close_dialog(self, e=None):
        self.page.pop_dialog()

    async def connection_info(self, e):
        await self.navigate("baidu_live")


    async def open_file(self, item, e=None):
        if item.is_dir:
            await self.visit(item.path)
        else:
            await self.details(item)

    async def details(self, item, e=None):
        async def rename_from_details(e):
            self.page.pop_dialog()
            await self.name_dialog(item)
        async def download_from_details(e):
            self.page.pop_dialog()
            await self.enqueue([item])
        controls = [self.file_icon(item, 46), self.text(item.name, 18, weight=ft.FontWeight.W_600), self.text(item.path, 12, self.c["muted"], selectable=True), ft.Divider(color=self.c["line"]), self.text("来源：百度网盘 · 示例数据", 12), self.text("大小：" + ("文件夹" if item.is_dir else human_size(item.size)), 12), self.text("修改时间：" + item.modified, 12), self.small("内容预览将在真实接入后提供。")]
        actions = [ft.TextButton(content="关闭", on_click=self.close_dialog), ft.TextButton(content="重命名", on_click=rename_from_details)]
        if not item.is_dir:
            actions.append(ft.Button(content="加入演示传输", on_click=download_from_details))
        self.page.show_dialog(ft.AlertDialog(content=ft.Container(ft.Column(controls, spacing=13, tight=True), width=400), actions=actions))

    async def new_folder(self, e):
        await self.name_dialog()

    async def rename_selected(self, e):
        item = next((f for f in self.files.items if f.id in self.selected), None)
        if item:
            await self.name_dialog(item)

    async def name_dialog(self, item=None):
        field = ft.TextField(label="名称", value=item.name if item else "", autofocus=True, text_size=14, max_length=200)
        error = self.text("", 12, self.c["danger"])
        async def save(e):
            try:
                if item:
                    await self.provider.rename(item.id, field.value or "")
                else:
                    await self.provider.create_folder(self.nav.current, field.value or "")
            except (ValueError, OSError) as exc:
                error.value = str(exc) if isinstance(exc, ValueError) else "无法保存，请检查目录写入权限"
                error.update()
                return
            self.page.pop_dialog()
            self.selected.clear()
            await self.load_files()
            self.toast("已更新示例文件" if item else "已创建示例文件夹")
        field.on_submit = save
        self.page.show_dialog(ft.AlertDialog(modal=True, title=self.text("重命名" if item else "新建文件夹", 19, weight=ft.FontWeight.W_600), content=ft.Container(ft.Column([field, error, self.small("此操作仅修改本地示例空间。")], tight=True, spacing=12), width=390), actions=[ft.TextButton(content="取消", on_click=self.close_dialog), ft.Button(content="保存" if item else "创建", on_click=save)]))

    async def delete_selected(self, e):
        ids = list(self.selected)
        async def confirm(e):
            try:
                await self.provider.delete(ids)
            except OSError:
                self.page.pop_dialog()
                self.toast("保存失败，请检查目录权限")
                return
            self.page.pop_dialog()
            self.selected.clear()
            await self.load_files()
            self.toast("已从示例空间移除")
        self.page.show_dialog(ft.AlertDialog(modal=True, title=self.text(f"删除这 {len(ids)} 个示例项目？", 18, weight=ft.FontWeight.W_600), content=self.text("文件夹中的示例内容也会移除。\n此操作不会影响真实网盘。", 13, self.c["muted"]), actions=[ft.TextButton(content="取消", on_click=self.close_dialog), ft.TextButton(content="删除示例", style=ft.ButtonStyle(color=self.c["danger"]), on_click=confirm)]))

    async def download_selected(self, e):
        await self.enqueue([f for f in self.files.items if f.id in self.selected])

    async def enqueue(self, files):
        added = self.workspace.enqueue(files)
        await self.workspace.save()
        self.selected.clear()
        self.render_page()
        self.toast(f"已加入 {added} 个演示任务，可在传输中心查看" if added else "请选择文件；重复任务和文件夹不会加入演示队列")
