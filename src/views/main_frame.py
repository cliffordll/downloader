import json
import textwrap
from pathlib import Path

import wx 
import wx.dataview as dv
from src.models.tree_model import MultiColumnTreeModel, EVT_ALL_DOWNLOAD
from src.views.ui_metrics import (
    MAIN_WINDOW_CONTENT_MARGIN_DIP,
    MAIN_WINDOW_TOOLBAR_MARGIN_DIP,
    OUTER_MARGIN_DIP,
)

from src.views.dialogs.information_dialog import InformationDialog
from src.views.dialogs.task_group_dialog import TaskGroupManagerDialog
from src.core.task_runner import TaskRunner
from src.core.http_service import LocalHTTPService
from src.core.services.browser_pairing import load_or_create_pairing, pairing_path


from src.core.sys_setting import SysSetting
from src.core.task_service import TaskService
from src.media.encrypted_file import is_encrypted_mp4
from src.models.file_base import TreeData
from src.schemas.task import TaskStatus

from src.views.components.icons import app_icons, toolbar_icon, set_titlebar_icon
from src.views.components.renderers import (
    TaskProgressRenderer, TaskActionRenderer, TaskSelectionRenderer,
)
from src.views.controllers.encryption_controller import EncryptionController
from src.views.controllers.playback_controller import PlaybackController
from src.views.controllers.service_controller import ServiceController
from src.views.controllers.browser_controller import BrowserController
from src.views.controllers.task_actions_controller import TaskActionsController
from src.views.controllers.task_list_controller import TaskListController
from src.views.controllers.download_controller import DownloadController
from src.views.controllers.task_media_controller import TaskMediaController
from src.views.controllers.batch_actions_controller import BatchActionsController


class MainFrame(wx.Frame):
    def __init__(self, parent, title, task_service: TaskService, initial_tree: TreeData):
        # super(MyFrame, self).__init__(parent, title=title)
        super().__init__(parent, title=title)
        self.playback = PlaybackController(self)
        self.encryption = EncryptionController(self)
        self.services = ServiceController(self)
        self.browser = BrowserController(self)
        self.task_actions = TaskActionsController(self)
        self.task_list = TaskListController(self)
        self.downloads = DownloadController(self)
        self.task_media = TaskMediaController(self)
        self.batch_actions = BatchActionsController(self)
        self._closing = False
        self.browser_bridge = None
        self._capture_dialog_open = False
        self._encrypted_tasks_unlocked = False
        self._encryption_context = None
        self._playback_service = None
        self._playback_sessions = set()
        preferred_service_port = SysSetting.GetAll()['playback_port']
        if not preferred_service_port and pairing_path().is_file():
            try:
                preferred_service_port = load_or_create_pairing(pairing_path())[1]
            except (OSError, ValueError):
                pass
        self._local_http_service = LocalHTTPService(
            port=preferred_service_port, on_port_assigned=self._RememberPlaybackPort)
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
        self._groupFilter = 'all'
        self._searchTimer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.OnSearchTimer, self._searchTimer)
        self._createMainPanel(initial_tree)
        # 记住默认窗口宽度；放大时按默认列比例扩展，恢复窗口时还原布局。
        self._defaultTaskWidth = self.GetClientSize().width
        self._task_display = {}
        self._completion_notified = set()
        self.runner = TaskRunner(self.tasks)
        self.runner.set_encryption_context_provider(lambda: self._encryption_context)
        # 显示前完成布局、列宽和状态缓存，避免先画初始列宽再跳到适配后的宽度。
        self.Layout()
        self.mcTree.GetParent().Layout()
        self._defaultTaskViewportWidth = self.mcTree.GetMainWindow().GetClientSize().width
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
        addMUItem   = fileMenu.Append(wx.ID_ANY, "&新建 M3U8 下载\tCtrl-M")
        addTSItem   = fileMenu.Append(wx.ID_ANY, "新建 TS 模板下载\tCtrl-T")
        addMP4Item = fileMenu.Append(wx.ID_ANY, '新建直链下载\tCtrl-P')
        openEncryptedItem = fileMenu.Append(wx.ID_ANY, '播放加密 MP4…')
        fileMenu.AppendSeparator()
        settingItem  = fileMenu.Append(wx.ID_ANY, "设置\tCtrl-,")
        playerItem = fileMenu.Append(wx.ID_ANY, '选择播放器')
        browserItem = fileMenu.Append(wx.ID_ANY, '浏览器扩展配对…')
        self.Bind(wx.EVT_MENU, self.OnSelectPlayer, playerItem)
        self.Bind(wx.EVT_MENU, self.OnBrowserConnect, browserItem)
        exitItem    = fileMenu.Append(wx.ID_EXIT, "&退出")
        # 绑定事件
        self.Bind(wx.EVT_MENU, self.OnOpen, openItem)
        self.Bind(wx.EVT_MENU, self.OnAddMU, addMUItem)
        self.Bind(wx.EVT_MENU, self.OnAddTS, addTSItem)
        self.Bind(wx.EVT_MENU, self.OnAddMP4, addMP4Item)
        self.Bind(wx.EVT_MENU, self.OnOpenEncryptedMP4, openEncryptedItem)
        self.Bind(wx.EVT_MENU, self.OnSetting, settingItem)
        self.Bind(wx.EVT_MENU, self.OnExit, exitItem)
		
        # 任务菜单负责下载控制，查看菜单负责列表及界面展示。
        taskMenu = wx.Menu()
        manageGroupsItem = taskMenu.Append(wx.ID_ANY, '管理分组…')
        self.Bind(wx.EVT_MENU, self.OnManageGroups, manageGroupsItem)
        taskMenu.AppendSeparator()
        self.downloadAllItem = taskMenu.Append(wx.ID_ANY, '全部下载\tCtrl-Shift-D')
        self.pauseAllItem = taskMenu.Append(wx.ID_ANY, '全部暂停\tCtrl-Shift-P')
        self.resumeAllItem = taskMenu.Append(wx.ID_ANY, '全部继续\tCtrl-Shift-R')
        can_pause, can_resume = self._GlobalDownloadActions()
        self.downloadAllItem.Enable(self.downloads.CanDownloadAll())
        self.pauseAllItem.Enable(can_pause)
        self.resumeAllItem.Enable(can_resume)
        self.Bind(wx.EVT_MENU, self.OnDownloadAll, self.downloadAllItem)
        self.Bind(wx.EVT_MENU, self.OnPauseAllDownloads, self.pauseAllItem)
        self.Bind(wx.EVT_MENU, self.OnResumeAllDownloads, self.resumeAllItem)
        self.Bind(wx.EVT_UPDATE_UI, self.OnUpdateGlobalDownloadAction, self.downloadAllItem)
        self.Bind(wx.EVT_UPDATE_UI, self.OnUpdateGlobalDownloadAction, self.pauseAllItem)
        self.Bind(wx.EVT_UPDATE_UI, self.OnUpdateGlobalDownloadAction, self.resumeAllItem)
        taskMenu.AppendSeparator()
        self.batchActionsMenu = self.batch_actions.CreateTaskSubMenu()
        self.batchActionsItem = taskMenu.AppendSubMenu(self.batchActionsMenu, '批量操作')
        taskMenu.AppendSeparator()
        self.setEncryptedPasswordItem = taskMenu.Append(wx.ID_ANY, '设置加密密码…')
        self.Bind(wx.EVT_MENU, self.OnSetEncryptedPassword, self.setEncryptedPasswordItem)
        self.encryptedTasksItem = taskMenu.Append(wx.ID_ANY, '显示加密任务…')
        self.Bind(wx.EVT_MENU, self.OnUnlockEncryptedTasks, self.encryptedTasksItem)
        self.lockEncryptedTasksItem = taskMenu.Append(wx.ID_ANY, '隐藏加密任务')
        self.lockEncryptedTasksItem.Enable(self._encrypted_tasks_unlocked)
        self.Bind(wx.EVT_MENU, self.OnLockEncryptedTasks, self.lockEncryptedTasksItem)

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
        viewMenu.AppendSeparator()
        serviceStatusItem = viewMenu.Append(wx.ID_ANY, '本机服务状态…')
        self.Bind(wx.EVT_MENU, self.OnShowLocalServiceStatus, serviceStatusItem)

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
        self.Bind(wx.EVT_MENU_OPEN, self.batch_actions.OnMenuOpen)

    def _createToolBar(self):
        if wx.Platform == '__WXMAC__':
            self.toolBar = None
            return
        self.toolBar = self.CreateToolBar(style=wx.TB_DEFAULT_STYLE)
        self.toolBar.SetMargins(self.FromDIP(MAIN_WINDOW_TOOLBAR_MARGIN_DIP), 0)
        # 由 BitmapBundle 按 DPI 选择图标；显式设置尺寸会干扰 wxMSW 的尺寸选择。
        # self.toolBar.SetToolBitmapSize(self.toolBar.FromDIP(wx.Size(24, 24)))


        def add_tool(tool_id, label, icon):
            bundle = toolbar_icon(icon)
            return self.toolBar.AddTool(tool_id, label, bundle, shortHelp=label)

        openButton = add_tool(wx.ID_OPEN, "打开下载文件夹", "folder-open")
        self._manageGroupsTool = add_tool(wx.ID_ANY, '管理分组', 'folder-settings')
        self.toolBar.Bind(wx.EVT_TOOL, self.OnManageGroups, self._manageGroupsTool)
        muButton = add_tool(wx.ID_ANY, "新建 M3U8 下载", "playlist")
        tsButton = add_tool(wx.ID_ANY, "新建 TS 模板下载", "segment")
        directButton = add_tool(wx.ID_ANY, '新建直链下载', 'url')
        self.toolBar.AddSeparator()
        self._downloadAllTool = add_tool(wx.ID_ANY, '全部下载', 'download-all')
        self.toolBar.SetToolShortHelp(self._downloadAllTool.GetId(), '开始下载所有尚未开始的任务')
        self._pauseTool = add_tool(wx.ID_ANY, "全部暂停", "task-pause")
        self._resumeTool = add_tool(wx.ID_ANY, "全部继续", "task-resume")
        self._pauseToolState = None
        self.toolBar.SetToolShortHelp(self._pauseTool.GetId(), '暂停所有正在下载的任务')
        self.toolBar.SetToolShortHelp(self._resumeTool.GetId(), '继续下载所有已暂停、部分完成或中断的任务')
        self._batchActionsTool = add_tool(wx.ID_ANY, '批量操作', 'batch')
        self.toolBar.SetToolShortHelp(self._batchActionsTool.GetId(), '对已勾选任务执行批量操作')
        self.toolBar.AddSeparator()
        expandButton = add_tool(wx.ID_ANY, "全部展开", "expand")
        collapseButton = add_tool(wx.ID_ANY, "全部折叠", "collapse")
        refreshButton = add_tool(wx.ID_REFRESH, "刷新", "refresh")
        self._encryptedTasksTool = add_tool(wx.ID_ANY, "显示加密任务", "unlock")
        self.toolBar.SetToolShortHelp(expandButton.GetId(), '展开当前列表中的全部任务')
        self.toolBar.SetToolShortHelp(collapseButton.GetId(), '折叠当前列表中的全部任务')
        self.toolBar.AddSeparator()
        browserButton = add_tool(wx.ID_ANY, '浏览器扩展配对', 'browser')
        self.toolBar.SetToolShortHelp(browserButton.GetId(), '获取浏览器扩展配对码')
        playerButton = add_tool(wx.ID_ANY, '选择播放器', 'player-setting')
        settingButton = add_tool(wx.ID_ANY, "设置", "settings")
        self.toolBar.AddSeparator()
        helpButton = add_tool(wx.ID_ANY, "帮助", "help")
        aboutButton = add_tool(wx.ID_ANY, "关于", "about")

        # self.toolBar.Bind(wx.EVT_TOOL, self.OnNew, newButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnOpen, openButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddMU, muButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddTS, tsButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddMP4, directButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnBrowserConnect, browserButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnDownloadAll, self._downloadAllTool)
        self.toolBar.Bind(wx.EVT_UPDATE_UI, self.OnUpdateGlobalDownloadAction, self._downloadAllTool)
        self.toolBar.Bind(wx.EVT_TOOL, self.batch_actions.OnShowBatchActions, self._batchActionsTool)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnExpandAll, expandButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnCollapseAll, collapseButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnPauseAllDownloads, self._pauseTool)
        self.toolBar.Bind(wx.EVT_UPDATE_UI, self.OnUpdatePauseDownloads, self._pauseTool)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnResumeAllDownloads, self._resumeTool)
        self.toolBar.Bind(wx.EVT_UPDATE_UI, self.OnUpdatePauseDownloads, self._resumeTool)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnRefresh, refreshButton)
        # Bind directly to the tool as well as the frame ID.  wxMSW versions differ
        # in whether toolbar command events bubble to the frame after realization.
        self.toolBar.Bind(wx.EVT_TOOL, self.OnEncryptedTasksTool,
                          self._encryptedTasksTool)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnSetting, settingButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnSelectPlayer, playerButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnHelp, helpButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAbout, aboutButton)
        # 启用工具栏
        self.toolBar.Realize()
        self._SyncLockEncryptedTasksEnabled()
        self.showToolItem.Check(self.toolBar.IsShown())

    def _createStatusBar(self):
        self.statusBar = self.CreateStatusBar()
        self.statusBar.SetFieldsCount(2)
        self.statusBar.SetStatusWidths([-1, -3])
        self._SetPrimaryStatusText('就绪')
        self.statusBar.SetStatusText('双击任务展开/折叠；下载请使用“操作”列', 1)
        self.showStatusItem.Check(self.statusBar.IsShown())

    def _SetPrimaryStatusText(self, text):
        # wx.StatusBar 第一字段没有左侧留白，用两个窄空格对齐主区域边缘。
        self.statusBar.SetStatusText(f'\u2009\u2009{text}', 0)

    def _createMainPanel(self, initial_tree):
        """创建主面板和布局"""
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # 筛选区域只放条件和结果数量，全局操作集中在菜单、工具栏。
        uriSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.searchCtrl = wx.SearchCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.searchCtrl.SetDescriptiveText('筛选文件名或路径')
        self.searchCtrl.ShowCancelButton(True)
        self.groupFilter = wx.Choice(panel, choices=[])
        self.task_list.RefreshGroupFilter()
        self.statusFilter = wx.Choice(panel, choices=[
            '全部状态', '未开始', '等待下载', '下载中', '暂停中', '已暂停', '已中断', '录制中', '停止中', '录制失败',
            '部分完成', '待继续', '下载失败', '检测时长', '待合并', '合并中', '合并失败', '已完成'])
        self.statusFilter.SetSelection(0)
        self.filterCount = wx.StaticText(panel, label='')
        self.filterCount.SetMinSize(self.FromDIP(wx.Size(140, -1)))
        uriSizer.Add(self.searchCtrl, proportion=1, flag=wx.EXPAND|wx.RIGHT, border=self.FromDIP(5))
        uriSizer.Add(self.groupFilter, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(8))
        uriSizer.Add(self.statusFilter, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(10))
        uriSizer.Add(self.filterCount, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.FromDIP(5))
        self.searchCtrl.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self.OnSearch)
        self.searchCtrl.Bind(wx.EVT_TEXT_ENTER, self.OnSearch)
        self.searchCtrl.Bind(wx.EVT_TEXT, self.OnSearchText)
        self.searchCtrl.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self.OnClearSearch)
        self.searchCtrl.Bind(wx.EVT_CHAR_HOOK, self.OnSearchKey)
        self.statusFilter.Bind(wx.EVT_CHOICE, self.OnStatusFilter)
        self.groupFilter.Bind(wx.EVT_CHOICE, self.OnGroupFilter)

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
        encrypted_indexes = {index for index, task in enumerate(initial_tree.items) if task.encrypted}
        if encrypted_indexes:
            self.model.visible_tasks = set(range(len(initial_tree.items))) - encrypted_indexes
        # 创建DataViewCtrl
        self.mcTree = dv.DataViewCtrl(
            panel, -1,
            style=(wx.BORDER_THEME | dv.DV_ROW_LINES | dv.DV_VERT_RULES
                   | dv.DV_VARIABLE_LINE_HEIGHT))
        if wx.Platform == '__WXMAC__':
            # 列表采用略小的字号；自绘进度与操作文字同样读取此字体。
            font = wx.Font(self.mcTree.GetFont())
            font.SetPointSize(max(10, font.GetPointSize() - 2))
            self.mcTree.SetFont(font)
        # Windows 下原生 RendererNative 不能通过 Python 重写其绘制回调。
        # 在列表内部窗口的原生绘制结束后统一替换选中边框。
        if wx.Platform == '__WXMSW__':
            self.mcTree.GetMainWindow().Bind(wx.EVT_PAINT, self.OnTaskListPaint)
        self.mcTree.AssociateModel(self.model)
        task_drag_format = self.task_list.task_drag_format
        self.mcTree.EnableDragSource(task_drag_format)
        self.mcTree.EnableDropTarget(task_drag_format)
        self._knownTaskPaths = {task.parent.fileName for task in self.model.fileTree.items if task.parent}
        self._UpdateFilterCount()
        # 添加多列
        selection_renderer = TaskSelectionRenderer(self)
        selection_column = dv.DataViewColumn('选择', selection_renderer, 7,
                                             width=self.FromDIP(42), align=wx.ALIGN_CENTER)
        self.mcTree.AppendColumn(selection_column)
        self.mcTree.AppendTextColumn("序列", 0, width=self.FromDIP(60))
        # # 自定义列
        # renderer = dv.DataViewTextRenderer()
        # renderer.EnableEllipsize(wx.ELLIPSIZE_END)
        # self.mcTree.AppendColumn(dv.DataViewColumn("文件名", renderer, 1, width=180, align=wx.ALIGN_LEFT))
        # self.mcTree.AppendTextColumn("文件名", 1, width=500)
        nameColumn = self.mcTree.AppendTextColumn("名称", 1, width=self.FromDIP(250))
        self.mcTree.SetExpanderColumn(nameColumn)
        self.mcTree.SetIndent(self.FromDIP(16))
        self.mcTree.AppendColumn(dv.DataViewColumn('下载进度', TaskProgressRenderer(self), 5,
                                                 width=self.FromDIP(180), align=wx.ALIGN_CENTER))
        self.mcTree.AppendTextColumn("状态", 6, width=self.FromDIP(80), align=wx.ALIGN_CENTER)
        self.mcTree.AppendTextColumn("文件大小", 2, width=self.FromDIP(90), align=wx.ALIGN_RIGHT)
        self.mcTree.AppendTextColumn("修改时间", 3, width=self.FromDIP(130))
        self._actionRenderer = TaskActionRenderer(self)
        initial_action_width = self.FromDIP(160 if wx.Platform == '__WXMAC__' else 192)
        actionColumn = dv.DataViewColumn("操作", self._actionRenderer, 4,
                                       width=initial_action_width, align=wx.ALIGN_CENTER)
        # Mac 原生控件在首次布局时会重新调整列宽，必须同时约束原生最小宽度。
        actionColumn.SetMinWidth(initial_action_width)
        self.mcTree.AppendColumn(actionColumn)
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_EXPANDED, self._QueueRowNumbers)
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_COLLAPSED, self._QueueRowNumbers)
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_BEGIN_DRAG, self.task_list.OnTaskBeginDrag)
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_DROP_POSSIBLE,
                         self.task_list.OnTaskDropPossible)
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_DROP, self.task_list.OnTaskDrop)
        self.mcTree.GetMainWindow().Bind(wx.EVT_SIZE, self.OnTaskListSize)
        self.mcTree.GetMainWindow().Bind(wx.EVT_LEFT_UP,
                                         self.task_list.OnTaskDragMouseUp)
        group_items = []
        self.model.GetChildren(dv.NullDataViewItem, group_items)
        for group_item in group_items:
            if self.model.IsContainer(group_item):
                self.mcTree.Expand(group_item)
        for index in range(self.mcTree.GetColumnCount()):
            column = self.mcTree.GetColumn(index)
            column.GetRenderer().EnableEllipsize(wx.ELLIPSIZE_END)
            column.SetFlags(column.GetFlags() & ~wx.COL_RESIZABLE)
        # self.mcTree.AppendTextColumn("下载地址", 5)
        # self.model.DecRef()  # 避免内存泄漏
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
            self.mcTree.GetMainWindow().Bind(wx.EVT_LEFT_DOWN, self.OnTaskListMouseDown)
            self.mcTree.GetMainWindow().Bind(wx.EVT_LEFT_UP, self.OnTaskListClick)
        self._taskTip = ''
        self._taskTipWindow = None
        self._taskTipDelay = None
        # Windows 的表格正文可能是子窗口，Mac 则使用原生表格窗口。
        for window in {self.mcTree, self.mcTree.GetMainWindow()}:
            window.Bind(wx.EVT_MOTION, self.OnTaskHover)
            for event_type in (wx.EVT_LEAVE_WINDOW, wx.EVT_MOUSEWHEEL, wx.EVT_SCROLLWIN,
                               wx.EVT_LEFT_DOWN, wx.EVT_RIGHT_DOWN, wx.EVT_KILL_FOCUS):
                window.Bind(event_type, self.ClearTaskToolTip)
        self.Bind(EVT_ALL_DOWNLOAD, self.OnAllTSDownload)

        sizer.Add(uriSizer, flag=wx.EXPAND, border=0)
        sizer.Add(self.emptyPanel, flag=wx.EXPAND | wx.TOP | wx.BOTTOM, border=self.FromDIP(12))
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND|wx.ALL, border=0)
        # 设置面板的sizer
        contentSizer = wx.BoxSizer(wx.VERTICAL)
        contentSizer.Add(sizer, proportion=1,
                         flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                         border=self.FromDIP(MAIN_WINDOW_CONTENT_MARGIN_DIP))
        panel.SetSizer(contentSizer)
        if wx.Platform == '__WXMAC__':
            frameSizer = wx.BoxSizer(wx.VERTICAL)
            frameSizer.Add(panel, proportion=1, flag=wx.EXPAND)
            self.SetSizer(frameSizer)


    def ClearTaskToolTip(self, event=None):
        delay = getattr(self, '_taskTipDelay', None)
        if delay is not None:
            delay.Stop()
            self._taskTipDelay = None
        popup = getattr(self, '_taskTipWindow', None)
        if popup is not None:
            popup.Hide()
            popup.Destroy()
            self._taskTipWindow = None
        self._taskTip = ''
        if event is not None:
            event.Skip()

    def _ShowTaskToolTip(self):
        # 系统 ToolTip 会超时消失；使用普通浮窗，只在离开、滚动等事件中关闭。
        self._taskTipDelay = None
        if self._closing or not self._taskTip:
            return
        popup = wx.PopupWindow(self, wx.BORDER_SIMPLE)
        colour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_INFOBK)
        popup.SetBackgroundColour(colour)
        label = wx.StaticText(popup, label=self._taskTip)
        label.SetBackgroundColour(colour)
        label.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_INFOTEXT))
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(label, flag=wx.ALL, border=self.FromDIP(6))
        popup.SetSizerAndFit(layout)
        popup.Position(wx.GetMousePosition(), self.FromDIP(wx.Size(12, 20)))
        self._taskTipWindow = popup
        popup.Show()

    def OnTaskHover(self, event):
        event.Skip()
        if self._closing or event.Dragging():
            return
        source = event.GetEventObject()
        pos = self.mcTree.ScreenToClient(source.ClientToScreen(event.GetPosition()))
        item, column = self.mcTree.HitTest(pos)
        text = ''
        if item.IsOk() and column is not None and not event.Dragging():
            if column.GetModelColumn() != 4:
                text = self.model.GetItemToolTip(item)
            else:
                value = self.model.GetValue(item, 4)
                if value.startswith('['):
                    actions = json.loads(value)
                    cell = wx.Rect(self.mcTree.GetItemRect(item, column))
                    if wx.Platform == '__WXMAC__':
                        width = min(cell.width, self._actionRenderer.GetSize().width)
                        cell.x += (cell.width - width) // 2
                        cell.width = width
                    for action, rect in zip(actions, self._actionRenderer._ActionRects(cell, len(actions))):
                        if rect.Contains(pos):
                            text = action['label'] + ('任务' if action['id'] != 'more' else '操作')
                            if not action['enabled']:
                                text += '（当前不可用）'
                            break
                elif value:
                    text = '下载此分片'
        # 长 URL 只在提示中折行；数据与“查看下载地址”的内容保持原样。
        text = '\n'.join(textwrap.fill(line, width=90, replace_whitespace=False,
                                       break_on_hyphens=False) for line in text.splitlines())
        if text != self._taskTip:
            self.ClearTaskToolTip()
            if text:
                self._taskTip = text
                self._taskTipDelay = wx.CallLater(500, self._ShowTaskToolTip)

    def OnTaskListMouseDown(self, event):
        """Mac 行首箭头使用统一的程序展开路径。"""
        self._disclosureClick = False
        if self._closing:
            event.Skip()
            return
        source = event.GetEventObject()
        pos = self.mcTree.ScreenToClient(source.ClientToScreen(event.GetPosition()))
        item, column = self.mcTree.HitTest(pos)
        if not item.IsOk() or column is None or column != self.mcTree.GetExpanderColumn():
            event.Skip()
            return
        keys = self.model.ParseKey(self.model.ItemToObject(item))
        if not self.model.IsContainer(item):
            event.Skip()
            return
        cell = self.mcTree.GetItemRect(item, column)
        arrow_width = max(self.mcTree.GetIndent(), self.FromDIP(16))
        if cell.IsEmpty() or not cell.x - arrow_width <= pos.x < cell.x:
            event.Skip()
            return
        self._disclosureClick = True
        self.mcTree.SetFocus()
        self.mcTree.Select(item)
        self._SetTaskExpanded(item)
        event.Skip(False)

    def OnTaskListClick(self, event):
        """Mac 松开鼠标后分发操作，避免在按下事件中打开原生菜单或弹窗。"""
        if getattr(self, '_disclosureClick', False):
            self._disclosureClick = False
            event.Skip(False)
            return
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
        # Cocoa may revise native DataView column widths on first show; Windows
        # and GTK already use the pre-show fit, so refitting there causes a
        # visible one-frame jump in the action column.
        if (wx.Platform == '__WXMAC__'
                and event.GetEventObject() is self and event.IsShown()):
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
        if getattr(getattr(self, 'task_actions', None), '_group_move_pending', False) is True:
            return
        # CallAfter 执行时窗口可能已经关闭，此时不能再访问原生控件。
        if self._closing or not self or self.IsBeingDeleted() or not self.mcTree:
            return
        # 序列列按可见行总数留宽；层级缩进由名称列承载。
        font = wx.Font(self.mcTree.GetFont())
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        dc = wx.ClientDC(self.mcTree)
        dc.SetFont(font)
        root_digits = len(str(max(1, len(self.model.row_numbers))))
        child_count = max((len(task.childs) for task in self.model.fileTree.items), default=0)
        sequence_width = max(self.FromDIP(40), dc.GetTextExtent('9' * root_digits)[0] + self.FromDIP(12))
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
        main_view_width = self.mcTree.GetMainWindow().GetClientSize().width
        # Clamp against the outer control for synthetic size changes before wx lays out its child.
        view_width = min(main_view_width, self.mcTree.GetClientSize().width)
        size_key = (self.mcTree.GetSize().width, view_width, self.FromDIP(100), sequence_width,
                    action_width, date_width, progress_width)
        if getattr(self, '_columnSizeKey', None) == size_key:
            return
        # 使用原生列表视口宽度；它已按实际滚动条显隐扣除空间。
        column_spacing = (self.FromDIP(17) * self.mcTree.GetColumnCount()
                          if wx.Platform == '__WXMAC__' else 0)
        available = view_width - column_spacing
        if available <= 0:
            return
        self._columnSizeKey = size_key
        # 按界面显示顺序：选择、序列、文件名、下载进度、状态、文件大小、修改时间、操作。
        # 数字是 DIP，由 FromDIP 按系统缩放换算；文件名的 0 是占位，下面补入剩余宽度。
        widths = [self.FromDIP(value) for value in (42, 50, 0, 180, 80, 85, 125, 200)]
        if wx.Platform == '__WXMAC__':
            # 辅助信息列保持紧凑，把可用空间优先留给文件名。
            widths[3:6] = [progress_width, self.FromDIP(65), self.FromDIP(70)]
        widths[1] = sequence_width
        widths[6] = date_width
        widths[-1] = action_width
        # 默认窗口的可用宽度是比例分配的基准，不随最大化/还原反复改变。
        baseline = self._defaultTaskViewportWidth - column_spacing
        # 不超过默认宽度时，其他列保持基准值，剩余空间交给文件名列。
        widths[2] = max(self.FromDIP(80), min(available, baseline) - sum(widths))
        if wx.Platform == '__WXMAC__' and available > baseline > 0:
            widths[2] += available - baseline
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
            remaining = available - widths[0] - widths[1] - widths[2] - widths[-1]
            flexible_end = 6 if wx.Platform == '__WXMAC__' else 7
            if wx.Platform == '__WXMAC__':
                # Mac 日期列保留完整时间所需宽度，压缩其余信息列。
                remaining -= widths[6]
            if remaining >= flexible_end - 2:
                total = sum(widths[3:flexible_end])
                widths[3:flexible_end] = [max(1, value * remaining // total)
                                          for value in widths[3:flexible_end]]
            else:
                # 初始化时控件可能暂时小于窗口最小尺寸，待布局完成后重新适配。
                self._columnSizeKey = None
                return
        # 批量调整时暂停重绘，完成后统一恢复。
        self.mcTree.Freeze()  # wx 方法：暂停该控件的屏幕重绘，期间仍可修改列宽。
        try:
            self.mcTree.GetColumn(7).SetMinWidth(action_width)
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
        if self.browser_bridge is not None:
            self.browser_bridge.close()
            self.browser_bridge = None
        self._ClosePlaybackServers()
        self.ClearTaskToolTip()
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
        if self.task_actions._group_move_pending and event.CanVeto():
            self._SetPrimaryStatusText('正在移动任务，请等待完成后关闭窗口…')
            event.Veto()
            return
        self._PrepareClose()
        event.Skip()

    def Destroy(self):
        self._PrepareClose()
        return super().Destroy()

    def OnDestroy(self, event):
        if event.GetEventObject() is self:
            self._PrepareClose()
        event.Skip()

    def OnTaskProgress(self, *args, **kwargs):
        return self.downloads.OnTaskProgress(*args, **kwargs)



    def OnTaskContextMenu(self, event):
        self.OnTaskMenu(event.GetItem())

    def _OpenLocalPath(self, *args, **kwargs):
        return self.playback._OpenLocalPath(*args, **kwargs)


    def _OpenWithPlayer(self, *args, **kwargs):
        return self.playback._OpenWithPlayer(*args, **kwargs)


    def _OpenVideo(self, *args, **kwargs):
        return self.playback._OpenVideo(*args, **kwargs)


    def _TaskPlaybackContext(self, *args, **kwargs):
        return self.playback._TaskPlaybackContext(*args, **kwargs)


    def _FindEncryptedMP4Task(self, *args, **kwargs):
        return self.playback._FindEncryptedMP4Task(*args, **kwargs)


    def _PlayEncryptedMP4(self, *args, **kwargs):
        return self.playback._PlayEncryptedMP4(*args, **kwargs)


    def _PlayEncryptedM3U8(self, *args, **kwargs):
        return self.playback._PlayEncryptedM3U8(*args, **kwargs)


    def _OpenTaskManifest(self, *args, **kwargs):
        return self.playback._OpenTaskManifest(*args, **kwargs)


    def _ChoosePlayerAndOpenManifest(self, *args, **kwargs):
        return self.playback._ChoosePlayerAndOpenManifest(*args, **kwargs)


    def _ClosePlaybackServers(self, *args, **kwargs):
        return self.playback._ClosePlaybackServers(*args, **kwargs)


    def _ChoosePlayer(self, *args, **kwargs):
        return self.playback._ChoosePlayer(*args, **kwargs)


    def _ChoosePlayerAndOpen(self, *args, **kwargs):
        return self.playback._ChoosePlayerAndOpen(*args, **kwargs)


    def OnSelectPlayer(self, *args, **kwargs):
        return self.playback.OnSelectPlayer(*args, **kwargs)


    def _SaveFileAs(self, *args, **kwargs):
        return self.task_actions._SaveFileAs(*args, **kwargs)


    def _ViewPlaylist(self, *args, **kwargs):
        return self.task_actions._ViewPlaylist(*args, **kwargs)


    def _EditSegmentInfo(self, *args, **kwargs):
        return self.task_actions._EditSegmentInfo(*args, **kwargs)


    def _DetectSegmentDurations(self, *args, **kwargs):
        return self.task_actions._DetectSegmentDurations(*args, **kwargs)


    def _CheckAndContinue(self, *args, **kwargs):
        return self.task_actions._CheckAndContinue(*args, **kwargs)


    def OnTaskMenu(self, *args, **kwargs):
        return self.task_actions.OnTaskMenu(*args, **kwargs)

    def OnManageGroups(self, event=None):
        dialog = TaskGroupManagerDialog(self, self.tasks.repository, SysSetting.GetWorkPath(),
                                       self.tasks, self.runner)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
        self.OnRefresh(None)


    def _RenameTask(self, *args, **kwargs):
        return self.task_actions._RenameTask(*args, **kwargs)


    def _SetTaskEncrypted(self, *args, **kwargs):
        return self.encryption._SetTaskEncrypted(*args, **kwargs)


    def _GetEncryptionContextForOperation(self, *args, **kwargs):
        return self.encryption._GetEncryptionContextForOperation(*args, **kwargs)



    ###################################
    ### 事件所需函数
    ###################################
    def _SearchItems(self, *args, **kwargs):
        return self.task_list._SearchItems(*args, **kwargs)


    def _UpdateFilterCount(self, *args, **kwargs):
        return self.task_list._UpdateFilterCount(*args, **kwargs)


    def OnUnlockEncryptedTasks(self, *args, **kwargs):
        return self.encryption.OnUnlockEncryptedTasks(*args, **kwargs)


    def OnLockEncryptedTasks(self, *args, **kwargs):
        return self.encryption.OnLockEncryptedTasks(*args, **kwargs)


    def OnEncryptedTasksTool(self, *args, **kwargs):
        return self.encryption.OnEncryptedTasksTool(*args, **kwargs)


    def _SyncLockEncryptedTasksEnabled(self, *args, **kwargs):
        return self.encryption._SyncLockEncryptedTasksEnabled(*args, **kwargs)


    def OnSetEncryptedPassword(self, *args, **kwargs):
        return self.encryption.OnSetEncryptedPassword(*args, **kwargs)


    def _ApplyTaskFilter(self, *args, **kwargs):
        return self.task_list._ApplyTaskFilter(*args, **kwargs)


    def _RecursiveExpand(self, *args, **kwargs):
        return self.task_list._RecursiveExpand(*args, **kwargs)


    def _RefreshWithState(self, *args, **kwargs):
        return self.task_list._RefreshWithState(*args, **kwargs)


    def _SaveExpandState(self, *args, **kwargs):
        return self.task_list._SaveExpandState(*args, **kwargs)


    def _RestoreExpandState(self, *args, **kwargs):
        return self.task_list._RestoreExpandState(*args, **kwargs)



    ###################################
    ### 操作菜单的事件
    ################################### 
    def OnOpen(self, event):
        """打开当前默认下载目录；单个任务的原目录由行内“更多”菜单打开。"""
        self._OpenLocalPath(SysSetting.GetWorkPath())

    def OnOpenEncryptedMP4(self, _event):
        dialog = wx.FileDialog(self, '选择加密 MP4 文件',
                               wildcard='加密 MP4 文件 (*.mp4.enc)|*.mp4.enc|所有文件 (*.*)|*.*',
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            path = Path(dialog.GetPath())
        finally:
            dialog.Destroy()
        if not is_encrypted_mp4(path):
            wx.MessageBox('所选文件不是 AVDownloader 加密 MP4。', '无法播放',
                          wx.OK | wx.ICON_WARNING, parent=self)
            return
        self._PlayEncryptedMP4(path)

    def _ConfiguredBrowserPort(self, *args, **kwargs):
        return self.services._ConfiguredBrowserPort(*args, **kwargs)


    def _PlaybackServiceStatus(self, *args, **kwargs):
        return self.services._PlaybackServiceStatus(*args, **kwargs)


    def _RememberPlaybackPort(self, *args, **kwargs):
        return self.services._RememberPlaybackPort(*args, **kwargs)


    def OnShowLocalServiceStatus(self, *args, **kwargs):
        return self.services.OnShowLocalServiceStatus(*args, **kwargs)

    
    def OnAddMU(self, *args, **kwargs):
        return self.task_media.OnAddMU(*args, **kwargs)


    def OnBrowserConnect(self, *args, **kwargs):
        return self.browser.OnBrowserConnect(*args, **kwargs)


    def RestoreBrowserConnection(self, *args, **kwargs):
        return self.browser.RestoreBrowserConnection(*args, **kwargs)


    def _CheckBrowserInbox(self, *args, **kwargs):
        return self.browser._CheckBrowserInbox(*args, **kwargs)


    def _OpenBrowserCapture(self, *args, **kwargs):
        return self.browser._OpenBrowserCapture(*args, **kwargs)


    def OnAddTS(self, *args, **kwargs):
        return self.task_media.OnAddTS(*args, **kwargs)

    
    def OnAddMP4(self, *args, **kwargs):
        return self.task_media.OnAddMP4(*args, **kwargs)


    def _StartMP4(self, *args, **kwargs):
        return self.downloads._StartMP4(*args, **kwargs)


    def _SyncDownloads(self, *args, **kwargs):
        return self.downloads._SyncDownloads(*args, **kwargs)


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



    def OnUpdateGlobalDownloadAction(self, *args, **kwargs):
        return self.downloads.OnUpdateGlobalDownloadAction(*args, **kwargs)


    def OnUpdatePauseDownloads(self, *args, **kwargs):
        return self.downloads.OnUpdatePauseDownloads(*args, **kwargs)


    def OnDownloadAll(self, *args, **kwargs):
        return self.downloads.OnDownloadAll(*args, **kwargs)


    def _UpdatePauseTool(self, *args, **kwargs):
        return self.downloads._UpdatePauseTool(*args, **kwargs)


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

    def _ShowInformation(self, title, text, *, line_numbers=False):
        dlg = InformationDialog(self, title, text, usage=title == '使用帮助', line_numbers=line_numbers)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def OnHelp(self, event):
        self._ShowInformation('使用帮助', (
            '1. 添加任务\n'
            '通过“文件 → 新建 M3U8 下载”输入 M3U8 播放列表网址，或通过“新建 TS 模板下载”按分片命名规则创建任务。\n\n'
            '通过“文件 → 新建直链下载”（Ctrl+P）添加可直接下载的文件地址。三个下载窗口均可选择保存目录和文件名，'
            '默认使用设置中的下载目录，文件名自动生成、可修改，重名自动追加序号。\n'
            'MP4 直接保存在所选目录；M3U8、TS 的分片放入同名子目录，合并后的 MP4 位于外层保存目录。\n\n'
            '筛选区域可按文件名、路径和任务状态筛选；关键词也匹配任务下的 MP4 和分片。'
            '清空关键词并选择“全部状态”恢复全部任务。隐藏任务仍继续下载。\n\n'
            '2. 下载与合并\n'
            '任务行固定显示“下载/暂停/继续、重试、删除、更多”，不可用的操作会置灰。'
            '“更多”或右键菜单提供转 MP4、播放视频、打开文件夹和另存为。'
            '已完成的视频可另存到其他目录，原文件和任务记录保持不变。删除默认同时将任务文件移入回收站，可取消勾选以保留文件。'
            '播放视频会调用已选择的外部播放器；VLC 多窗口设置见“5. 播放器”。'
            '行内“暂停/继续”只控制当前任务，已发出的请求允许完成。进度按已完成分片数计算。\n'
            '双击任务行展开或折叠分片列表；下载请点击操作列中的下载或继续。'
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
            '下载窗口内的“？”可查看对应输入框的说明。\n\n'
            '5. 播放器\n'
            '视频通过“更多 → 播放视频”或右键菜单交给已选择的外部播放器打开。\n'
            '若 VLC 每次打开视频都复用同一窗口，请在 VLC“工具 → 偏好设置 → 界面 → 播放列表和实例”中取消“仅允许一个实例”和“从文件管理器启动时，仅允许一个实例”，保存后重试。'
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
    def OnFind(self, *args, **kwargs):
        return self.task_list.OnFind(*args, **kwargs)


    def OnSearch(self, *args, **kwargs):
        return self.task_list.OnSearch(*args, **kwargs)

    
    def OnSearchText(self, *args, **kwargs):
        return self.task_list.OnSearchText(*args, **kwargs)


    def OnSearchKey(self, *args, **kwargs):
        return self.task_list.OnSearchKey(*args, **kwargs)


    def OnClearSearch(self, *args, **kwargs):
        return self.task_list.OnClearSearch(*args, **kwargs)


    def OnClearAllFilters(self, *args, **kwargs):
        return self.task_list.OnClearAllFilters(*args, **kwargs)


    def OnStatusFilter(self, *args, **kwargs):
        return self.task_list.OnStatusFilter(*args, **kwargs)


    def OnGroupFilter(self, *args, **kwargs):
        return self.task_list.OnGroupFilter(*args, **kwargs)


    def OnSearchTimer(self, *args, **kwargs):
        return self.task_list.OnSearchTimer(*args, **kwargs)


    def OnExpandAll(self, *args, **kwargs):
        return self.task_list.OnExpandAll(*args, **kwargs)

    
    def OnCollapseAll(self, *args, **kwargs):
        return self.task_list.OnCollapseAll(*args, **kwargs)


    def OnRefresh(self, *args, **kwargs):
        return self.task_list.OnRefresh(*args, **kwargs)


    def OnActivatedChanged(self, *args, **kwargs):
        return self.task_list.OnActivatedChanged(*args, **kwargs)


    # 窗口把点击位置转换为稳定的任务 UUID；执行规则由 TaskRunner 重新核对。
    def _SetTaskExpanded(self, *args, **kwargs):
        return self.task_list._SetTaskExpanded(*args, **kwargs)


    def _QueueRowNumbers(self, *args, **kwargs):
        return self.task_list._QueueRowNumbers(*args, **kwargs)


    def _UpdateRowNumbers(self, *args, **kwargs):
        return self.task_list._UpdateRowNumbers(*args, **kwargs)


    def OnTaskAction(self, *args, **kwargs):
        return self.task_actions.OnTaskAction(*args, **kwargs)


    def _QueueTaskPopup(self, *args, **kwargs):
        return self.task_actions._QueueTaskPopup(*args, **kwargs)


    def _ShowTaskPopup(self, *args, **kwargs):
        return self.task_actions._ShowTaskPopup(*args, **kwargs)


    def OnDeleteTask(self, *args, **kwargs):
        return self.task_actions.OnDeleteTask(*args, **kwargs)


    def _GlobalDownloadActions(self, *args, **kwargs):
        return self.downloads._GlobalDownloadActions(*args, **kwargs)


    def OnPauseAllDownloads(self, *args, **kwargs):
        return self.downloads.OnPauseAllDownloads(*args, **kwargs)


    def OnResumeAllDownloads(self, *args, **kwargs):
        return self.downloads.OnResumeAllDownloads(*args, **kwargs)


    def OnSegmentDownload(self, *args, **kwargs):
        return self.task_actions.OnSegmentDownload(*args, **kwargs)


    def OnAllTSDownload(self, *args, **kwargs):
        return self.task_actions.OnAllTSDownload(*args, **kwargs)


    def _QueueDurationCheck(self, *args, **kwargs):
        return self.task_actions._QueueDurationCheck(*args, **kwargs)


    def _DurationChecked(self, *args, **kwargs):
        return self.task_actions._DurationChecked(*args, **kwargs)


    def _M3U8Changed(self, *args, **kwargs):
        return self.task_actions._M3U8Changed(*args, **kwargs)


    def _DownloadFiles(self, *args, **kwargs):
        return self.task_media._DownloadFiles(*args, **kwargs)


    def _CreateM3U8File(self, *args, **kwargs):
        return self.task_media._CreateM3U8File(*args, **kwargs)


    def _CreateMP4File(self, *args, **kwargs):
        return self.task_media._CreateMP4File(*args, **kwargs)
