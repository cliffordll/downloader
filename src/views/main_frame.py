import json
from pathlib import Path

import wx 
import wx.dataview as dv
from src.models.tree_model import MultiColumnTreeModel, EVT_ALL_DOWNLOAD

from src.views.downloads.dialog_mu import DownloadDialogMU
from src.views.downloads.dialog_ts import DownloadDialogTS


from src.managers.file_manager import FileManager
from src.managers.downloader import Downloader
from src.managers.converter import Converter
from src.managers.sys_setting import SysSetting
from src.managers.path_manager import PathManager

ICON_ROOT = Path(__file__).resolve().parents[2] / "icons"
ICON_FILES = {
    "open": "tools/open.png",
    "playlist": "files/m3u8.png",
    "segment": "files/ts.png",
    "expand": "tools/expand.png",
    "collapse": "tools/collapse.png",
    "refresh": "tools/refresh.png",
    "help": "tools/help.png",
    "about": "tools/about.png",
}
ICON_SIZES = (24, 30, 36, 48)


def icon_image(filename, size):
    """Load an exact-size PNG, or scale the shared source for other sizes."""
    path = ICON_ROOT / str(size) / filename
    if not path.is_file():
        path = ICON_ROOT / "source" / filename
    image = wx.Image(str(path), wx.BITMAP_TYPE_PNG)
    if not image.IsOk():
        raise ValueError(f"Cannot load icon: {path}")
    if image.GetSize() != wx.Size(size, size):
        image = image.Scale(size, size, wx.IMAGE_QUALITY_HIGH)
    return image


def toolbar_icon(name):
    filename = ICON_FILES[name]
    return wx.BitmapBundle.FromBitmaps([
        wx.Bitmap(icon_image(filename, size)) for size in ICON_SIZES
    ])


class TaskActionRenderer(dv.DataViewCustomRenderer):
    """Render a compact text action, aligned consistently across rows."""
    def __init__(self, frame):
        super().__init__('string', dv.DATAVIEW_CELL_ACTIVATABLE, wx.ALIGN_CENTER)
        self.frame = frame
        self.label = ''

    def SetValue(self, value):
        self.label = value
        return True

    def GetValue(self):
        return self.label

    def GetSize(self):
        return self.frame.FromDIP(wx.Size(152, 24))

    def _ActionRects(self, cell):
        rect = wx.Rect(cell)
        rect.Deflate(self.frame.FromDIP(4), 0)
        return [wx.Rect(rect.x + rect.width * i // 4, rect.y,
                        rect.width * (i + 1) // 4 - rect.width * i // 4, rect.height)
                for i in range(4)]

    def Render(self, cell, dc, state):
        if not self.label:
            return True
        font = wx.Font(self.frame.mcTree.GetFont())
        font.SetWeight(wx.FONTWEIGHT_NORMAL)
        dc.SetFont(font)
        colour = (wx.SYS_COLOUR_HIGHLIGHTTEXT if state & dv.DATAVIEW_CELL_SELECTED
                  else wx.SYS_COLOUR_HOTLIGHT)
        if self.label.startswith('['):
            for action, rect in zip(json.loads(self.label), self._ActionRects(cell)):
                dc.SetTextForeground(wx.SystemSettings.GetColour(
                    colour if action['enabled'] else wx.SYS_COLOUR_GRAYTEXT))
                dc.DrawLabel(action['label'], rect, wx.ALIGN_CENTER)
        else:
            dc.SetTextForeground(wx.SystemSettings.GetColour(colour))
            dc.DrawLabel(self.label, cell, wx.ALIGN_CENTER)
        return True

    def ActivateCell(self, cell, model, item, col, mouseEvent):
        value = model.GetValue(item, col)
        if not value:
            return False
        if model.IsContainer(item):
            actions = json.loads(value)
            if mouseEvent is None:
                # Enter defaults to download, or More; never delete via a default action.
                action = actions[0] if actions[0]['enabled'] else actions[3]
            else:
                point = mouseEvent.GetPosition()  # Relative to the cell.
                rects = self._ActionRects(wx.Rect(0, 0, cell.width, cell.height))
                action = next((entry for entry, rect in zip(actions, rects) if rect.Contains(point)), None)
            if action is None or not action['enabled']:
                return False
            self.frame.OnTaskAction(item, action['id'])
        else:
            event = dv.DataViewEvent(dv.wxEVT_DATAVIEW_ITEM_ACTIVATED, self.frame.mcTree, item)
            self.frame.OnActivatedChanged(event)
        return True


class MainFrame(wx.Frame):
    def __init__(self, parent, title):
        # super(MyFrame, self).__init__(parent, title=title)
        super().__init__(parent, title=title)
        self.SetSize(width=1024, height=700)
        self.SetMinSize(self.FromDIP(wx.Size(780, 420)))
        
        # 先加载 PNG/JPG，再转为 ICO， 调整尺寸（建议32x32或16x16）
        image = icon_image("app/logo.png", 32)
        icon = wx.Icon(wx.Bitmap(image))
        # icon = wx.Icon()
        # icon.CopyFromBitmap(wx.Bitmap(image))
        self.SetIcon(icon)

        self._createMenuBar()
        self._createToolBar()
        self._createStatusBar()

        self._createMainPanel()
        self._task_display = {}
        self._progressTimer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.OnTaskProgress, self._progressTimer)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.OnDestroy)
        self._progressTimer.Start(500)

        self.Center()
        self.Show()

    def _createMenuBar(self):
        # 创建菜单栏
        self.menuBar = wx.MenuBar()
        # 创建文件菜单
        fileMenu = wx.Menu()
        openItem    = fileMenu.Append(wx.ID_OPEN, "&打开\tCtrl-O")
        fileMenu.AppendSeparator()
        addMUItem   = fileMenu.Append(wx.ID_ANY, "&下载M3U8\tCtrl-M")
        addTSItem   = fileMenu.Append(wx.ID_ANY, "&下载TS\tCtrl-T")
        pauseItem = fileMenu.Append(wx.ID_ANY, "全部暂停")
        self.Bind(wx.EVT_MENU, self.OnPauseDownloads, pauseItem)
        self.Bind(wx.EVT_UPDATE_UI, self.OnUpdatePauseDownloads, pauseItem)
        fileMenu.AppendSeparator()
        # importM3U8  = fileMenu.Append(wx.ID_ANY, "&导入M3U8\tCtrl-D")
        # fileMenu.AppendSeparator()
        expandItem  = fileMenu.Append(wx.ID_ANY, "展开全部")
        collapseItem = fileMenu.Append(wx.ID_ANY, "折叠全部")
        refeshItem  = fileMenu.Append(wx.ID_REFRESH, "刷新")
        fileMenu.AppendSeparator()
        settingItem  = fileMenu.Append(wx.ID_ANY, "设置\tCtrl-,")
        exitItem    = fileMenu.Append(wx.ID_EXIT, "&退出")
        # 绑定事件
        self.Bind(wx.EVT_MENU, self.OnOpen, openItem)
        self.Bind(wx.EVT_MENU, self.OnAddMU, addMUItem)
        self.Bind(wx.EVT_MENU, self.OnAddTS, addTSItem)
        self.Bind(wx.EVT_MENU, self.OnExpandAll, expandItem)
        self.Bind(wx.EVT_MENU, self.OnCollapseAll, collapseItem)
        self.Bind(wx.EVT_MENU, self.OnRefresh, refeshItem)
        self.Bind(wx.EVT_MENU, self.OnSetting, settingItem)
        self.Bind(wx.EVT_MENU, self.OnExit, exitItem)
		
        # 创建编辑菜单
        viewMenu = wx.Menu()
        self.showToolItem   = viewMenu.Append(wx.ID_ANY, "显示工具栏", kind=wx.ITEM_CHECK)
        self.showStatusItem = viewMenu.Append(wx.ID_ANY, "显示状态栏", kind=wx.ITEM_CHECK)
        self.Bind(wx.EVT_MENU, self.OnToggleToolBar, self.showToolItem)
        self.Bind(wx.EVT_MENU, self.OnToggleStatusBar, self.showStatusItem)

        # 创建关于菜单
        aboutMenu = wx.Menu()
        helpItem    = aboutMenu.Append(wx.ID_ANY, "帮助")
        aboutItem   = aboutMenu.Append(wx.ID_ANY, "关于")
        self.Bind(wx.EVT_MENU, self.OnHelp, helpItem)
        self.Bind(wx.EVT_MENU, self.OnAbout, aboutItem)

        # 将文件菜单添加到菜单栏
        self.menuBar.Append(fileMenu, "&文件")
        self.menuBar.Append(viewMenu, "&查看")
        self.menuBar.Append(aboutMenu, "&帮助")
        # 设置菜单栏
        self.SetMenuBar(self.menuBar)

    def _createToolBar(self):
        self.toolBar = self.CreateToolBar(style=wx.TB_DEFAULT_STYLE)
        self.toolBar.SetToolBitmapSize(self.toolBar.FromDIP(wx.Size(24, 24)))
        self.toolBar.SetToolPacking(self.toolBar.FromDIP(4))
        self.toolBar.SetToolSeparation(self.toolBar.FromDIP(8))
        self.toolBar.SetMargins(self.toolBar.FromDIP(wx.Size(4, 3)))

        def add_tool(tool_id, label, icon):
            bundle = toolbar_icon(icon)
            return self.toolBar.AddTool(tool_id, label, bundle, shortHelp=label)

        openButton = add_tool(wx.ID_OPEN, "打开", "open")
        muButton = add_tool(wx.ID_ANY, "下载M3U8", "playlist")
        tsButton = add_tool(wx.ID_ANY, "下载TS", "segment")
        self.toolBar.AddSeparator()
        expandButton = add_tool(wx.ID_ANY, "展开全部", "expand")
        collapseButton = add_tool(wx.ID_ANY, "折叠全部", "collapse")
        refreshButton = add_tool(wx.ID_ANY, "刷新", "refresh")
        self.toolBar.AddSeparator()
        helpButton = add_tool(wx.ID_ANY, "帮助", "help")
        aboutButton = add_tool(wx.ID_ANY, "关于", "about")

        # self.toolBar.Bind(wx.EVT_TOOL, self.OnNew, newButton)
        # self.toolBar.Bind(wx.EVT_TOOL, self.OnOpen, openButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddMU, muButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddTS, tsButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnExpandAll, expandButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnCollapseAll, collapseButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnRefresh, refreshButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnHelp, helpButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAbout, aboutButton)
        # 启用工具栏
        self.toolBar.Realize()
        self.showToolItem.Check(self.toolBar.IsShown())

    def _createStatusBar(self):
        self.statusBar = self.CreateStatusBar()
        self.statusBar.SetFieldsCount(2)
        self.statusBar.SetStatusWidths([-1, -3])
        self.statusBar.SetStatusText('就绪', 0)
        self.statusBar.SetStatusText('双击任务或分片行执行“操作”列中的操作', 1)
        self.showStatusItem.Check(self.statusBar.IsShown())

    def _createMainPanel(self):
        """创建主面板和布局"""
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # 首航布局
        uriSizer = wx.BoxSizer(wx.HORIZONTAL)
        bxSearch = wx.SearchCtrl(panel)
        btnExpand = wx.Button(panel, label="全部展开")
        btnCollapse = wx.Button(panel, label="全部折叠")
        btnRefresh = wx.Button(panel, label="刷新")
        btnPause = wx.Button(panel, label="全部暂停")
        btnPause.SetToolTip('暂停全部分片下载；已发出的请求允许完成，排队任务保留。')
        uriSizer.Add(bxSearch, proportion=50, flag=wx.EXPAND|wx.TOP|wx.BOTTOM|wx.RIGHT, border=5)
        uriSizer.Add(btnExpand, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)
        uriSizer.Add(btnCollapse, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)
        uriSizer.Add(btnRefresh, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)
        uriSizer.Add(btnPause, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)
        
        bxSearch.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self.OnSearch)
        bxSearch.Bind(wx.EVT_TEXT, self.OnSearchText)
        btnExpand.Bind(wx.EVT_BUTTON, self.OnExpandAll)
        btnCollapse.Bind(wx.EVT_BUTTON, self.OnCollapseAll)
        btnRefresh.Bind(wx.EVT_BUTTON, self.OnRefresh)
        btnPause.Bind(wx.EVT_BUTTON, self.OnPauseDownloads)
        btnPause.Bind(wx.EVT_UPDATE_UI, self.OnUpdatePauseDownloads)

        # 多列树布局
        # self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_READONLY|wx.TE_RICH2)
        listSizer = wx.BoxSizer(wx.HORIZONTAL)
        # 创建并关联模型
        self.model = MultiColumnTreeModel(self)
        # 创建DataViewCtrl
        self.mcTree = dv.DataViewCtrl(panel, -1, style=wx.BORDER_THEME|dv.DV_ROW_LINES|dv.DV_VERT_RULES|dv.DV_VARIABLE_LINE_HEIGHT|dv.DV_ROW_LINES)
        self.mcTree.AssociateModel(self.model)
        # 添加多列
        self.mcTree.AppendTextColumn("序列", 0, width=60)
        # # 自定义列
        # renderer = dv.DataViewTextRenderer()
        # renderer.EnableEllipsize(wx.ELLIPSIZE_END)
        # self.mcTree.AppendColumn(dv.DataViewColumn("文件名", renderer, 1, width=180, align=wx.ALIGN_LEFT))
        # self.mcTree.AppendTextColumn("文件名", 1, width=500)
        self.mcTree.AppendTextColumn("文件名", 1, width=250)
        self.mcTree.AppendTextColumn("下载进度", 5, width=140)
        self.mcTree.AppendTextColumn("状态", 6, width=140)
        self.mcTree.AppendTextColumn("文件大小", 2, width=90, align=wx.ALIGN_RIGHT)
        self.mcTree.AppendTextColumn("修改时间", 3, width=130)
        self.mcTree.AppendColumn(dv.DataViewColumn("操作", TaskActionRenderer(self), 4,
                                                 width=self.FromDIP(160), align=wx.ALIGN_CENTER))
        for index in range(self.mcTree.GetColumnCount()):
            column = self.mcTree.GetColumn(index)
            column.GetRenderer().EnableEllipsize(wx.ELLIPSIZE_END)
            column.SetFlags(column.GetFlags() & ~wx.COL_RESIZABLE)
        self.mcTree.Bind(wx.EVT_SIZE, self.OnTaskListSize)
        # self.mcTree.AppendTextColumn("下载地址", 5)
        # self.model.DecRef()  # 避免内存泄漏
        self.OnExpandAll(None)
        listSizer.Add(self.mcTree, proportion=10, flag=wx.EXPAND|wx.TOP, border=5)
        # listSizer.Add(self.mulist, proportion=10, flag=wx.EXPAND|wx.ALL, border=5)
        # self.list.SetBackgroundColour(wx.RED)

        # 双击下载
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_ACTIVATED, self.OnActivatedChanged)
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_CONTEXT_MENU, self.OnTaskContextMenu)
        self.Bind(EVT_ALL_DOWNLOAD, self.OnAllTSDownload)

        sizer.Add(uriSizer, flag=wx.EXPAND, border=0)
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND|wx.ALL, border=0)
        # 设置面板的sizer
        panel.SetSizer(sizer)
        wx.CallAfter(self._FitTaskColumns)

    def OnTaskListSize(self, event):
        event.Skip()
        if not getattr(self, '_columnFitPending', False):
            self._columnFitPending = True
            wx.CallAfter(self._FitTaskColumns)

    def _FitTaskColumns(self):
        self._columnFitPending = False
        if not self or not self.mcTree:
            return
        size_key = (self.mcTree.GetSize().width, self.FromDIP(100))
        if getattr(self, '_columnSizeKey', None) == size_key:
            return
        # Reserve space for the vertical scrollbar, including when it appears later.
        available = (self.mcTree.GetSize().width
                     - wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X, self.mcTree)
                     - self.FromDIP(4))
        if available <= 0:
            return
        self._columnSizeKey = size_key
        widths = [self.FromDIP(value) for value in (50, 0, 110, 115, 85, 125, 160)]
        widths[1] = max(1, available - sum(widths))
        if sum(widths) > available:
            # Also handle transient small sizes during window creation.
            total = sum(widths)
            widths = [max(1, value * available // total) for value in widths]
        self.mcTree.Freeze()
        try:
            for index, width in enumerate(widths):
                column = self.mcTree.GetColumn(index)
                if column.GetWidth() != width:
                    column.SetWidth(width)
        finally:
            self.mcTree.Thaw()

    def OnDestroy(self, event):
        if event.GetEventObject() is self:
            self._progressTimer.Stop()
        event.Skip()

    def OnTaskProgress(self, event):
        display = {}
        for index, task in enumerate(self.model.fileTree.items):
            info = self.model.TaskInfo(index)
            state = (info['progress'], info['status'], json.dumps(self.model.TaskActions(index), ensure_ascii=False))
            display[task.parent.fileName] = state
            if self._task_display.get(task.parent.fileName) != state:
                self.model.ItemChanged(self.model.ObjectToItem(self.model._BuildKey((index,))))
        self._task_display = display

    def OnTaskAction(self, item, action_id='start'):
        if not item.IsOk():
            return
        keys = self.model.ParseKey(self.model.ItemToObject(item))
        if len(keys) != 1:
            return
        index = keys[0]
        task = self.model.fileTree.items[index]
        info = self.model.TaskInfo(index)
        if action_id == 'merge':
            if info['pending'] or info['merging'] or task.outputs or not info['total'] or info['done'] != info['total']:
                return
            self._CreateMP4File(task.parent.fileName, item)
        else:
            action = next((entry for entry in self.model.TaskActions(index) if entry['id'] == action_id), None)
            if action is None or not action['enabled']:
                return
            if action_id == 'more':
                self.OnTaskMenu(item)
                return
            if action_id == 'delete':
                self.OnDeleteTask(task)
                return
        if action_id in ('start', 'retry'):
            tasks = []
            for segment_index, child in enumerate(task.childs):
                key = Downloader.FileKey(PathManager.GetAbsPath(child.fileName))
                if child.fileSize == '-' and (action_id != 'retry' or key in info['failed']):
                    child_item = self.model.ObjectToItem(self.model._BuildKey(
                        (index, len(task.outputs) + segment_index)))
                    tasks.append((segment_index, child_item))
            self._DownloadFiles(task.parent.fileName, tasks)
        self.model.ItemChanged(item)

    def OnTaskContextMenu(self, event):
        self.OnTaskMenu(event.GetItem())

    def _OpenLocalPath(self, path):
        target = Path(path)
        if not target.exists():
            wx.MessageBox('文件或目录已不存在，请刷新列表。', '提示', parent=self)
            return
        if not wx.LaunchDefaultApplication(str(target)):
            wx.MessageBox('无法打开，请检查系统的默认应用设置。', '提示', parent=self)

    def OnTaskMenu(self, item):
        if not item.IsOk():
            return
        index = self.model.ParseKey(self.model.ItemToObject(item))[0]
        task = self.model.fileTree.items[index]
        directory = Path(PathManager.GetAbsPath(task.parent.fileName)).parent
        menu = wx.Menu()

        def add(label, callback, enabled=True):
            entry = menu.Append(wx.ID_ANY, label)
            entry.Enable(enabled)
            menu.Bind(wx.EVT_MENU, lambda event: callback(), entry)

        info = self.model.TaskInfo(index)
        parent_item = self.model.ObjectToItem(self.model._BuildKey((index,)))
        add('转 MP4', lambda: self.OnTaskAction(parent_item, 'merge'),
            bool(info['total']) and info['done'] == info['total'] and not task.outputs
            and not info['pending'] and not info['merging'])
        add('打开文件夹', lambda: self._OpenLocalPath(directory))
        if task.outputs:
            for output in task.outputs:
                path = PathManager.GetAbsPath(output.fileName)
                add('播放视频：' + Path(path).name, lambda path=path: self._OpenLocalPath(path))
        else:
            add('播放视频（尚未生成）', lambda: None, False)
        try:
            self.mcTree.PopupMenu(menu)
        finally:
            menu.Destroy()

    def OnDeleteTask(self, task):
        try:
            directory = FileManager.TaskDeletionDirectory(task.parent.fileName)
        except (ValueError, OSError) as error:
            wx.MessageBox(str(error), '无法删除', wx.OK | wx.ICON_WARNING, parent=self)
            return
        dialog = wx.MessageDialog(self, '', '删除任务及文件',
                                  wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        dialog.SetExtendedMessage(f'将永久删除以下任务文件夹及其中全部文件：\n\n{directory}\n\n'
                                  '包括播放列表、已下载分片、MP4 和该目录中的其他文件。此操作无法撤销。')
        dialog.SetYesNoLabels('删除', '取消')
        try:
            if dialog.ShowModal() != wx.ID_YES:
                return
        finally:
            dialog.Destroy()
        try:
            FileManager.DeleteTaskDirectory(task.parent.fileName, directory)
            self.model.merge_failed.discard(task.parent.fileName)
        except (ValueError, OSError) as error:
            wx.MessageBox(f'删除未完成：{error}', '无法删除', wx.OK | wx.ICON_WARNING, parent=self)
        finally:
            self.OnRefresh(None)

    ###################################
    ### 事件所需函数
    ###################################
    def _FindItem(self, parent, search_text):
        """递归查找匹配项"""
        child, cookie = self.model.GetFirstChild(parent)
        while child.IsOk():
            # 检查当前项
            for col in range(self.model.GetColumnCount()):
                value = self.model.GetValue(child, col).lower()
                if search_text in value:
                    return child
            # 如果是容器，递归检查子项
            if self.model.IsContainer(child):
                found_in_child = self._FindItem(child, search_text)
                if found_in_child.IsOk():
                    return found_in_child
            child, cookie = self.model.GetNextChild(parent, cookie)
        return dv.NullDataViewItem
    
    def _SearchItems(self, text):
        """搜索匹配项"""
        if not text:
            return
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        found_item = self._FindItem(root, text.lower())
        if found_item.IsOk():
            self.mcTree.Select(found_item)
            self.mcTree.EnsureVisible(found_item)

    def _RecursiveExpand(self, item, expand):
        """递归展开/折叠"""
        child, cookie = self.model.GetFirstChild(item)
        while child.IsOk():            
            if self.model.IsContainer(child):
                self.mcTree.Expand(child) if expand else self.mcTree.Collapse(child)
            else:
                self._RecursiveExpand(child, expand)
            child, cookie = self.model.GetNextChild(item, cookie)

    def _RefreshWithState(self):
        """保存当前所有展开状态, 并刷新视图"""
        expandeds = set()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        # 保存所有展开状态
        self._SaveExpandState(root, expandeds)

        # 刷新视图（默认折叠）
        self.model.Cleared()

        # 恢复所有展开状态
        self._RestoreExpandState(root, expandeds)

    def _SaveExpandState(self, parent, expandeds):
        """递归保存展开状态"""
        child, cookie = self.model.GetFirstChild(parent)
        while child.IsOk():            
            if self.model.IsContainer(child):
                if self.mcTree.IsExpanded(child):
                    obj = self.model.ItemToObject(child)
                    expandeds.add(obj)
            else:
                self._SaveExpandState(child, expandeds)
            child, cookie = self.model.GetNextChild(parent, cookie)

    def _RestoreExpandState(self, parent, expandeds):
        """根据上一次展开折叠状态，递归展开或折叠"""
        child, cookie = self.model.GetFirstChild(parent)
        while child.IsOk():            
            if self.model.IsContainer(child):
                obj = self.model.ItemToObject(child)
                if obj in expandeds:
                    self.mcTree.Expand(child) 
                # else:
                #     self.mcTree.Collapse(child)
            else:
                self._RestoreExpandState(child)
            child, cookie = self.model.GetNextChild(parent, cookie)


    ###################################
    ### 操作菜单的事件
    ################################### 
    def OnOpen(self, event):
        print("Open action")
    
    def OnAddMU(self, event):
        workPath = SysSetting.GetWorkPath()
        dlg = DownloadDialogMU(None, "M3U8 URI", workPath)
        result = dlg.ShowModal()
        if result == wx.OK:
            # 创建新任务成功，自动刷新页面
            self.OnRefresh(None)
        dlg.Destroy()

    def OnAddTS(self, event):
        workPath = SysSetting.GetWorkPath()
        dlg = DownloadDialogTS(None, "M3U8 TS", workPath)
        result = dlg.ShowModal()
        if result == wx.OK:
            # 创建新任务成功，自动刷新页面
            self.OnRefresh(None)
        dlg.Destroy()
    
    def OnSetting(self, event):
        from src.views.tab_setting import SettingsDialog
        previous = SysSetting.GetWorkPath()
        dlg = SettingsDialog(self)
        try:
            if dlg.ShowModal() == wx.ID_OK and previous != SysSetting.GetWorkPath():
                self.model.fileTree = FileManager.GetFileInfos()
                self.model.Cleared()
                self.OnExpandAll(None)
        finally:
            dlg.Destroy()

    def OnExit(self, event):
        self.Close()

    def OnPauseDownloads(self, event):
        if Downloader.IsPaused():
            Downloader.Resume()
        else:
            Downloader.Pause()
        self.UpdateWindowUI(wx.UPDATE_UI_RECURSE)

    def OnUpdatePauseDownloads(self, event):
        paused = Downloader.IsPaused()
        busy = Downloader.IsBusy()
        event.SetText('全部继续' if paused else '全部暂停')
        event.Enable(paused or busy)
        snapshot = Downloader.Snapshot()
        self.statusBar.SetStatusText(('暂停中' if snapshot['requesting'] else '已暂停') if paused
                                     else ('正在下载' if busy else '就绪'), 0)

    def OnToggleToolBar(self, event):
        '''隐藏展示工具栏'''
        self.toolBar.Show(self.showToolItem.IsChecked())
        self.SendSizeEvent()

    def OnToggleStatusBar(self, event):
        '''隐藏展示状态栏'''
        self.statusBar.Show(self.showStatusItem.IsChecked())
        self.SendSizeEvent()

    def _ShowInformation(self, title, text):
        dlg = wx.MessageDialog(self, '', title, wx.OK | wx.ICON_INFORMATION)
        dlg.SetExtendedMessage(text)
        dlg.SetOKLabel('关闭')
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def OnHelp(self, event):
        self._ShowInformation('使用帮助', (
            '1. 添加任务\n'
            '通过“文件 → 下载M3U8”输入播放列表网址，或通过“下载TS”按分片命名规则创建任务。\n\n'
            '2. 下载与合并\n'
            '任务行固定显示“开始/继续、重试、删除、更多”，不可用的操作会置灰。'
            '“更多”或右键菜单提供转 MP4、播放视频和打开文件夹。删除会确认是否删除任务及本地文件。'
            '进度按已完成分片数计算。\n'
            '双击列表中的任务行下载全部分片，也可双击未下载的分片行单独下载。'
            '全部下载完成后，任务的操作变为“转MP4”，双击即可合并。\n\n'
            '点击“全部暂停”可暂停全部分片任务；已发出的请求允许完成，此时显示“暂停中”。'
            '这些请求结束后显示“已暂停”。点击“全部继续”后接着下载排队分片，暂停不影响 MP4 合并。\n\n'
            '3. 下载设置\n'
            '通过“文件 → 设置”（Ctrl+,）调整保存目录、并发、请求间隔、重试、超时及自动合并。'
            '合并需要 FFmpeg，路径留空时先查找 scripts 目录，再查找系统 PATH。\n\n'
            '4. 界面显示\n'
            '通过“查看”菜单显示或隐藏工具栏、状态栏。'
            '下载窗口内的“？”可查看对应输入框的说明。'
        ))

    def OnAbout(self, event):
        self._ShowInformation('关于视频下载', (
            '视频下载\n\n'
            '支持 M3U8 播放列表、TS 分片下载及 FFmpeg 合并 MP4。\n\n'
            '项目地址：\nhttps://github.com/cliffordll/downloader'
        ))

    ###################################
    ### 操作树得事件
    ###################################
    def OnSearch(self, event):
        """搜索按钮事件"""
        # print("on_search", event.GetString())
        self._SearchItems(event.GetString())
    
    def OnSearchText(self, event):
        """搜索文本变化事件"""
        # print("on_search_text", event.GetString())
        self._SearchItems(event.GetString())

    def OnExpandAll(self, event):
        """展开所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._RecursiveExpand(root, True)
    
    def OnCollapseAll(self, event):
        """折叠所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._RecursiveExpand(root, False)

    def OnRefresh(self, event):
        # 完全重置数据
        self.model.fileTree = FileManager.GetFileInfos()

        # 方法1：刷新全部，默认折叠
        # # 不需要调用 ValueChanged()
        # # 因为 Cleared() 已经通知视图重新加载数据
        # self.model.Cleared()

        # 方法2：记录上一次的展开折叠状态
        self._RefreshWithState()

    def OnActivatedChanged(self, event):
        """选中项变化事件"""
        column = event.GetDataViewColumn()
        if column is not None and column.GetModelColumn() == 4:
            return  # The custom renderer handles action cells on a single click.
        item = event.GetItem()
        if not item.IsOk():
            return
        value = self.model.GetValue(item, 4)
        if not value:
            return

        # 解析索引
        keys = self.model.ItemToObject(item)
        objs = self.model.ParseKey(keys)
        if len(objs) == 1:
            self.OnTaskAction(item)
            return
        elif len(objs) == 2:    # 子节点
            parent = self.model.GetParent(item)
            if parent.IsOk():
                idxj = objs[1] - len(self.model.fileTree.items[objs[0]].outputs)
                if idxj < 0:
                    return
                # tsName = self.model.GetValue(item, 1)
                tsSeed = self.model.GetValue(parent, 1)
                # # dlg = wx.MessageBox(f"是否下载{tsName}文件。", "提示", style=wx.ICON_QUESTION)
                # dlg = wx.MessageBox(f"是否下载{tsName}文件。", "提示", style=wx.OK|wx.ICON_QUESTION)
                # if dlg != wx.ID_OK:
                #     return

                count = self._DownloadFiles(tsSeed, [(idxj, item)])
                # wx.MessageBox(f"共需提交{count}个下载任务。", "提示", style=wx.OK|wx.ICON_INFORMATION)
        else:
            pass

    
    def OnAllTSDownload(self, event):
        '''所有TS文件都已经下载完毕，修改操作文本'''
        print("OnAllTSDownload", event.GetData())
        payload = event.GetData()
        if not payload:
            return
        fileName = payload.get("fileName", "")
        # print("OnAllTSDownload", fileName)
        self._CreateM3U8File(tsSeed=fileName)

        if SysSetting.GetAll()['auto_merge']:
            for index, task in enumerate(self.model.fileTree.items):
                if task.parent.fileName == fileName and not task.outputs:
                    item = self.model.ObjectToItem(self.model._BuildKey((index,)))
                    self._CreateMP4File(fileName, item)
                    break

        # 方法2：记录上一次的展开折叠状态
        self._RefreshWithState()

    def _DownloadCall(self, flag: bool, fileName: str, item):
        if flag:
            flag, fileItem = FileManager.GetFileItem(fileName)
            if not flag:
                return
            key = Downloader.FileKey(fileName)
            for index, task in enumerate(self.model.fileTree.items):
                for segment_index, child in enumerate(task.childs):
                    if Downloader.FileKey(PathManager.GetAbsPath(child.fileName)) == key:
                        current = self.model.ObjectToItem(self.model._BuildKey(
                            (index, len(task.outputs) + segment_index)))
                        self.model.SetValue(variant=fileItem, item=current, col=0)
                        self.model.ItemChanged(current)
                        self.model.ItemChanged(self.model.GetParent(current))

    def _DownloadFiles(self, tsSeed: str, tasks: list):
        '''下载文件并修改视图状态'''
        absSeed = PathManager.GetAbsPath(tsSeed)
        absDir = PathManager.GetAbsDir(absSeed)    # 下载文件路径

        tsList = FileManager.GetSegments(absSeed)
        if not tsList or any(idx < 0 or idx >= len(tsList) for idx, _ in tasks):
            wx.MessageBox("播放列表无效或已发生变化，请刷新后重试。", "提示")
            return 0
        missing_source = False
        count = 0
        for task in tasks:
            idx = task[0]
            item = task[1]
            tsName = tsList[idx].name
            absUri = tsList[idx].absUri

            if not tsName:
                continue
            absFile = PathManager.JoinPath(absDir, tsName)
            # 文件已经存在，返回
            if PathManager.IsExists(absFile):
                continue
            if not absUri:
                missing_source = True
                continue
            PathManager.MakeDirsByFile(absFile)        # 判断最后一层目录是否存在（针对ts uri 有/）

            if Downloader.DownloadTSFile(absUri, absFile, self._DownloadCall, item):
                count += 1
        if missing_source:
            wx.MessageBox("部分分片缺少下载地址，请提供完整网址或配套的 seed 文件。", "提示")
        return count
    
    def _CreateM3U8File(self, tsSeed):
        '''创建M3U8文件'''
        absSeed = PathManager.GetAbsPath(tsSeed)
        absDir = PathManager.GetAbsDir(absSeed)    # 下载文件路径

        FileManager.CreateM3U8File(absDir, absSeed)

    def _CreateMP4Call(self, flag: bool, fileName: str, item):
        # A refresh can reorder rows while FFmpeg is running.
        output_dir = Downloader.FileKey(PathManager.GetAbsDir(fileName))
        task = None
        for index, candidate in enumerate(self.model.fileTree.items):
            if Downloader.FileKey(PathManager.GetAbsDir(PathManager.GetAbsPath(candidate.parent.fileName))) == output_dir:
                task = candidate
                item = self.model.ObjectToItem(self.model._BuildKey((index,)))
                break
        if task is None:
            return
        if flag:
            code, newFile = FileManager.GetFileItem(fileName)
            # 插入数据
            if not any(output.fileName == newFile.fileName for output in task.outputs):
                self.model.InsertChildData(item, newFile)
            self.model.merge_failed.discard(task.parent.fileName)
            # 刷新视图
            self._RefreshWithState()
        else:
            self.model.merge_failed.add(task.parent.fileName)
            wx.MessageBox(f"视频文件合并失败", "提示")

    def _CreateMP4File(self, tsSeed: str, item):
        absSeed = PathManager.GetAbsPath(tsSeed)
        absDir = PathManager.GetAbsDir(absSeed)

        # # 2. 确保下载文件一定存在
        playlist = PathManager.JoinPath(absDir, "playlist.txt")
        outputFile = PathManager.JoinPath(absDir, "output.mp4")

        # 文件已经存在，返回
        if PathManager.IsExists(outputFile):
            wx.MessageBox(f"视频文件已经存在。", "提示")
            return

        print("absSeed", absSeed)
        print("absDir", absDir)
        print("playlist", playlist)
        # 写palylist文件
        if not FileManager.CreatePlaylist(absSeed=absSeed, playDir=absDir, playlist=playlist):
            wx.MessageBox("播放列表无效，无法生成合并清单。", "提示")
            return

        self.model.merge_failed.discard(tsSeed)
        Converter.ConvertTSFile(playlist, outputFile, self._CreateMP4Call, item)
