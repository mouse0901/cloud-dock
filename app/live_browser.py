"""Shared account file browser; selection is restricted to the loaded page."""
from functools import partial
from pathlib import PurePosixPath
import flet as ft
from app.domain import human_size
from app.live_upload_panel import LiveUploadMixin


class LiveBrowserMixin(LiveUploadMixin):
    def build_live_browser(self, title, account_extra=None, extra=None, header_actions=None):
        self.init_live_uploads()
        self.status.size = 12
        self.browser_rows = []
        self.browser_selected = set()
        self.browser_busy = False
        self.account_details_open = False
        self.browser_search = ft.TextField(hint_text="筛选本页文件", prefix_icon=ft.Icons.SEARCH_ROUNDED,
            expand=True, height=42, text_size=13, on_change=self.filter_browser)
        self.browser_type = ft.Dropdown(value="all", width=130, text_size=12,
            options=[ft.DropdownOption(k,v) for k,v in [("all","全部类型"),("folder","文件夹"),
                ("document","文档"),("image","图片"),("video","视频"),("other","其他")]],
            on_select=self.filter_browser)
        self.browser_sort = ft.Dropdown(value="name", width=145, text_size=12,
            options=[ft.DropdownOption("name","名称排序"),ft.DropdownOption("size","大小从大到小")],
            on_select=self.filter_browser)
        self.browser_all = ft.Checkbox(label="全选本页文件", on_change=self.select_browser_all)
        self.browser_count = ft.Text("",size=12,expand=True)
        self.browser_download = ft.Button(content="下载所选",icon=ft.Icons.DOWNLOAD_ROUNDED,
            on_click=self.download_browser_selection,disabled=True)
        self.browser_crumbs = ft.Row(spacing=0,wrap=True)
        self.account_box = ft.Column([self.qr,*(account_extra or []),self.remember_login,self.session_notice,
            ft.Row([self.begin_button,self.retry_button,self.cancel_button],wrap=True)],spacing=10)
        return ft.Column([
            ft.Row([ft.Text(title,size=26,weight=ft.FontWeight.W_700,expand=True),self.browser_upload,*(header_actions or []),
                ft.TextButton(content="管理账号",icon=ft.Icons.PERSON_OUTLINED,on_click=self.toggle_account)],spacing=12),
            self.status,self.account_box,*(extra or []),self.results,
        ],spacing=10,scroll=ft.ScrollMode.AUTO,expand=True)

    def update(self):
        if hasattr(self,'account_box'):
            connected = self.reader is not None
            self.browser_upload.disabled = not self.upload_allowed()
            self.account_box.visible = not connected or self.account_details_open
            if not connected and hasattr(self,'browser_selected'):
                self.browser_rows = []
                self.browser_selected.clear()
        super().update()

    async def toggle_account(self,e=None):
        self.account_details_open = not self.account_details_open
        self.update()

    def set_directory_busy(self,value):
        super().set_directory_busy(value)
        self.browser_busy = value
        self.browser_upload.disabled = not self.upload_allowed()
        if value:
            self.browser_rows = []
            self.browser_selected.clear()
            self.browser_all.value = False
            self.browser_count.value = "正在读取目录…"
        for control in (self.browser_search,self.browser_type,self.browser_sort,self.browser_all):
            control.disabled = value
        self.browser_download.disabled = value or not self.browser_selected

    def directory_controls(self):
        root = self.reader.root
        path = PurePosixPath(self.path)
        crumbs = [ft.TextButton(content="全部文件" if getattr(self,'share',None) is None else "分享文件",
                               on_click=partial(self.open_folder,root))]
        relative = path.relative_to(PurePosixPath(root))
        current = PurePosixPath(root)
        for part in relative.parts:
            current = current / part
            crumbs.extend([ft.Text("/",size=12),ft.TextButton(content=part,
                          on_click=partial(self.open_folder,str(current)))])
        self.browser_crumbs.controls = crumbs
        return [self.browser_crumbs,
            ft.Row([self.browser_search,self.browser_type,self.browser_sort],spacing=10),
            ft.Row([self.browser_all,self.browser_count,self.browser_download],spacing=10),
            ft.Row([self.parent_button,self.refresh_button,ft.Container(expand=True),
                    self.prev_button,self.page_text,self.next_button],spacing=3),
            self.files,*self.upload_controls(),*self.download_controls()]

    def filtered_browser_rows(self):
        query = (self.browser_search.value or "").strip().casefold()
        category = self.browser_type.value
        rows = [item for item in self.browser_rows if query in item.name.casefold()
                and (category == "all" or item.category == category)]
        return sorted(rows,key=lambda item:(not item.is_dir,
            -item.size if self.browser_sort.value == "size" else item.name.casefold(),item.name.casefold()))

    def render_directory_items(self,rows):
        self.browser_rows = list(rows)
        self.render_browser_rows()

    def render_browser_rows(self):
        rows = self.filtered_browser_rows()
        selectable = {item.id for item in rows if not item.is_dir}
        self.browser_selected.intersection_update(selectable)
        self.browser_all.value = bool(selectable) and self.browser_selected == selectable
        self.browser_all.disabled = self.browser_busy or not selectable
        self.browser_count.value = f"显示 {len(rows)} / {len(self.browser_rows)} 项 · 已选 {len(self.browser_selected)} 个文件"
        self.browser_download.disabled = self.browser_busy or not self.browser_selected
        self.files.controls = []
        for item in rows:
            icon = ft.Icons.FOLDER_OUTLINED if item.is_dir else {"image":ft.Icons.IMAGE_OUTLINED,
                "video":ft.Icons.MOVIE_OUTLINED,"document":ft.Icons.DESCRIPTION_OUTLINED}.get(item.category,ft.Icons.INSERT_DRIVE_FILE_OUTLINED)
            leading = ft.Row([ft.Container(width=36) if item.is_dir else ft.Checkbox(
                value=item.id in self.browser_selected,on_change=partial(self.select_browser_item,item)),
                ft.Icon(icon,color="#6D91C5" if item.is_dir else None)],tight=True,spacing=6)
            self.files.controls.append(ft.ListTile(leading=leading,dense=True,
                title=ft.Text(item.name,size=13,max_lines=1,overflow=ft.TextOverflow.ELLIPSIS),
                subtitle=ft.Text("文件夹" if item.is_dir else human_size(item.size),size=11),
                selected=item.id in self.browser_selected,
                trailing=None if item.is_dir else ft.IconButton(ft.Icons.DOWNLOAD_ROUNDED,
                    tooltip="下载到本机",on_click=partial(self.start_download,item)),
                on_click=partial(self.open_folder,item.path) if item.is_dir else partial(self.select_browser_item,item)))
        if not rows:
            self.files.controls = [ft.Container(ft.Text("本页没有匹配的文件" if self.browser_rows else "这个文件夹是空的",size=13),padding=24)]

    async def filter_browser(self,e=None):
        self.render_browser_rows()
        self.update()

    async def select_browser_item(self,item,e=None):
        if self.browser_busy or item.is_dir or item not in self.browser_rows:
            return
        if item.id in self.browser_selected:
            self.browser_selected.remove(item.id)
        else:
            self.browser_selected.add(item.id)
        self.render_browser_rows()
        self.update()

    async def select_browser_all(self,e=None):
        if self.browser_busy: return
        ids = {item.id for item in self.filtered_browser_rows() if not item.is_dir}
        self.browser_selected = set() if ids == self.browser_selected else ids
        self.render_browser_rows()
        self.update()

    async def download_browser_selection(self,e=None):
        if self.browser_busy: return
        reader,generation = self.reader,self.generation
        items = [item for item in self.filtered_browser_rows() if item.id in self.browser_selected and not item.is_dir]
        before = len(self.jobs)
        for item in items:
            if self.reader is not reader or self.generation != generation or self.closed: break
            await self.start_download(item)
        if self.reader is not reader or self.generation != generation or self.closed: return
        self.browser_selected.clear()
        self.render_browser_rows()
        added = len(self.jobs)-before
        self.download_status.value = f"已加入 {added} 个下载任务；可在传输中心查看。" if added else "这些文件已在下载队列中。"
        self._downloads_changed()
