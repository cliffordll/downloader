import json
import sqlite3
from queue import Empty
from pathlib import Path

import wx 
import wx.dataview as dv
from src.models.tree_model import MultiColumnTreeModel, EVT_ALL_DOWNLOAD, load_tree

from src.views.dialogs.m3u8_dialog import DownloadDialogMU
from src.views.dialogs.ts_dialog import DownloadDialogTS
from src.views.dialogs.mp4_dialog import DownloadDialogMP4
from src.views.dialogs.information_dialog import InformationDialog
from src.core.task_runner import TaskRunner


from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.core.sys_setting import SysSetting
from src.core.task_service import TaskService
from src.models.file_base import TreeData
from src.core.path_manager import PathManager
from src.storage.task_repository import TaskDataError, TaskConflictError
from src.schemas.task import TaskStatus, TaskType

from src.views.components.icons import app_icons, icon_image, toolbar_icon, set_titlebar_icon
from src.views.components.renderers import TaskProgressRenderer, TaskActionRenderer


class MainFrame(wx.Frame):
    def __init__(self, parent, title, task_service: TaskService, initial_tree: TreeData):
        # super(MyFrame, self).__init__(parent, title=title)
        super().__init__(parent, title=title)
        self._closing = False
        # 窗口、下载器和添加任务弹窗共享入口创建的服务，列表模型不持有业务服务。
        self.tasks = task_service
        self.SetSize(self.FromDIP((1024, 700)))
        self.SetMinSize(self.FromDIP(wx.Size(1024 if wx.Platform == '__WXMAC__' else 780, 420)))
        
        self.SetIcons(app_icons())
        set_titlebar_icon(self)
        self.Bind(wx.EVT_DPI_CHANGED, self.OnIconDPIChanged)

        self._createMenuBar()
        self._createToolBar()
        self._createStatusBar()

        self._searchText = ''
        self._filterText = ''
        self._searchTimer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.OnSearchTimer, self._searchTimer)
        self._createMainPanel(initial_tree)
        # 记住默认窗口宽度；放大时按默认列比例扩展，恢复窗口时还原布局。
        self._defaultTaskWidth = self.GetClientSize().width
        self._task_display = {}
        self._completion_notified = set()
        self.runner = TaskRunner(self.tasks)
        # 显示前完成布局、列宽和状态缓存，避免先画初始列宽再跳到适配后的宽度。
        self.Layout()
        self.mcTree.GetParent().Layout()
        self._FitTaskColumns()
        self.OnTaskProgress(None, notify=False)
        self._UpdatePauseTool()
        # 初始化完成后再监听尺寸变化；后续缩放仍合并为一次延迟调整。
        self.mcTree.Bind(wx.EVT_SIZE, self.OnTaskListSize)
        self.Bind(wx.EVT_SHOW, self.OnFrameShown)
        self._progressTimer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.OnTaskProgress, self._progressTimer)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.OnDestroy)
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self._progressTimer.Start(500)

        self.Center()
        self.Show()

    def OnIconDPIChanged(self, event):
        event.Skip()
        wx.CallAfter(self._UpdateTitlebarIcon)

    def _UpdateTitlebarIcon(self):
        if not self._closing and self and not self.IsBeingDeleted():
            set_titlebar_icon(self)

    def _createMenuBar(self):
        # 创建菜单栏
        self.menuBar = wx.MenuBar()
        # 创建文件菜单
        fileMenu = wx.Menu()
        openItem    = fileMenu.Append(wx.ID_OPEN, "打开下载文件夹\tCtrl-O")
        fileMenu.AppendSeparator()
        addMUItem   = fileMenu.Append(wx.ID_ANY, "&下载 M3U8\tCtrl-M")
        addTSItem   = fileMenu.Append(wx.ID_ANY, "&下载 TS\tCtrl-T")
        addMP4Item = fileMenu.Append(wx.ID_ANY, '下载 MP4\tCtrl-P')
        fileMenu.AppendSeparator()
        settingItem  = fileMenu.Append(wx.ID_ANY, "设置\tCtrl-,")
        exitItem    = fileMenu.Append(wx.ID_EXIT, "&退出")
        # 绑定事件
        self.Bind(wx.EVT_MENU, self.OnOpen, openItem)
        self.Bind(wx.EVT_MENU, self.OnAddMU, addMUItem)
        self.Bind(wx.EVT_MENU, self.OnAddTS, addTSItem)
        self.Bind(wx.EVT_MENU, self.OnAddMP4, addMP4Item)
        self.Bind(wx.EVT_MENU, self.OnSetting, settingItem)
        self.Bind(wx.EVT_MENU, self.OnExit, exitItem)
		
        # 任务菜单负责下载控制，查看菜单负责列表及界面展示。
        taskMenu = wx.Menu()
        self.pauseAllItem = taskMenu.Append(wx.ID_ANY, '全部暂停\tCtrl-Shift-P')
        self.resumeAllItem = taskMenu.Append(wx.ID_ANY, '全部继续\tCtrl-Shift-R')
        can_pause, can_resume = self._GlobalDownloadActions()
        self.pauseAllItem.Enable(can_pause)
        self.resumeAllItem.Enable(can_resume)
        self.Bind(wx.EVT_MENU, self.OnPauseAllDownloads, self.pauseAllItem)
        self.Bind(wx.EVT_MENU, self.OnResumeAllDownloads, self.resumeAllItem)
        self.Bind(wx.EVT_UPDATE_UI, self.OnUpdateGlobalDownloadAction, self.pauseAllItem)
        self.Bind(wx.EVT_UPDATE_UI, self.OnUpdateGlobalDownloadAction, self.resumeAllItem)

        viewMenu = wx.Menu()
        expandItem = viewMenu.Append(wx.ID_ANY, "全部展开\tCtrl-Shift-E")
        collapseItem = viewMenu.Append(wx.ID_ANY, "全部折叠\tCtrl-Shift-C")
        refreshItem = viewMenu.Append(wx.ID_REFRESH, "刷新\tF5")
        findItem = viewMenu.Append(wx.ID_FIND, "查找\tCtrl-F", '定位到文件名或路径筛选框')
        self.Bind(wx.EVT_MENU, self.OnExpandAll, expandItem)
        self.Bind(wx.EVT_MENU, self.OnCollapseAll, collapseItem)
        self.Bind(wx.EVT_MENU, self.OnRefresh, refreshItem)
        self.Bind(wx.EVT_MENU, self.OnFind, findItem)
        viewMenu.AppendSeparator()
        self.defaultExpandItem = viewMenu.Append(wx.ID_ANY, '默认展开任务',
                                                '新加载任务时应用；不改变当前任务的展开状态', kind=wx.ITEM_CHECK)
        self.defaultExpandItem.Check(SysSetting.GetAll()['default_expand_tasks'])
        self.Bind(wx.EVT_MENU, self.OnDefaultExpandTasks, self.defaultExpandItem)
        viewMenu.AppendSeparator()
        self.showToolItem = None
        if wx.Platform != '__WXMAC__':
            self.showToolItem = viewMenu.Append(wx.ID_ANY, "显示工具栏", kind=wx.ITEM_CHECK)
            self.Bind(wx.EVT_MENU, self.OnToggleToolBar, self.showToolItem)
        self.showStatusItem = viewMenu.Append(wx.ID_ANY, "显示状态栏", kind=wx.ITEM_CHECK)
        self.Bind(wx.EVT_MENU, self.OnToggleStatusBar, self.showStatusItem)

        # 创建关于菜单
        aboutMenu = wx.Menu()
        helpItem    = aboutMenu.Append(wx.ID_ANY, "使用说明\tF1")
        aboutItem   = aboutMenu.Append(wx.ID_ANY, "关于")
        self.Bind(wx.EVT_MENU, self.OnHelp, helpItem)
        self.Bind(wx.EVT_MENU, self.OnAbout, aboutItem)

        # 将文件菜单添加到菜单栏
        self.menuBar.Append(fileMenu, "&文件")
        self.menuBar.Append(taskMenu, "&任务")
        self.menuBar.Append(viewMenu, "&查看")
        self.menuBar.Append(aboutMenu, "&帮助")
        # 设置菜单栏
        self.SetMenuBar(self.menuBar)

    def _createToolBar(self):
        if wx.Platform == '__WXMAC__':
            self.toolBar = None
            return
        self.toolBar = self.CreateToolBar(style=wx.TB_DEFAULT_STYLE)
        # 由 BitmapBundle 按 DPI 选择图标；显式设置尺寸会干扰 wxMSW 的尺寸选择。
        # self.toolBar.SetToolBitmapSize(self.toolBar.FromDIP(wx.Size(24, 24)))


        def add_tool(tool_id, label, icon):
            bundle = toolbar_icon(icon)
            return self.toolBar.AddTool(tool_id, label, bundle, shortHelp=label)

        openButton = add_tool(wx.ID_OPEN, "打开下载文件夹", "open")
        muButton = add_tool(wx.ID_ANY, "下载 M3U8", "playlist")
        tsButton = add_tool(wx.ID_ANY, "下载 TS", "segment")
        mp4Button = add_tool(wx.ID_ANY, '下载 MP4', 'mp4')
        self.toolBar.AddSeparator()
        expandButton = add_tool(wx.ID_ANY, "全部展开", "expand")
        collapseButton = add_tool(wx.ID_ANY, "全部折叠", "collapse")
        self.toolBar.SetToolShortHelp(expandButton.GetId(), '展开当前列表中的全部任务')
        self.toolBar.SetToolShortHelp(collapseButton.GetId(), '折叠当前列表中的全部任务')
        self._pauseTool = add_tool(wx.ID_ANY, "全部暂停", "pause")
        self._pauseIcons = {False: toolbar_icon('pause'), True: toolbar_icon('start')}
        self._pauseToolState = None
        refreshButton = add_tool(wx.ID_REFRESH, "刷新", "refresh")
        self.toolBar.AddSeparator()
        settingButton = add_tool(wx.ID_ANY, "设置", "settings")
        self.toolBar.AddSeparator()
        helpButton = add_tool(wx.ID_ANY, "帮助", "help")
        aboutButton = add_tool(wx.ID_ANY, "关于", "about")

        # self.toolBar.Bind(wx.EVT_TOOL, self.OnNew, newButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnOpen, openButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddMU, muButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddTS, tsButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddMP4, mp4Button)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnExpandAll, expandButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnCollapseAll, collapseButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnPauseDownloads, self._pauseTool)
        self.toolBar.Bind(wx.EVT_UPDATE_UI, self.OnUpdatePauseDownloads, self._pauseTool)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnRefresh, refreshButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnSetting, settingButton)
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
        self.statusBar.SetStatusText('双击任务展开/折叠；下载请使用“操作”列', 1)
        self.showStatusItem.Check(self.statusBar.IsShown())

    def _createMainPanel(self, initial_tree):
        """创建主面板和布局"""
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # 筛选区域只放条件和结果数量，全局操作集中在菜单、工具栏。
        uriSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.searchCtrl = wx.SearchCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.searchCtrl.SetDescriptiveText('筛选文件名或路径')
        self.searchCtrl.ShowCancelButton(True)
        self.statusFilter = wx.Choice(panel, choices=[
            '全部状态', '未开始', '等待下载', '下载中', '暂停中', '已暂停', '已中断', '录制中', '停止中', '录制失败',
            '待继续', '下载失败', '检测时长', '待合并', '合并中', '合并失败', '已完成'])
        self.statusFilter.SetSelection(0)
        self.filterCount = wx.StaticText(panel, label='')
        self.filterCount.SetMinSize(self.FromDIP(wx.Size(140, -1)))
        uriSizer.Add(self.searchCtrl, proportion=1, flag=wx.EXPAND|wx.TOP|wx.BOTTOM|wx.RIGHT, border=self.FromDIP(5))
        uriSizer.Add(self.statusFilter, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(10))
        uriSizer.Add(self.filterCount, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(5))
        self.searchCtrl.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self.OnSearch)
        self.searchCtrl.Bind(wx.EVT_TEXT_ENTER, self.OnSearch)
        self.searchCtrl.Bind(wx.EVT_TEXT, self.OnSearchText)
        self.searchCtrl.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self.OnClearSearch)
        self.searchCtrl.Bind(wx.EVT_CHAR_HOOK, self.OnSearchKey)
        self.statusFilter.Bind(wx.EVT_CHOICE, self.OnStatusFilter)

        self.emptyPanel = wx.Panel(panel)
        emptySizer = wx.BoxSizer(wx.HORIZONTAL)
        self.emptyText = wx.StaticText(self.emptyPanel, label='没有符合条件的任务')
        self.clearFiltersButton = wx.Button(self.emptyPanel, label='清除全部筛选')
        self.clearFiltersButton.Bind(wx.EVT_BUTTON, self.OnClearAllFilters)
        emptySizer.AddStretchSpacer()
        emptySizer.Add(self.emptyText, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=self.FromDIP(12))
        emptySizer.Add(self.clearFiltersButton, flag=wx.ALIGN_CENTER_VERTICAL)
        emptySizer.AddStretchSpacer()
        self.emptyPanel.SetSizer(emptySizer)
        self.emptyPanel.Hide()

        # 多列树布局
        # self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_READONLY|wx.TE_RICH2)
        listSizer = wx.BoxSizer(wx.HORIZONTAL)
        # 创建并关联模型
        self.model = MultiColumnTreeModel(initial_tree, self)
        # 创建DataViewCtrl
        self.mcTree = dv.DataViewCtrl(panel, -1, style=wx.BORDER_THEME|dv.DV_ROW_LINES|dv.DV_VERT_RULES|dv.DV_VARIABLE_LINE_HEIGHT|dv.DV_ROW_LINES)
        # Windows 下原生 RendererNative 不能通过 Python 重写其绘制回调。
        # 在列表内部窗口的原生绘制结束后统一替换选中边框。
        if wx.Platform == '__WXMSW__':
            self.mcTree.GetMainWindow().Bind(wx.EVT_PAINT, self.OnTaskListPaint)
        self.mcTree.AssociateModel(self.model)
        self._knownTaskPaths = {task.parent.fileName for task in self.model.fileTree.items if task.parent}
        self._UpdateFilterCount()
        # 添加多列
        self.mcTree.AppendTextColumn("序列", 0, width=self.FromDIP(60))
        # # 自定义列
        # renderer = dv.DataViewTextRenderer()
        # renderer.EnableEllipsize(wx.ELLIPSIZE_END)
        # self.mcTree.AppendColumn(dv.DataViewColumn("文件名", renderer, 1, width=180, align=wx.ALIGN_LEFT))
        # self.mcTree.AppendTextColumn("文件名", 1, width=500)
        self.mcTree.AppendTextColumn("文件名", 1, width=self.FromDIP(250))
        self.mcTree.AppendColumn(dv.DataViewColumn('下载进度', TaskProgressRenderer(self), 5,
                                                 width=self.FromDIP(180), align=wx.ALIGN_CENTER))
        self.mcTree.AppendTextColumn("状态", 6, width=self.FromDIP(80), align=wx.ALIGN_CENTER)
        self.mcTree.AppendTextColumn("文件大小", 2, width=self.FromDIP(90), align=wx.ALIGN_RIGHT)
        self.mcTree.AppendTextColumn("修改时间", 3, width=self.FromDIP(130))
        self._actionRenderer = TaskActionRenderer(self)
        initial_action_width = self.FromDIP(200 if wx.Platform == '__WXMAC__' else 240)
        actionColumn = dv.DataViewColumn("操作", self._actionRenderer, 4,
                                       width=initial_action_width, align=wx.ALIGN_CENTER)
        # Mac 原生控件在首次布局时会重新调整列宽，必须同时约束原生最小宽度。
        actionColumn.SetMinWidth(initial_action_width)
        self.mcTree.AppendColumn(actionColumn)
        for index in range(self.mcTree.GetColumnCount()):
            column = self.mcTree.GetColumn(index)
            column.GetRenderer().EnableEllipsize(wx.ELLIPSIZE_END)
            column.SetFlags(column.GetFlags() & ~wx.COL_RESIZABLE)
        # self.mcTree.AppendTextColumn("下载地址", 5)
        # self.model.DecRef()  # 避免内存泄漏
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_EXPANDED, self.OnTaskExpansionChanged)
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_COLLAPSED, self.OnTaskExpansionChanged)
        # 默认折叠；用户开启查看菜单中的偏好后，首次加载才主动展开。
        # self.OnExpandAll(None)
        if SysSetting.GetAll()['default_expand_tasks']:
            self.OnExpandAll(None)
        listSizer.Add(self.mcTree, proportion=10, flag=wx.EXPAND|wx.TOP, border=self.FromDIP(5))
        # listSizer.Add(self.mulist, proportion=10, flag=wx.EXPAND|wx.ALL, border=5)
        # self.list.SetBackgroundColour(wx.RED)

        # 双击任务只切换展开状态，下载由操作列触发。
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_ACTIVATED, self.OnActivatedChanged)
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_CONTEXT_MENU, self.OnTaskContextMenu)
        self._manualActionClicks = wx.Platform == '__WXMAC__'
        if self._manualActionClicks:
            self.mcTree.GetMainWindow().Bind(wx.EVT_LEFT_UP, self.OnTaskListClick)
        self.Bind(EVT_ALL_DOWNLOAD, self.OnAllTSDownload)

        sizer.Add(uriSizer, flag=wx.EXPAND, border=0)
        sizer.Add(self.emptyPanel, flag=wx.EXPAND | wx.TOP | wx.BOTTOM, border=self.FromDIP(12))
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND|wx.ALL, border=0)
        # 设置面板的sizer
        panel.SetSizer(sizer)
        if wx.Platform == '__WXMAC__':
            frameSizer = wx.BoxSizer(wx.VERTICAL)
            frameSizer.Add(panel, proportion=1, flag=wx.EXPAND)
            self.SetSizer(frameSizer)


    def OnTaskListClick(self, event):
        """Mac 松开鼠标后分发操作，避免在按下事件中打开原生菜单或弹窗。"""
        source = event.GetEventObject()
        pos = self.mcTree.ScreenToClient(source.ClientToScreen(event.GetPosition()))
        item, column = self.mcTree.HitTest(pos)
        if not item.IsOk() or column is None or column.GetModelColumn() != 4:
            event.Skip()
            return
        cell = wx.Rect(self.mcTree.GetItemRect(item, column))
        if cell.IsEmpty():
            event.Skip()
            return
        self.mcTree.SetFocus()
        self.mcTree.Select(item)
        # Cocoa 把自定义渲染器居中放在列内；GetItemRect 返回整列区域，
        # 实际 Render 区域则按 GetSize 缩进。点击必须使用同一绘制宽度。
        content_width = min(cell.width, self._actionRenderer.GetSize().width)
        cell.x += (cell.width - content_width) // 2
        cell.width = content_width
        local = wx.Point(pos.x - cell.x, pos.y - cell.y)
        self._actionRenderer.ActivateAt(cell, self.model, item, 4, local)
        # 操作列点击（包含置灰项和留白）到此结束，避免原生控件再次激活。
        event.Skip(False)

    def OnTaskListPaint(self, event):
        """先同步完成原生列表绘制，再覆盖黑色焦点边框，避免延迟补画闪烁。"""
        window = self.mcTree.GetMainWindow()
        # 暂时移除本处理函数，让同一个绘制事件进入控件自身的默认处理路径。
        # finally 恢复绑定，避免递归进入自己，也保证滚动/切换选择后继续生效。
        window.Unbind(wx.EVT_PAINT, handler=self.OnTaskListPaint)
        try:
            window.GetEventHandler().ProcessEvent(event)
            event.Skip(False)
            item = self.mcTree.GetSelection()
            if not item.IsOk():
                return
            row = self.mcTree.GetItemRect(item)
            # GetItemRect 使用列表坐标，内部绘图区位于表头下方，需要转换坐标。
            top = window.ScreenToClient(self.mcTree.ClientToScreen(row.GetTopLeft())).y
            if row.height <= 0 or top >= window.GetClientSize().height or top + row.height <= 0:
                return
            dc = wx.ClientDC(window)
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            dc.SetPen(wx.Pen('#99D1FF', 1))
            dc.DrawRectangle(0, top, window.GetClientSize().width, row.height)
        finally:
            window.Bind(wx.EVT_PAINT, self.OnTaskListPaint)

    def OnFrameShown(self, event):
        event.Skip()
        if event.GetEventObject() is self and event.IsShown():
            wx.CallAfter(self._FitShownTaskColumns)

    def _FitShownTaskColumns(self):
        if self._closing or not self or self.IsBeingDeleted():
            return
        # 初次原生布局可能改变列宽，但控件整体尺寸不变，不能沿用显示前的缓存。
        self._columnSizeKey = None
        self.Layout()
        self.mcTree.GetParent().Layout()
        self._FitTaskColumns()

    def OnTaskListSize(self, event):
        event.Skip()
        if self._closing:
            return
        if not getattr(self, '_columnFitPending', False):
            self._columnFitPending = True
            wx.CallAfter(self._FitTaskColumns)

    def _FitTaskColumns(self):
        """自定义列宽适配方法，不是 wx 的重写回调。

        初始化时在显示前完成布局并直接调用；后续尺寸变化通过 wx.CallAfter 调用。
        默认窗口维持现有布局；放大时各列按默认比例扩展；缩小时优先压缩文件名列。
        所有列的总宽度控制在可用区域内，避免出现横向滚动条。
        """
        # 本次延迟调整开始执行，允许后续尺寸变化再次安排调整。
        self._columnFitPending = False
        # CallAfter 执行时窗口可能已经关闭，此时不能再访问原生控件。
        if self._closing or not self or self.IsBeingDeleted() or not self.mcTree:
            return
        # 按最宽编号预留文字、两级树缩进和留白；不遍历所有分片文本。
        font = wx.Font(self.mcTree.GetFont())
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        dc = wx.ClientDC(self.mcTree)
        dc.SetFont(font)
        root_digits = len(str(max(1, len(self.model.fileTree.items))))
        child_count = max((max(len(task.childs), len(task.outputs))
                           for task in self.model.fileTree.items), default=0)
        label = '9' * root_digits
        if child_count:
            label += '.' + '9' * len(str(child_count))
        indent = max(self.mcTree.GetIndent(), self.FromDIP(16))
        sequence_width = max(self.FromDIP(50), dc.GetTextExtent(label)[0]
                             + indent * (2 if child_count else 1) + self.FromDIP(12))
        if wx.Platform == '__WXMAC__':
            # 使用 Cocoa 的实际树缩进，避免额外放大编号列的留白。
            sequence_width = max(self.FromDIP(40), dc.GetTextExtent(label)[0]
                                 + self.mcTree.GetIndent() * (2 if child_count else 1)
                                 + self.FromDIP(6))
        # 编号位数也参与缓存，新增任务或刷新后即使窗口尺寸不变也能重新适配。
        font.SetWeight(wx.FONTWEIGHT_NORMAL)
        dc.SetFont(font)
        action_width = self._actionRenderer.MinimumWidth(dc)
        progress_width = self.FromDIP(180)
        if wx.Platform == '__WXMAC__':
            progress_labels = [self.model.TaskInfo(index)['progress']
                               for index in range(len(self.model.fileTree.items))]
            progress_labels.append(f'100% · {child_count}/{child_count}')
            progress_width = max(progress_width,
                                 max(dc.GetTextExtent(text)[0] for text in progress_labels)
                                 + self.FromDIP(20))
        date_width = self.FromDIP(125)
        if wx.Platform == '__WXMAC__':
            # 按当前字体最宽的数字测量完整日期，留出文本单元格的左右边距。
            text_width = max(dc.GetTextExtent(
                f'{digit * 4}-{digit * 2}-{digit * 2} {digit * 2}:{digit * 2}')[0]
                for digit in '0123456789')
            date_width = max(date_width, text_width + self.FromDIP(12))
        size_key = (self.mcTree.GetSize().width, self.FromDIP(100), sequence_width,
                    action_width, date_width, progress_width)
        if getattr(self, '_columnSizeKey', None) == size_key:
            return
        # 预留垂直滚动条和边框空间，避免滚动条出现后把最后一列挤出可视区域。
        # 使用控件整体宽度，避免滚动条改变客户区宽度后触发列宽来回调整。
        # Cocoa 原生表格在列宽之外为每列增加 17 DIP 的单元格间距。
        # GetWidth 不包含这些间距，必须一起预留，否则末列仍会越过可视区域。
        column_spacing = (self.FromDIP(17) * self.mcTree.GetColumnCount()
                          if wx.Platform == '__WXMAC__' else 0)
        available = (self.mcTree.GetSize().width
                     - wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X, self.mcTree)
                     - self.FromDIP(4) - column_spacing)
        if available <= 0:
            return
        self._columnSizeKey = size_key
        # 按界面显示顺序：序列、文件名、下载进度、状态、文件大小、修改时间、操作。
        # 数字是 DIP，由 FromDIP 按系统缩放换算；文件名的 0 是占位，下面补入剩余宽度。
        widths = [self.FromDIP(value) for value in (50, 0, 180, 80, 85, 125, 200)]
        if wx.Platform == '__WXMAC__':
            # 辅助信息列保持紧凑，把可用空间优先留给文件名。
            widths[2:5] = [progress_width, self.FromDIP(65), self.FromDIP(70)]
        widths[0] = sequence_width
        widths[5] = date_width
        widths[-1] = action_width
        # 默认窗口的可用宽度是比例分配的基准，不随最大化/还原反复改变。
        baseline = (self._defaultTaskWidth
                    - wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X, self.mcTree)
                    - self.FromDIP(4) - column_spacing)
        # 不超过默认宽度时，其他列保持基准值，剩余空间交给文件名列。
        widths[1] = max(self.FromDIP(80), min(available, baseline) - sum(widths))
        if wx.Platform == '__WXMAC__' and available > baseline > 0:
            widths[1] += available - baseline
        elif available > baseline > 0:
            # 以默认布局为基准按比例分配，不把额外空间全部留给文件名。
            # 相邻边界取差，避免整数取整后列宽之和超出窗口。
            total = sum(widths)
            edge = 0
            scaled = []
            for width in widths:
                # 每列的新宽度 = 缩放后的右边界 - 缩放后的左边界。
                scaled.append((edge + width) * available // total - edge * available // total)
                edge += width
            widths = scaled
        if sum(widths) > available:
            # 窄窗口先缩短进度/状态/体积/日期，保留编号、文件名和操作的可读宽度。
            remaining = available - widths[0] - widths[1] - widths[-1]
            flexible_end = 5 if wx.Platform == '__WXMAC__' else 6
            if flexible_end == 5:
                # Mac 日期列保留完整时间所需宽度，压缩其余信息列。
                remaining -= widths[5]
            if remaining >= flexible_end - 2:
                total = sum(widths[2:flexible_end])
                widths[2:flexible_end] = [max(1, value * remaining // total)
                                          for value in widths[2:flexible_end]]
            else:
                # 初始化时控件可能暂时小于窗口最小尺寸，待布局完成后重新适配。
                self._columnSizeKey = None
                return
        # 批量调整时暂停重绘，完成后统一恢复。
        self.mcTree.Freeze()  # wx 方法：暂停该控件的屏幕重绘，期间仍可修改列宽。
        try:
            self.mcTree.GetColumn(6).SetMinWidth(action_width)
            for index, width in enumerate(widths):
                column = self.mcTree.GetColumn(index)
                # 只写入真正变化的列宽，减少原生控件的布局和重绘开销。
                if column.GetWidth() != width:
                    column.SetWidth(width)
        finally:
            # wx 方法：恢复重绘，让批量修改后的列宽显示出来。
            # 与 Freeze 配对；放在 finally 中，确保调整过程出错时也能恢复界面刷新。
            self.mcTree.Thaw()

    def _PrepareClose(self):
        if self._closing:
            return
        self._closing = True
        # Cocoa 的销毁通知晚于部分原生控件清理，必须在开始销毁前停表。
        for name in ('_progressTimer', '_searchTimer'):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.Stop()
        self.Unbind(wx.EVT_TIMER)
        if hasattr(self, 'runner'):
            self.runner.shutdown(task.task_id for task in self.model.fileTree.items
                                 if task.task_id and task.task_status == TaskStatus.MERGING)

    def OnClose(self, event):
        self._PrepareClose()
        event.Skip()

    def Destroy(self):
        self._PrepareClose()
        return super().Destroy()

    def OnDestroy(self, event):
        if event.GetEventObject() is self:
            self._PrepareClose()
        event.Skip()

    def OnTaskProgress(self, event, notify=True):
        """启动只建立缓存；后续只刷新变化的单元格，不反复使整行和全部表头失效。"""
        if self._closing:
            return
        self._SyncDownloads()
        display = {}
        child_display = {}
        snapshot = M3U8Downloader.Snapshot()
        for index, task in enumerate(self.model.fileTree.items):
            self._QueueDurationCheck(task)
            info = self.model.TaskInfo(index)
            state = (info['progress'], info['status'], json.dumps(self.model.TaskActions(index), ensure_ascii=False))
            display[task.parent.fileName] = state
            previous = self._task_display.get(task.parent.fileName)
            if notify and previous != state:
                item = self.model.ObjectToItem(self.model._BuildKey((index,)))
                for field, column in enumerate((5, 6, 4)):
                    if previous is None or previous[field] != state[field]:
                        self.model.ValueChanged(item, column)
            root = self.model.ObjectToItem(self.model._BuildKey((index,)))
            if self.mcTree.IsExpanded(root):
                for child_index in range(len(task.outputs) + len(task.childs)):
                    key = (task.parent.fileName, child_index)
                    child_status = self.model.ChildStatus(index, child_index, snapshot)
                    child_display[key] = child_status
                    if notify and getattr(self, '_child_status_display', {}).get(key) != child_status:
                        child = self.model.ObjectToItem(self.model._BuildKey((index, child_index)))
                        self.model.ValueChanged(child, 6)
        status_changed = any(self._task_display.get(key, ('', ''))[1] != state[1]
                             for key, state in display.items())
        self._task_display = display
        self._child_status_display = child_display
        if status_changed and self.statusFilter.GetSelection() > 0:
            self._ApplyTaskFilter()


    def OnTaskExpansionChanged(self, event):
        # 行首箭头、行内操作和全部展开/折叠共用通知，及时更新操作文字。
        self.model.ItemChanged(event.GetItem())
        event.Skip()

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
        directory = task.save_dir or Path(PathManager.GetAbsPath(task.parent.fileName)).parent
        menu = wx.Menu()

        def add(label, callback, enabled=True):
            entry = menu.Append(wx.ID_ANY, label)
            entry.Enable(enabled)
            menu.Bind(wx.EVT_MENU, lambda event: callback(), entry)

        info = self.model.TaskInfo(index)
        parent_item = self.model.ObjectToItem(self.model._BuildKey((index,)))
        if task.task_type == TaskType.M3U8:
            add('转 MP4', lambda: self.OnTaskAction(parent_item, 'merge'),
                bool(info['total']) and info['done'] == info['total'] and not task.outputs
                and not info['pending'] and not info['merging'])
        add('打开文件夹', lambda: self._OpenLocalPath(directory))
        if task.task_type == TaskType.MP4 and task.last_error:
            add('查看失败原因', lambda: self._ShowInformation('MP4 下载失败', task.last_error))
        if task.outputs and (task.task_type != TaskType.MP4 or task.task_status == TaskStatus.COMPLETED):
            for output in task.outputs:
                path = PathManager.GetAbsPath(output.fileName)
                add('播放视频：' + Path(path).name, lambda path=path: self._OpenLocalPath(path))
        else:
            add('播放视频', lambda: None, False)
        try:
            self.mcTree.PopupMenu(menu)
        finally:
            menu.Destroy()


    ###################################
    ### 事件所需函数
    ###################################
    def _SearchItems(self, text):
        """应用关键词筛选；命中任务、MP4 或分片路径均保留整个任务。"""
        self._filterText = text.strip().casefold()
        self._ApplyTaskFilter()

    def _UpdateFilterCount(self):
        total = len(self.model.fileTree.items)
        visible = total if self.model.visible_tasks is None else len(self.model.visible_tasks)
        label = f'显示 {visible} / {total} 个任务'
        if self.filterCount.GetLabel() != label:
            self.filterCount.SetLabel(label)
        filtered = bool(self._filterText) or self.statusFilter.GetSelection() > 0
        # 无任务时保持空列表；仅筛选无匹配结果时显示清除筛选提示。
        empty = total > 0 and visible == 0 and filtered
        self.clearFiltersButton.Show(filtered)
        if self.emptyPanel.IsShown() != empty:
            self.emptyPanel.Show(empty)
            self.emptyPanel.GetParent().Layout()
        if empty:
            self.emptyPanel.Layout()

    def _ApplyTaskFilter(self, force=False, expanded_keys=None):
        """只筛选根任务，保持原始任务索引，避免筛选后暂停、删除或下载回调操作错行。"""
        text = self._filterText
        status = self.statusFilter.GetStringSelection()
        visible = None
        if text or status != '全部状态':
            visible = set()
            for index, task in enumerate(self.model.fileTree.items):
                if text and not (
                    (task.parent and (text in task.parent.fileName.casefold()
                                     or text in task.parent.displayName.casefold()))
                    or any(text in file.fileName.casefold() for file in task.outputs)
                    or any(text in file.fileName.casefold() for file in task.childs)
                ):
                    continue
                if status != '全部状态' and self.model.TaskInfo(index)['status'].split(' · ', 1)[0] != status:
                    continue
                visible.add(index)
        if force or visible != self.model.visible_tasks:
            expanded = set() if expanded_keys is None else set(expanded_keys)
            root = dv.NullDataViewItem
            if expanded_keys is None:
                self._SaveExpandState(root, expanded)
            # 刷新时只给新任务应用默认值，已存在任务保留用户手动展开/折叠的状态。
            if SysSetting.GetAll()['default_expand_tasks']:
                for index, task in enumerate(self.model.fileTree.items):
                    if task.parent and task.parent.fileName not in self._knownTaskPaths:
                        expanded.add(self.model._BuildKey((index,)))
            selected = self.mcTree.GetSelection()
            selected_key = self.model.ItemToObject(selected) if selected.IsOk() else None
            self.mcTree.Freeze()
            try:
                self.model.visible_tasks = visible
                self.model.Cleared()
                self._RestoreExpandState(root, expanded)
                if selected_key is not None and not force:
                    index = self.model.ParseKey(selected_key)[0]
                    if visible is None or index in visible:
                        self.mcTree.Select(self.model.ObjectToItem(selected_key))
            finally:
                self.mcTree.Thaw()
        self._UpdateFilterCount()
        self._knownTaskPaths = {task.parent.fileName for task in self.model.fileTree.items if task.parent}
        if hasattr(self, '_defaultTaskWidth'):
            self._FitTaskColumns()

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
        """重新加载视图时仍应用当前条件，并保留可见任务的展开状态。"""
        self._ApplyTaskFilter(force=True)

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
                self._RestoreExpandState(child, expandeds)
            child, cookie = self.model.GetNextChild(parent, cookie)


    ###################################
    ### 操作菜单的事件
    ################################### 
    def OnOpen(self, event):
        """打开当前默认下载目录；单个任务的原目录由行内“更多”菜单打开。"""
        self._OpenLocalPath(SysSetting.GetWorkPath())
    
    def OnAddMU(self, event):
        workPath = SysSetting.GetWorkPath()
        dlg = DownloadDialogMU(self, "M3U8 URI", workPath, task_service=self.tasks)
        result = dlg.ShowModal()
        if result == wx.OK:
            # 创建新任务成功，自动刷新页面
            self.OnRefresh(None)
        dlg.Destroy()

    def OnAddTS(self, event):
        workPath = SysSetting.GetWorkPath()
        dlg = DownloadDialogTS(self, "M3U8 TS", workPath, task_service=self.tasks)
        result = dlg.ShowModal()
        if result == wx.OK:
            # 创建新任务成功，自动刷新页面
            self.OnRefresh(None)
        dlg.Destroy()
    
    def OnAddMP4(self, event):
        dialog = DownloadDialogMP4(self, SysSetting.GetWorkPath(), self.tasks)
        try:
            if dialog.ShowModal() == wx.OK:
                self.OnRefresh(None)
                self._StartMP4(dialog.task_id)
        finally:
            dialog.Destroy()

    def _StartMP4(self, task_id):
        try:
            self.runner.start(task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'无法启动 MP4 下载：{error}', '下载失败', parent=self)
        self._SyncDownloads()

    def _SyncDownloads(self):
        """只消费协调对象的通知；窗口负责展示结果，不调度线程或选择下载器。"""
        while True:
            try:
                task_id, error = self.runner.duration_results.get_nowait()
            except Empty:
                break
            self._DurationChecked(task_id, None, error)
        structure_changed = False
        for task_id in self.runner.changes():
            try:
                record = self.tasks.repository.get(task_id)
                if record is None:
                    continue  # 删除后的迟到通知不能重新添加任务。
                previous = next((task for task in self.model.fileTree.items if task.task_id == task_id), None)
                index = self.model.ApplyTaskRecord(record)
                if index is None:
                    continue
                current = self.model.fileTree.items[index]
                structure_changed = structure_changed or (previous is not None and len(previous.outputs) != len(current.outputs))
                if record.type == TaskType.M3U8:
                    # 只刷新变化的分片，不因每次状态变化重绘整棵树。
                    for offset, child in enumerate(current.childs):
                        old = previous.childs[offset] if previous and offset < len(previous.childs) else None
                        if old is None or (old.fileSize, old.status) != (child.fileSize, child.status):
                            item = self.model.ObjectToItem(self.model._BuildKey((index, len(current.outputs) + offset)))
                            self.model.ItemChanged(item)
                    self._M3U8Changed(task_id)
                elif record.type == TaskType.MP4:
                    self.model.ItemChanged(self.model.ObjectToItem(self.model._BuildKey((index, 0))))
                    self.model.ItemChanged(self.model.ObjectToItem(self.model._BuildKey((index,))))
                elif record.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PAUSED):
                    self.model.ItemChanged(self.model.ObjectToItem(self.model._BuildKey((index,))))
            except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
                self.statusBar.SetStatusText(f'读取下载进度失败：{error}', 0)
        if structure_changed:
            # 合并完成会新增 MP4 子节点，保留展开状态后重建树，不能只重绘单元格。
            self._RefreshWithState()
        while True:
            try:
                error = self.runner.errors.get_nowait()
            except Empty:
                break
            wx.MessageBox(error, '保存失败', parent=self)

    def OnSetting(self, event):
        from src.views.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        try:
            # 设置只影响之后新建任务的默认目录，已有行及其绝对路径保持不变。
            dlg.ShowModal()
            # self.OnExpandAll(None)
        finally:
            dlg.Destroy()

    def OnExit(self, event):
        self.Close()

    def OnPauseDownloads(self, event):
        # 工具栏维持单按钮；所有待处理任务都已单独暂停时，也应显示并执行继续。
        can_pause, can_resume = self._GlobalDownloadActions()
        if can_pause:
            self.OnPauseAllDownloads(event)
        elif can_resume:
            self.OnResumeAllDownloads(event)


    def OnUpdateGlobalDownloadAction(self, event):
        can_pause, can_resume = self._GlobalDownloadActions()
        if event.GetId() == self.pauseAllItem.GetId():
            event.Enable(can_pause)
        else:
            event.Enable(can_resume)

    def OnUpdatePauseDownloads(self, event):
        paused = M3U8Downloader.IsPaused()
        busy = M3U8Downloader.IsBusy() or any(task.task_type == TaskType.MP4 and
            self.runner.mp4.busy(task.task_id) for task in self.model.fileTree.items)
        can_pause, can_resume = self._GlobalDownloadActions()
        event.Enable(can_pause or can_resume)
        self._UpdatePauseTool()
        snapshot = M3U8Downloader.Snapshot()
        self.statusBar.SetStatusText(('暂停中' if snapshot['requesting'] else '已暂停') if paused
                                     else ('正在下载' if busy else '就绪'), 0)

    def _UpdatePauseTool(self):
        """菜单和工具栏共用下载状态；状态未变时不重复设置位图，避免工具栏闪动。"""
        if self.toolBar is None:
            return
        can_pause, can_resume = self._GlobalDownloadActions()
        paused = not can_pause and can_resume
        enabled = can_pause or can_resume
        if self._pauseToolState == (paused, enabled):
            return
        label = '全部继续' if paused else '全部暂停'
        tool_id = self._pauseTool.GetId()
        self.toolBar.EnableTool(tool_id, enabled)
        if self._pauseToolState is None or self._pauseToolState[0] != paused:
            self.toolBar.SetToolNormalBitmap(tool_id, self._pauseIcons[paused])
            self._pauseTool.SetLabel(label)
            self.toolBar.SetToolShortHelp(tool_id, label + '（包括筛选隐藏的任务）')
        self._pauseToolState = (paused, enabled)

    def OnDefaultExpandTasks(self, event):
        """只保存加载偏好，不把当前列表的临时展开操作变成默认设置。"""
        values = SysSetting.GetAll()
        previous = values['default_expand_tasks']
        values['default_expand_tasks'] = event.IsChecked()
        try:
            SysSetting.Save(values)
        except (ValueError, OSError) as error:
            self.defaultExpandItem.Check(previous)
            wx.MessageBox(str(error), '设置未保存', wx.OK | wx.ICON_WARNING, self)

    def OnToggleToolBar(self, event):
        '''隐藏展示工具栏'''
        if self.toolBar is None:
            return
        self.toolBar.Show(self.showToolItem.IsChecked())
        self.SendSizeEvent()

    def OnToggleStatusBar(self, event):
        '''隐藏展示状态栏'''
        self.statusBar.Show(self.showStatusItem.IsChecked())
        self.SendSizeEvent()

    def _ShowInformation(self, title, text):
        dlg = InformationDialog(self, title, text, usage=title == '使用帮助')
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def OnHelp(self, event):
        self._ShowInformation('使用帮助', (
            '1. 添加任务\n'
            '通过“文件 → 下载M3U8”输入播放列表网址，或通过“下载TS”按分片命名规则创建任务。\n\n'
            '通过“文件 → 下载 MP4”（Ctrl+P）添加视频直链，填写任务子目录后开始下载。\n\n'
            '筛选区域可按文件名、路径和任务状态筛选；关键词也匹配任务下的 MP4 和分片。'
            '清空关键词并选择“全部状态”恢复全部任务。隐藏任务仍继续下载。\n\n'
            '2. 下载与合并\n'
            '任务行固定显示“开始/暂停/继续、重试、删除、展开/折叠、更多”，不可用的操作会置灰。'
            '“更多”或右键菜单提供转 MP4、播放视频和打开文件夹。删除只移除任务记录，保留本地文件。'
            '行内“暂停/继续”只控制当前任务，已发出的请求允许完成。进度按已完成分片数计算。\n'
            '双击任务行展开或折叠分片列表；下载请点击操作列中的开始、继续或下载。'
            '全部下载完成后，可从“更多”中选择“转 MP4”。\n\n'
            '点击“全部暂停”可暂停全部分片任务；已发出的请求允许完成，此时显示“暂停中”。'
            '这些请求结束后显示“已暂停”。“全部继续”恢复排队分片和重启后的暂停/中断任务，暂停不影响 MP4 合并。\n'
            '重启保留进度和失败记录，不自动下载；中断的合并请重新选择“转 MP4”。\n\n'
            'MP4 直链按字节显示进度，可展开查看下载文件。暂停保留临时文件，继续时由服务器决定是否可续传；'
            '不支持续传或文件已改变时从头下载。等待响应时暂停可能要等到网络超时。'
            '全部暂停/继续也包含 MP4，下载失败可在“更多”查看原因。\n\n'
            '3. 下载设置\n'
            '通过“文件 → 设置”（Ctrl+,）调整默认下载目录、并发、请求间隔、重试、超时及自动合并。'
            '默认目录仅影响之后新建的任务，已有任务仍使用原目录，可在下载或合并期间修改。'
            '合并需要 FFmpeg，路径留空时先查找 scripts 目录，再查找系统 PATH。\n\n'
            '4. 界面显示\n'
            '通过“查看”菜单显示或隐藏状态栏；Windows 还可以显示或隐藏工具栏。'
            '下载窗口内的“？”可查看对应输入框的说明。'
        ))

    def OnAbout(self, event):
        self._ShowInformation('关于 AVDownloader', (
            'AVDownloader\n\n'
            '支持 M3U8 播放列表、TS 分片、MP4 直链下载及 FFmpeg 合并 MP4。\n\n'
            '项目地址：\nhttps://github.com/cliffordll/downloader'
        ))

    ###################################
    ### 操作树得事件
    ###################################
    def OnFind(self, event):
        """菜单和 Ctrl+F 共用：聚焦现有筛选框，选中文字以便直接输入新条件。"""
        self.searchCtrl.SetFocus()
        self.searchCtrl.SelectAll()

    def OnSearch(self, event):
        """点击搜索按钮或按回车立即筛选，取消尚未执行的延迟筛选。"""
        self._searchTimer.Stop()
        self._SearchItems(event.GetEventObject().GetValue())
    
    def OnSearchText(self, event):
        """输入停止 200 毫秒后才筛选；清空时立即移除关键词条件。"""
        self._searchTimer.Stop()
        self._searchText = event.GetString()
        if self._searchText.strip():
            self._searchTimer.StartOnce(200)
        else:
            self._SearchItems('')

    def OnSearchKey(self, event):
        """只处理筛选框内的 Esc，不注册全局快捷键，也不清除状态筛选。"""
        if event.GetKeyCode() == wx.WXK_ESCAPE and not event.HasAnyModifiers():
            self.OnClearSearch(event)
        else:
            event.Skip()

    def OnClearSearch(self, event):
        self._searchTimer.Stop()
        self._searchText = ''
        self.searchCtrl.ChangeValue('')
        self._SearchItems('')

    def OnClearAllFilters(self, event):
        """空结果提示中的恢复入口，同时清除关键词、状态及尚未执行的筛选。"""
        self.statusFilter.SetSelection(0)
        self.OnClearSearch(event)

    def OnStatusFilter(self, event):
        self._searchTimer.Stop()
        self._SearchItems(self.searchCtrl.GetValue())

    def OnSearchTimer(self, event):
        """计时结束时只搜索最新输入；清空输入或关闭窗口会取消计时。"""
        if self._closing:
            return
        self._SearchItems(self._searchText)

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
        self.runner.reset_duration_failures()
        # 只从数据库恢复任务；读取失败保留当前列表，不退回扫描目录。
        try:
            tree = load_tree(self.tasks)
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'读取任务失败：{error}', '刷新失败', wx.OK | wx.ICON_WARNING, parent=self)
            return
        # 删除或新增记录会改变行号，先按任务身份保存展开状态，再映射到新行。
        expanded_ids = set()
        for index, task in enumerate(self.model.fileTree.items):
            item = self.model.ObjectToItem(self.model._BuildKey((index,)))
            if self.mcTree.IsExpanded(item):
                expanded_ids.add(task.task_id or task.parent.fileName)
        self.model.fileTree = tree
        expanded = {self.model._BuildKey((index,)) for index, task in enumerate(tree.items)
                    if (task.task_id or task.parent.fileName) in expanded_ids}
        self._ApplyTaskFilter(force=True, expanded_keys=expanded)

    def OnActivatedChanged(self, event):
        """任务双击/键盘激活只展开或折叠，普通分片单元格激活不下载。"""
        column = event.GetDataViewColumn()
        if column is not None and column.GetModelColumn() == 4:
            return  # The custom renderer handles action cells on a single click.
        item = event.GetItem()
        if not item.IsOk():
            return
        keys = self.model.ItemToObject(item)
        objs = self.model.ParseKey(keys)
        if len(objs) == 1:
            self.OnTaskAction(item, 'toggle')

    # 窗口把点击位置转换为稳定的任务 UUID；执行规则由 TaskRunner 重新核对。
    def OnTaskAction(self, item, action_id='start'):
        """自定义操作分发入口，不是 wx 自动调用的重写方法。

        行内渲染器识别点击区域后传入 item 和操作 id；“转 MP4”菜单传入 merge。
        item 指明具体任务行，不依赖当前选中行；默认 start 表示开始/暂停/继续。
        start 表示开始/暂停/继续，retry 重试失败，delete 删除，toggle 展开/折叠，more 打开菜单。
        """
        if not item.IsOk():
            return
        keys = self.model.ParseKey(self.model.ItemToObject(item))
        # 只有任务父节点使用这组操作；分片子节点由另一个处理函数负责。
        if len(keys) != 1:
            return
        index = keys[0]
        task = self.model.fileTree.items[index]
        info = self.model.TaskInfo(index)
        # 点击与绘制之间状态可能改变，所以在执行前重新读取当前任务状态。
        if action_id == 'merge':
            if task.task_type != TaskType.M3U8:
                return
            # 合并位于“更多”菜单内：分片必须完整，不能正在下载/合并或已有输出。
            if info['pending'] or info['merging'] or task.outputs or not info['total'] or info['done'] != info['total']:
                return
            self._CreateMP4File(task.parent.fileName, item)
        else:
            # 按稳定的操作 id 匹配，不依赖“开始”“继续”等会随状态变化的显示文字。
            # 再检查 enabled，防止过期的界面状态触发已不可用的操作。
            action = next((entry for entry in self.model.TaskActions(index) if entry['id'] == action_id), None)
            if action is None or not action['enabled']:
                return
            if action_id == 'toggle':
                if self.mcTree.IsExpanded(item):
                    self.mcTree.Collapse(item)
                else:
                    self.mcTree.Expand(item)
                return
            if action_id == 'more':
                if self._manualActionClicks:
                    self._QueueTaskPopup(task, action_id)
                else:
                    self.OnTaskMenu(item)
                return
            if action_id == 'delete':
                # 删除处理函数会再次检查运行状态，并确认只删除任务记录。
                if self._manualActionClicks:
                    self._QueueTaskPopup(task, action_id)
                else:
                    self.OnDeleteTask(task)
                return
        if action_id in ('start', 'retry'):
            try:
                if self.runner.activate(task.task_id, retry=action_id == 'retry'):
                    self._completion_notified.discard(task.task_id)
            except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
                wx.MessageBox(str(error), '任务操作失败', parent=self)
            self._SyncDownloads()
        # 通知 wx 重新读取本行数据，使操作文字、置灰状态和任务状态及时更新。
        self.model.ItemChanged(item)

    def _QueueTaskPopup(self, task, action_id):
        # Cocoa 必须先结束鼠标事件处理，再进入菜单跟踪或模态对话框循环。
        identity = task.task_id or task.parent.fileName
        wx.CallAfter(self._ShowTaskPopup, identity, action_id)

    def _ShowTaskPopup(self, identity, action_id):
        if self._closing or not self or self.IsBeingDeleted():
            return
        # 延迟期间可能刷新或删除任务，按身份重新定位，不能沿用旧行号。
        for index, task in enumerate(self.model.fileTree.items):
            if (task.task_id or task.parent.fileName) != identity:
                continue
            action = next((entry for entry in self.model.TaskActions(index)
                           if entry['id'] == action_id), None)
            if action is None or not action['enabled']:
                return
            if action_id == 'more':
                self.OnTaskMenu(self.model.ObjectToItem(self.model._BuildKey((index,))))
            elif action_id == 'delete':
                self.OnDeleteTask(task)
            return

    def OnDeleteTask(self, task):
        """本阶段只删除数据库记录；下载文件保留，不再递归删除任务目录。"""
        try:
            if self.runner.busy(task.task_id):
                raise ValueError('任务仍在下载、检测或合并中，暂时不能删除。')
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '无法删除', wx.OK | wx.ICON_WARNING, parent=self)
            return
        dialog = wx.MessageDialog(self, '', '删除任务', wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        dialog.SetExtendedMessage('将从列表和数据库中删除此任务，已下载的文件会保留。')
        dialog.SetYesNoLabels('删除任务', '取消')
        try:
            if dialog.ShowModal() != wx.ID_YES:
                return
        finally:
            dialog.Destroy()
        try:
            self.runner.delete(task.task_id)
            self.model.merge_failed.discard(task.parent.fileName)
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'删除未完成：{error}', '无法删除', wx.OK | wx.ICON_WARNING, parent=self)
            return
        self.OnRefresh(None)

    def _GlobalDownloadActions(self):
        """固定菜单项各自判定可用状态；混合运行/暂停时两项均可用。"""
        snapshot = M3U8Downloader.Snapshot()
        can_pause = not snapshot['paused'] and bool(snapshot['pending'] - snapshot['paused_files'])
        tasks = self.model.fileTree.items if hasattr(self, 'model') else []
        can_pause = can_pause or any(task.task_type == TaskType.MP4 and task.task_status in
                                    (TaskStatus.QUEUED, TaskStatus.DOWNLOADING) for task in tasks)
        can_resume = snapshot['paused'] or bool(snapshot['pending'] & snapshot['paused_files']) or any(
            task.task_type == TaskType.M3U8 and task.task_status in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED)
            and any(child.fileSize == '-' for child in task.childs) for task in tasks)
        can_resume = can_resume or any(task.task_type == TaskType.MP4 and task.task_status in
                                      (TaskStatus.PAUSED, TaskStatus.INTERRUPTED) for task in tasks)
        return can_pause, can_resume

    def OnPauseAllDownloads(self, event):
        try:
            self.runner.pause_all()
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '暂停失败', parent=self)
        self._SyncDownloads()
        self.UpdateWindowUI(wx.UPDATE_UI_RECURSE)

    def OnResumeAllDownloads(self, event):
        try:
            if self._GlobalDownloadActions()[1]:
                self._completion_notified.difference_update(self.runner.resume_all())
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '继续失败', parent=self)
        self._SyncDownloads()
        self.UpdateWindowUI(wx.UPDATE_UI_RECURSE)

    def OnSegmentDownload(self, item):
        """只由分片的“下载”操作调用，重新核对索引和是否仍需要下载。"""
        if not item.IsOk():
            return
        keys = self.model.ParseKey(self.model.ItemToObject(item))
        if len(keys) != 2:
            return
        task = self.model.fileTree.items[keys[0]]
        index = keys[1] - len(task.outputs)
        if 0 <= index < len(task.childs) and task.childs[index].fileSize == '-' and task.parent:
            self._DownloadFiles(task.parent.fileName, [(index, item)])

    def OnAllTSDownload(self, event):
        '''所有TS文件都已经下载完毕，修改操作文本'''
        payload = event.GetData()
        if not payload:
            return
        fileName = payload.get("fileName", "")
        if payload.get('task_id') is not None:
            task = next((task for task in self.model.fileTree.items if task.task_id == payload['task_id']), None)
            if task is None:
                return  # 任务已删除，不处理之前排入 wx 队列的完成事件。
            fileName = task.parent.fileName
            if task.duration_pending:
                self._completion_notified.discard(task.task_id)
                self._QueueDurationCheck(task)
                return  # 实际时长入库后再重建清单，并执行自动合并。
        task = next((task for task in self.model.fileTree.items if task.parent.fileName == fileName), None)
        if task is None:
            return
        try:
            self.runner.complete(task.task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '任务处理失败', parent=self)
        # 方法2：记录上一次的展开折叠状态
        self._RefreshWithState()

    def _QueueDurationCheck(self, task):
        """展示层只指出哪些行还需检测，去重、线程池及关闭处理由协调对象负责。"""
        if task.duration_pending and task.task_id:
            self.runner.queue_duration(task.task_id)

    def _DurationChecked(self, task_id, record, error):
        if not self or self.IsBeingDeleted() or self.runner.closed.is_set():
            return
        if error:
            self.runner.block_duration(task_id)
            wx.MessageBox(f'时长检测结果保存失败：{error}\n请刷新后重试。', '检测失败', parent=self)
            return
        # 后台快照可能早于其他分片的下载回调，必须重新读取最新记录再更新列表。
        try:
            record = self.tasks.repository.get(task_id)
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as exc:
            self._DurationChecked(task_id, None, str(exc))
            return
        if record is None:
            return
        index = self.model.ApplyTaskRecord(record)
        if index is None:
            return
        task = self.model.fileTree.items[index]
        if task.duration_pending:
            self._QueueDurationCheck(task)
            return
        self._CreateM3U8File(task.parent.fileName)
        failures = sum(s.duration_status == 'failed' for s in record.details.segments)
        if failures:
            self.statusBar.SetStatusText(f'{task.parent.displayName}：{failures} 个分片时长检测失败，保留原时长。', 0)
        pending = M3U8Downloader.Snapshot()['pending']
        if (task.task_status == TaskStatus.WAITING_MERGE
                and task.childs and task.download == len(task.childs) and not task.outputs
                and task_id not in self._completion_notified
                and not any(M3U8Downloader.FileKey(child.fileName) in pending for child in task.childs)):
            self._completion_notified.add(task_id)
            self.model._SendEvent({'task_id': task_id, 'fileName': task.parent.fileName})
        self.model.ItemChanged(self.model.ObjectToItem(self.model._BuildKey((index,))))

    def _M3U8Changed(self, task_id):
        """下载结果已在后台保存；这里只触发时长检测和现有的自动合并流程。"""
        for task in self.model.fileTree.items:
            if task.task_id != task_id:
                continue
            pending = M3U8Downloader.Snapshot()['pending']
            if (task.task_status == TaskStatus.WAITING_MERGE
                    and task.childs and task.download == len(task.childs) and not task.outputs
                    and task_id not in self._completion_notified
                    and not any(M3U8Downloader.FileKey(child.fileName) in pending for child in task.childs)):
                if task.duration_pending:
                    self._QueueDurationCheck(task)
                    break  # 检测回调负责发送完成事件，避免自动合并早于时长入库。
                self._completion_notified.add(task_id)
                self.model._SendEvent({'task_id': task_id, 'fileName': task.parent.fileName})
            break

    def _DownloadFiles(self, tsSeed: str, tasks: list):
        """先保存排队状态，再提交带稳定身份的分片请求。"""
        task = next((task for task in self.model.fileTree.items
                     if task.parent and task.parent.fileName == tsSeed), None)
        if task is not None and task.task_type != TaskType.M3U8:
            return 0  # 单文件任务不能误入 TS 分片下载器。
        if task is None or any(index < 0 or index >= len(task.childs) for index, _ in tasks):
            wx.MessageBox('任务不存在或分片已变化，请刷新后重试。', '提示')
            return 0
        children = [task.childs[index] for index, _ in tasks if not Path(task.childs[index].fileName).is_file()]
        if not children:
            return 0
        try:
            count = self.runner.start(task.task_id, sequences=[child.sequence for child in children])
            if count:
                self._completion_notified.discard(task.task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'无法启动分片下载：{error}', '下载失败', parent=self)
            return 0
        self._SyncDownloads()
        return count

    def _CreateM3U8File(self, tsSeed):
        """本地播放列表是可重建文件，丢失后从任务库恢复。"""
        task = next((task for task in self.model.fileTree.items
                     if task.parent and task.parent.fileName == tsSeed), None)
        try:
            if task is None:
                raise ValueError('任务不存在，请刷新后重试。')
            self.runner.write_playlist(task.task_id)
            return True
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'生成本地播放列表失败：{error}', '提示')
            return False

    def _CreateMP4File(self, tsSeed: str, item):
        """菜单事件只定位任务并显示错误；清单准备、合并及结果落库由 TaskRunner 负责。"""
        task = next((task for task in self.model.fileTree.items if task.parent.fileName == tsSeed), None)
        if task is None:
            return
        try:
            self.runner.merge(task.task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '合并失败', parent=self)
        self._SyncDownloads()
