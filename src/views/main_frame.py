import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from queue import Empty
from pathlib import Path
from threading import Thread

import wx 
import wx.dataview as dv
from src.models.tree_model import MultiColumnTreeModel, EVT_ALL_DOWNLOAD, load_tree
from src.views.ui_metrics import OUTER_MARGIN_DIP

from src.views.dialogs.m3u8_dialog import DownloadDialogMU
from src.views.dialogs.ts_dialog import DownloadDialogTS
from src.views.dialogs.mp4_dialog import DownloadDialogMP4
from src.views.dialogs.information_dialog import InformationDialog
from src.views.dialogs.segment_edit_dialog import SegmentEditDialog
from src.views.dialogs.browser_connect_dialog import BrowserConnectDialog
from src.views.dialogs.player_select_dialog import PlayerSelectDialog
from src.views.dialogs.task_rename_dialog import TaskRenameDialog
from src.views.dialogs.task_unlock_dialog import TaskUnlockDialog
from src.views.dialogs.task_password_dialog import TaskPasswordDialog
from src.views.dialogs.service_status_dialog import ServiceStatusDialog
from src.core.task_runner import TaskRunner
from src.core.services.browser_bridge import BrowserBridge
from src.core.services.playback_http import PlaybackHTTPServer
from src.core.http_service import LocalHTTPService
from src.core.services.browser_pairing import load_or_create_pairing, pairing_path
from src.core.file_export import save_file_as


from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.core.sys_setting import SysSetting
from src.core.media_players import discover_media_players
from src.core.task_service import TaskService
from src.core.task_files import output_for, output_path
from src.core.task_encryption import (
    TaskEncryption,
    plan_task_artifacts, prepare_task_artifacts, publish_task_artifacts,
)
from src.media.mp4.encrypted_file import is_encrypted_mp4
from src.media.mp4.encrypted_file import EncryptedMP4Reader, context_from_password
from src.models.file_base import TreeData
from src.core.path_manager import PathManager
from src.storage.task_repository import TaskDataError, TaskConflictError
from src.schemas.task import FileStatus, TaskStatus, TaskType

from src.views.components.icons import app_icons, icon_image, toolbar_icon, set_titlebar_icon
from src.views.components.renderers import TaskProgressRenderer, TaskActionRenderer


class MainFrame(wx.Frame):
    def __init__(self, parent, title, task_service: TaskService, initial_tree: TreeData):
        # super(MyFrame, self).__init__(parent, title=title)
        super().__init__(parent, title=title)
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
        addMUItem   = fileMenu.Append(wx.ID_ANY, "&下载 M3U8\tCtrl-M")
        addTSItem   = fileMenu.Append(wx.ID_ANY, "&下载 TS\tCtrl-T")
        addMP4Item = fileMenu.Append(wx.ID_ANY, '下载 MP4\tCtrl-P')
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
        self.pauseAllItem = taskMenu.Append(wx.ID_ANY, '全部暂停\tCtrl-Shift-P')
        self.resumeAllItem = taskMenu.Append(wx.ID_ANY, '全部继续\tCtrl-Shift-R')
        can_pause, can_resume = self._GlobalDownloadActions()
        self.pauseAllItem.Enable(can_pause)
        self.resumeAllItem.Enable(can_resume)
        self.Bind(wx.EVT_MENU, self.OnPauseAllDownloads, self.pauseAllItem)
        self.Bind(wx.EVT_MENU, self.OnResumeAllDownloads, self.resumeAllItem)
        self.Bind(wx.EVT_UPDATE_UI, self.OnUpdateGlobalDownloadAction, self.pauseAllItem)
        self.Bind(wx.EVT_UPDATE_UI, self.OnUpdateGlobalDownloadAction, self.resumeAllItem)
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

    def _createToolBar(self):
        if wx.Platform == '__WXMAC__':
            self.toolBar = None
            return
        self.toolBar = self.CreateToolBar(style=wx.TB_DEFAULT_STYLE)
        self.toolBar.SetMargins(self.FromDIP(OUTER_MARGIN_DIP), 0)
        # 由 BitmapBundle 按 DPI 选择图标；显式设置尺寸会干扰 wxMSW 的尺寸选择。
        # self.toolBar.SetToolBitmapSize(self.toolBar.FromDIP(wx.Size(24, 24)))


        def add_tool(tool_id, label, icon):
            bundle = toolbar_icon(icon)
            return self.toolBar.AddTool(tool_id, label, bundle, shortHelp=label)

        openButton = add_tool(wx.ID_OPEN, "打开下载文件夹", "open")
        muButton = add_tool(wx.ID_ANY, "下载 M3U8", "playlist")
        tsButton = add_tool(wx.ID_ANY, "下载 TS", "segment")
        mp4Button = add_tool(wx.ID_ANY, '下载 MP4', 'mp4')
        browserButton = add_tool(wx.ID_ANY, '浏览器扩展配对', 'browser')
        self.toolBar.SetToolShortHelp(browserButton.GetId(), '获取浏览器扩展配对码')
        self.toolBar.AddSeparator()
        expandButton = add_tool(wx.ID_ANY, "全部展开", "expand")
        collapseButton = add_tool(wx.ID_ANY, "全部折叠", "collapse")
        self.toolBar.SetToolShortHelp(expandButton.GetId(), '展开当前列表中的全部任务')
        self.toolBar.SetToolShortHelp(collapseButton.GetId(), '折叠当前列表中的全部任务')
        self._pauseTool = add_tool(wx.ID_ANY, "全部暂停", "pause")
        self._pauseIcons = {False: toolbar_icon('task-pause'), True: toolbar_icon('task-resume')}
        self._pauseToolState = None
        refreshButton = add_tool(wx.ID_REFRESH, "刷新", "refresh")
        self.toolBar.AddSeparator()
        self._encryptedTasksTool = add_tool(wx.ID_ANY, "显示加密任务", "unlock")
        settingButton = add_tool(wx.ID_ANY, "设置", "settings")
        playerButton = add_tool(wx.ID_ANY, '选择播放器', 'player')
        self.toolBar.AddSeparator()
        helpButton = add_tool(wx.ID_ANY, "帮助", "help")
        aboutButton = add_tool(wx.ID_ANY, "关于", "about")

        # self.toolBar.Bind(wx.EVT_TOOL, self.OnNew, newButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnOpen, openButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddMU, muButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddTS, tsButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddMP4, mp4Button)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnBrowserConnect, browserButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnExpandAll, expandButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnCollapseAll, collapseButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnPauseDownloads, self._pauseTool)
        self.toolBar.Bind(wx.EVT_UPDATE_UI, self.OnUpdatePauseDownloads, self._pauseTool)
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
        # wx.StatusBar has no per-field text inset; an en space provides the
        # visual left padding while keeping every primary status message aligned.
        self.statusBar.SetStatusText(f'\u2002{text}', 0)

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
            '部分完成', '待继续', '下载失败', '检测时长', '待合并', '合并中', '合并失败', '已完成'])
        self.statusFilter.SetSelection(0)
        self.filterCount = wx.StaticText(panel, label='')
        self.filterCount.SetMinSize(self.FromDIP(wx.Size(140, -1)))
        uriSizer.Add(self.searchCtrl, proportion=1, flag=wx.EXPAND|wx.RIGHT, border=self.FromDIP(5))
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
        encrypted_indexes = {index for index, task in enumerate(initial_tree.items) if task.encrypted}
        if encrypted_indexes:
            self.model.visible_tasks = set(range(len(initial_tree.items))) - encrypted_indexes
        # 创建DataViewCtrl
        self.mcTree = dv.DataViewCtrl(panel, -1, style=wx.BORDER_THEME|dv.DV_ROW_LINES|dv.DV_VERT_RULES|dv.DV_VARIABLE_LINE_HEIGHT|dv.DV_ROW_LINES)
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
        self._knownTaskPaths = {task.parent.fileName for task in self.model.fileTree.items if task.parent}
        self._UpdateFilterCount()
        # 添加多列
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
        self.mcTree.GetMainWindow().Bind(wx.EVT_SIZE, self.OnTaskListSize)
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
                         border=self.FromDIP(OUTER_MARGIN_DIP))
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
        if self._closing:
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
        baseline = self._defaultTaskViewportWidth - column_spacing
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
        self._CheckBrowserInbox()
        display = {}
        child_display = {}
        file_actions = {}
        snapshot = M3U8Downloader.Snapshot()
        for index, task in enumerate(self.model.fileTree.items):
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
                for file_index in [-4, *range(len(task.outputs))]:
                    actions = self.model.FileActions(index, file_index)
                    if not actions:
                        continue
                    key = (task.task_id or task.parent.fileName, file_index)
                    file_actions[key] = json.dumps(actions, ensure_ascii=False)
                    if notify and getattr(self, '_file_actions_display', {}).get(key) != file_actions[key]:
                        node = self.model.ObjectToItem(self.model._BuildKey((index, file_index)))
                        self.model.ValueChanged(node, 4)
                for child_index in range(len(task.outputs) + (0 if task.work_dir_missing else len(task.childs))):
                    folder = self.model.ObjectToItem(self.model._BuildKey((index, -1)))
                    if child_index >= len(task.outputs) and not self.mcTree.IsExpanded(folder):
                        continue
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
        self._file_actions_display = file_actions
        if status_changed and self.statusFilter.GetSelection() > 0:
            self._ApplyTaskFilter()


    def OnTaskContextMenu(self, event):
        self.OnTaskMenu(event.GetItem())

    def _OpenLocalPath(self, path):
        target = Path(path)
        if not target.exists():
            wx.MessageBox('文件或目录已不存在，请确认保存位置或重新下载。', '提示', parent=self)
            return
        if not wx.LaunchDefaultApplication(str(target)):
            wx.MessageBox('无法打开，请检查系统的默认应用设置。', '提示', parent=self)

    def _OpenWithPlayer(self, path, player=None):
        target = Path(path)
        player = player or SysSetting.GetAll()['player_path']
        if not target.is_file():
            wx.MessageBox('视频文件已不存在，请确认保存位置或重新下载。', '提示', parent=self)
            return
        if not player or not Path(player).exists():
            wx.MessageBox('尚未选择有效的播放器，请使用“选择播放器打开”。', '提示', parent=self)
            return
        try:
            if sys.platform == 'darwin':
                subprocess.Popen(['open', '-a', player, '--', str(target)])
            else:
                subprocess.Popen([player, str(target)])
        except (OSError, subprocess.SubprocessError) as error:
            wx.MessageBox(f'无法启动播放器：{error}', '提示', parent=self)

    def _OpenVideo(self, path):
        target = Path(path)
        if is_encrypted_mp4(target):
            self._PlayEncryptedMP4(target)
            return
        player = SysSetting.GetAll()['player_path']
        if player and Path(player).exists():
            self._OpenWithPlayer(path, player)
        else:
            self._OpenLocalPath(path)

    def _PlayEncryptedMP4(self, path, player=None):
        path = Path(path)
        context = self._encryption_context
        if context is not None:
            try:
                EncryptedMP4Reader(path, context['wrapping_key'])
            except (OSError, ValueError):
                context = None
        if context is None:
            dialog = TaskUnlockDialog(self, title='播放加密 MP4')
            try:
                if dialog.ShowModal() != wx.ID_OK:
                    return
                context = context_from_password(path, dialog.password.GetValue())
                EncryptedMP4Reader(path, context['wrapping_key'])
            except (OSError, ValueError) as error:
                wx.MessageBox(str(error), '无法解密 MP4', wx.OK | wx.ICON_ERROR, parent=self)
                return
            finally:
                dialog.Destroy()
        player = player or SysSetting.GetAll()['player_path']
        if not player or not Path(player).exists():
            player = self._ChoosePlayer()
        if not player:
            return
        try:
            if self._playback_service is None:
                self._playback_service = PlaybackHTTPServer(
                    port=SysSetting.GetAll()['playback_port'],
                    on_port_assigned=self._RememberPlaybackPort,
                    http_service=self._local_http_service)
            token, url = self._playback_service.create_session(path, context['wrapping_key'])
            self._playback_sessions.add(token)
            if sys.platform == 'darwin':
                subprocess.Popen(['open', '-a', player, '--', url])
            else:
                subprocess.Popen([player, url])
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            if 'token' in locals() and self._playback_service is not None:
                self._playback_service.close_session(token)
                self._playback_sessions.discard(token)
            wx.MessageBox(f'无法播放加密 MP4：{error}', '播放失败', parent=self)

    def _ClosePlaybackServers(self):
        self._playback_sessions.clear()
        if self._playback_service is not None:
            self._playback_service.close()
            self._playback_service = None

    def _ChoosePlayer(self):
        current = SysSetting.GetAll()['player_path']
        players = discover_media_players()
        if current and Path(current).exists() and all(Path(candidate).resolve() != Path(current).resolve()
                                                      for _, candidate in players):
            players.insert(0, (f'已选播放器（{Path(current).name}）', current))
        labels = [f'{name} — {Path(candidate).name}' for name, candidate in players]
        dialog = PlayerSelectDialog(self, list(zip(labels, [candidate for _, candidate in players])), current)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            selection = dialog.GetSelection()
            browse_requested = dialog.browse_requested
        finally:
            dialog.Destroy()
        if browse_requested:
            if sys.platform == 'darwin':
                browser = wx.DirDialog(self, '选择播放器应用（例如 VLC.app 或 IINA.app）',
                                       defaultPath=str(Path(current).parent) if current else '/Applications')
            else:
                browser = wx.FileDialog(
                    self, '选择播放器程序', defaultDir=str(Path(current).parent) if current else '',
                    wildcard='可执行文件 (*.exe)|*.exe|所有文件 (*.*)|*.*', style=wx.FD_OPEN)
            try:
                if browser.ShowModal() != wx.ID_OK:
                    return
                player = browser.GetPath()
            finally:
                browser.Destroy()
        else:
            player = players[selection][1]
        try:
            values = SysSetting.GetAll()
            values['player_path'] = player
            SysSetting.Save(values)
        except (OSError, ValueError) as error:
            wx.MessageBox(f'播放器设置保存失败：{error}', '提示', parent=self)
            return None
        return player

    def _ChoosePlayerAndOpen(self, path):
        player = self._ChoosePlayer()
        if player:
            if is_encrypted_mp4(path):
                self._PlayEncryptedMP4(path, player)
            else:
                self._OpenWithPlayer(path, player)

    def OnSelectPlayer(self, event):
        player = self._ChoosePlayer()
        if player:
            self._SetPrimaryStatusText(f'已选择播放器：{Path(player).name}')

    def _SaveFileAs(self, source):
        source = Path(source)
        if not source.is_file():
            wx.MessageBox('原文件已不存在，请重新下载后再另存为。', '另存为失败', wx.OK | wx.ICON_ERROR, self)
            return
        dialog = wx.FileDialog(self, '另存为', defaultFile=source.name,
                               wildcard='所有文件 (*.*)|*.*',
                               style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            destination = dialog.GetPath()
        finally:
            dialog.Destroy()
        errors = []

        def copy():
            try:
                save_file_as(source, destination)
            except (OSError, ValueError) as error:
                errors.append(str(error))

        progress = wx.ProgressDialog('另存为', '正在复制文件，请稍候…', parent=self,
                                     style=wx.PD_APP_MODAL | wx.PD_ELAPSED_TIME)
        worker = Thread(target=copy, name='file-export')
        try:
            worker.start()
            while worker.is_alive():
                progress.Pulse()
                worker.join(0.05)
        finally:
            progress.Destroy()
        if errors:
            wx.MessageBox(errors[0], '另存为失败', wx.OK | wx.ICON_ERROR, self)

    def _ViewPlaylist(self, path):
        path = Path(path)
        try:
            text = path.read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError) as error:
            wx.MessageBox(f'无法读取播放列表：{error}', '查看内容失败', parent=self)
            return
        self._ShowInformation('查看内容：' + path.name, text, line_numbers=True)

    def _EditSegmentInfo(self, task_id, sequence):
        try:
            record = self.tasks.repository.get(task_id)
            if record is None:
                raise ValueError('任务不存在，请刷新后重试。')
            segment = next((s for s in record.details.media_segments if s.sequence == sequence), None)
            if segment is None:
                raise ValueError('分片不存在，请刷新后重试。')
            dialog = SegmentEditDialog(self, segment.source_url or '', segment.duration,
                                       completed=segment.status == FileStatus.COMPLETED)
            try:
                while dialog.ShowModal() == wx.ID_OK:
                    try:
                        self.runner.update_segment_info(task_id, sequence, dialog.GetURL(), dialog.GetDuration())
                    except ValueError as error:
                        wx.MessageBox(str(error), '无法修改分片信息', parent=dialog)
                        continue
                    self._SyncDownloads()
                    break
            finally:
                dialog.Destroy()
        except (ValueError, OSError, sqlite3.Error, TaskDataError, TaskConflictError) as error:
            wx.MessageBox(str(error), '无法修改分片信息', parent=self)

    def _DetectSegmentDurations(self, task_id):
        try:
            self.runner.detect_durations(task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '无法检测分片时长', parent=self)
            return
        if not hasattr(self, '_manualDurationChecks'):
            self._manualDurationChecks = set()
        self._manualDurationChecks.add(task_id)
        self._SetPrimaryStatusText('正在检测已下载分片的播放时长…')
        self.OnTaskProgress(None)

    def _CheckAndContinue(self, task_id):
        """已完成任务也可显式检查并补下载，不在展示时检查磁盘。"""
        try:
            if self.runner.start(task_id):
                self._completion_notified.discard(task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '任务操作失败', parent=self)
        self._SyncDownloads()

    def OnTaskMenu(self, item):
        if not item.IsOk():
            return
        keys = self.model.ParseKey(self.model.ItemToObject(item))
        if len(keys) not in (1, 2):
            return
        index = keys[0]
        task = self.model.fileTree.items[index]
        directory = task.save_dir or Path(PathManager.GetAbsPath(task.parent.fileName)).parent
        menu = wx.Menu()

        def add(label, callback, enabled=True, *, new_group=False):
            if new_group and menu.GetMenuItemCount():
                menu.AppendSeparator()
            entry = menu.Append(wx.ID_ANY, label)
            entry.Enable(enabled)
            menu.Bind(wx.EVT_MENU, lambda event: callback(), entry)

        # keys=[任务索引] 表示任务；[任务索引, 节点键] 表示该任务的文件或目录。
        # 节点键不表示目录深度；工作目录里的分片也使用两个元素的 keys。
        if len(keys) == 2:
            key = keys[1]
            # 负数是特殊节点：-4 为工作目录，-1 为 segments 分片目录。
            if key in (-4, -1):
                directory = task.work_dir or Path(task.parent.fileName).parent
                if key == -1:
                    directory = directory / 'segments'
                add('打开文件夹', lambda: self._OpenLocalPath(directory))
            elif key in task.manifests:
                # 清单节点单独登记在 manifests 中，当前 -2 对应 download.m3u8。
                path = Path(task.manifests[key].fileName)
                add('打开/播放', lambda: self._OpenVideo(path))
                add('选择播放器…', lambda: self._ChoosePlayerAndOpen(path), new_group=True)
                add('打开所在文件夹', lambda: self._OpenLocalPath(path.parent))
                add('查看内容', lambda: self._ViewPlaylist(path), new_group=True)
            elif 0 <= key < len(task.outputs) + len(task.childs):
                # 非负键先排列 outputs（成品或 .part），再排列 childs（TS 分片）。
                # 减去输出数量后，负数表示输出文件，非负数才是分片列表的索引。
                # 例如有 1 个输出：key=0 是输出，key=1 是第 0 个分片。
                segment_index = key - len(task.outputs)
                file = task.outputs[key] if segment_index < 0 else task.childs[segment_index]
                path = Path(file.fileName)
                # 未完成的直链 MP4 输出行代表 .part，不提供播放和另存为。
                partial = segment_index < 0 and task.task_type == TaskType.MP4 and task.task_status != TaskStatus.COMPLETED
                pending = segment_index >= 0 and file.fileSize == '-'
                if pending:
                    # '-' 是展示模型的未完成分片标记；此处不访问磁盘检查文件。
                    info = self.model.TaskInfo(index)
                    add('下载', lambda: self.OnSegmentDownload(item),
                        self.model.CanDownloadSegment(index, key))
                else:
                    if not partial:
                        add('打开/播放', lambda: self._OpenVideo(path))
                        add('选择播放器…', lambda: self._ChoosePlayerAndOpen(path), new_group=True)
                    add('打开所在文件夹', lambda: self._OpenLocalPath(path.parent))
                # 分片使用自己的 URL；直链 MP4（含 .part）使用任务 URL。
                # 合成 MP4 是本地生成文件，没有独立下载地址。
                source_url = file.absUri if segment_index >= 0 else (
                    task.parent.absUri if task.task_type == TaskType.MP4 else '')
                if source_url:
                    add('查看下载地址', lambda: self._ShowInformation('下载地址', source_url), new_group=True)
                if segment_index >= 0:
                    info = self.model.TaskInfo(index)
                    add('修改分片信息', lambda: self._EditSegmentInfo(task.task_id, file.sequence),
                        not info['merging'] and not info['pending'], new_group=not source_url)
                if not pending and not partial:
                    add('另存为…', lambda: self._SaveFileAs(path), new_group=True)
            try:
                if menu.GetMenuItemCount():
                    self.mcTree.PopupMenu(menu)
            finally:
                menu.Destroy()
            # 文件/目录菜单处理完即返回，不再追加下面的任务菜单。
            return

        info = self.model.TaskInfo(index)
        parent_item = self.model.ObjectToItem(self.model._BuildKey((index,)))
        add('重命名…', lambda: self._RenameTask(task.task_id, task.parent.displayName),
            task.rename_allowed)
        add('解密任务' if task.encrypted else '加密任务',
            lambda: self._SetTaskEncrypted(task.task_id, not task.encrypted),
            task.rename_allowed, new_group=True)
        add('打开文件夹', lambda: self._OpenLocalPath(directory))
        if task.outputs and (task.task_type != TaskType.MP4 or task.task_status == TaskStatus.COMPLETED):
            for output in task.outputs:
                path = PathManager.GetAbsPath(output.fileName)
                add('播放视频', lambda path=path: self._OpenVideo(path))
                add('选择播放器',
                    lambda path=path: self._ChoosePlayerAndOpen(path), new_group=True)
        else:
            add('播放视频', lambda: None, False)
        if task.parent.absUri:
            add('查看下载地址', lambda: self._ShowInformation('下载地址', task.parent.absUri), new_group=True)
        if task.task_type == TaskType.MP4 and task.last_error:
            add('查看失败原因', lambda: self._ShowInformation('MP4 下载失败', task.last_error),
                new_group=not task.parent.absUri)
        if task.task_type == TaskType.M3U8:
            add('检测分片时长', lambda: self._DetectSegmentDurations(task.task_id),
                bool(info['done']) and not info['pending'] and not info['merging'], new_group=True)
            add('转 MP4', lambda: self.OnTaskAction(parent_item, 'merge'),
                bool(info['total']) and info['done'] == info['total'] and not task.outputs
                and not info['pending'] and not info['merging'])
        if task.task_type in (TaskType.M3U8, TaskType.MP4) and task.task_status in (TaskStatus.COMPLETED, TaskStatus.WAITING_MERGE):
            add('检查并继续下载', lambda: self._CheckAndContinue(task.task_id),
                new_group=task.task_type != TaskType.M3U8)
        completed_files = []
        if task.task_type == TaskType.M3U8 or task.task_status == TaskStatus.COMPLETED:
            completed_files = [Path(PathManager.GetAbsPath(output.fileName)) for output in task.outputs]
            if not completed_files and task.task_type != TaskType.M3U8:
                completed_files = [Path(PathManager.GetAbsPath(task.parent.fileName))]
        if completed_files:
            for position, path in enumerate(completed_files):
                label = '另存为…' if len(completed_files) == 1 else '另存为：' + path.name
                add(label, lambda path=path: self._SaveFileAs(path), new_group=position == 0)
        else:
            add('另存为…', lambda: None, False, new_group=True)
        try:
            self.mcTree.PopupMenu(menu)
        finally:
            menu.Destroy()

    def _RenameTask(self, task_id, current_name):
        dialog = TaskRenameDialog(self, current_name)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            name = dialog.GetName().strip()
            if not name:
                return
            self.runner.rename(task_id, name)
            self.OnRefresh(None)
            self._SyncDownloads()
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError, TaskConflictError) as error:
            wx.MessageBox(str(error), '重命名失败', parent=self)
        finally:
            dialog.Destroy()

    def _SetTaskEncrypted(self, task_id, encrypted):
        try:
            busy = self.runner.busy(task_id)
        except (ValueError, OSError, sqlite3.Error, TaskDataError, TaskConflictError) as error:
            wx.MessageBox(str(error), '任务加密失败' if encrypted else '任务解密失败',
                          wx.OK | wx.ICON_ERROR, parent=self)
            return
        if busy:
            wx.MessageBox('任务正在下载、检测或合并，请等待结束后再加密或解密。',
                          '暂时无法操作', wx.OK | wx.ICON_INFORMATION, parent=self)
            return
        task = self.tasks.repository.get(task_id)
        if task is None:
            wx.MessageBox('任务不存在，请刷新后重试。', '任务加密失败' if encrypted else '任务解密失败',
                          wx.OK | wx.ICON_ERROR, parent=self)
            return
        if task.type not in (TaskType.MP4, TaskType.M3U8):
            wx.MessageBox('当前任务类型不支持加密或解密文件。',
                          '暂时无法操作', wx.OK | wx.ICON_INFORMATION, parent=self)
            return
        if task.type == TaskType.M3U8 and task.status != TaskStatus.COMPLETED:
            wx.MessageBox('M3U8 任务需要先完成下载和合并，才能加密或解密。',
                          '暂时无法操作', wx.OK | wx.ICON_INFORMATION, parent=self)
            return
        media_paths = plan_task_artifacts(task, encrypted)
        for media_path, destination, _complete in media_paths:
            if destination != media_path and (destination.exists() or destination.is_symlink()):
                wx.MessageBox(f'目标文件已存在，未修改原文件：{destination.name}',
                              '目标文件已存在', wx.OK | wx.ICON_WARNING, parent=self)
                return
        needs_key = encrypted or task.encrypted or any(
            is_encrypted_mp4(path) for path, _destination, _complete in media_paths)
        context = self._encryption_context
        if needs_key and context is None:
            context = self._GetEncryptionContextForOperation()
            if context is None:
                return
        if encrypted:
            try:
                if not TaskEncryption().is_configured():
                    wx.MessageBox('请先从“任务”菜单设置加密任务密码。',
                                  '尚未设置密码', wx.OK | wx.ICON_INFORMATION, parent=self)
                    return
            except ValueError as error:
                wx.MessageBox(str(error), '读取密码失败', wx.OK | wx.ICON_ERROR, parent=self)
                return
        try:
            prepared = []
            metadata_updated = False
            if media_paths:
                failures = []

                def convert_media():
                    try:
                        prepared.extend(prepare_task_artifacts(media_paths, encrypted, context))
                    except Exception as error:
                        failures.append(error)

                worker = Thread(target=convert_media, name='task-encryption')
                action = '加密' if encrypted else '解密'
                progress = wx.ProgressDialog(f'任务{action}', '正在转换本地任务文件…', parent=self,
                                             style=wx.PD_APP_MODAL | wx.PD_ELAPSED_TIME)
                try:
                    worker.start()
                    while worker.is_alive():
                        progress.Pulse()
                        worker.join(0.05)
                finally:
                    progress.Destroy()
                if failures:
                    raise failures[0]
            self.runner.set_task_encrypted(task_id, encrypted, artifacts_prepared=True)
            metadata_updated = True
            cleanup_errors = publish_task_artifacts(prepared)
            self.OnRefresh(None)
            if cleanup_errors:
                action = '加密' if encrypted else '解密'
                names = '、'.join(source.name for source, _destination, _working, _complete in prepared
                                  if source.exists())
                wx.MessageBox(f'{action}状态已更新，但旧文件未能全部清理：{names}\n'
                              f'请检查文件占用后手动删除旧文件。',
                              f'{action}完成但清理未完成', wx.OK | wx.ICON_WARNING, parent=self)
        except Exception as error:
            if not metadata_updated:
                for source, destination, working, _complete in prepared:
                    artifacts = {path for path in (destination, working) if path != source}
                    for artifact in artifacts:
                        try:
                            if artifact.exists():
                                artifact.unlink()
                        except OSError:
                            pass
            wx.MessageBox(str(error), '任务加密失败' if encrypted else '任务解密失败',
                          wx.OK | wx.ICON_ERROR, parent=self)

    def _GetEncryptionContextForOperation(self):
        """Load the locally protected key, asking only for first-time setup/migration."""
        try:
            password_store = TaskEncryption()
            if not password_store.is_configured():
                dialog = TaskPasswordDialog(self, has_existing=False)
                try:
                    if dialog.ShowModal() != wx.ID_OK:
                        return None
                    password = dialog.GetPassword()
                    password_store.set_password(password)
                    return password_store.unlock(password)
                finally:
                    dialog.Destroy()
        except (OSError, ValueError) as error:
            wx.MessageBox(str(error), '读取密码失败', wx.OK | wx.ICON_ERROR, parent=self)
            return None

        try:
            context = password_store.unlock_local()
        except (OSError, ValueError) as error:
            wx.MessageBox(str(error), '读取本地密码失败', wx.OK | wx.ICON_ERROR, parent=self)
            return None
        if context is not None:
            return context

        dialog = TaskUnlockDialog(self, title='验证加密任务密码')
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            password = dialog.password.GetValue()
            context = password_store.unlock(password)
            if context is None:
                wx.MessageBox('密码不正确。', '验证失败', wx.OK | wx.ICON_ERROR, parent=self)
            else:
                # 旧配置只有不可逆校验值；验证成功后保存一份由当前 Windows
                # 用户 DPAPI 保护的凭据，以便后续加解密操作直接复用。
                try:
                    password_store.remember_password(password)
                except OSError as error:
                    wx.MessageBox(f'密码验证成功，但无法保存本地凭据；后续操作仍需验证密码。\n{error}',
                                  '本地密码保存失败', wx.OK | wx.ICON_WARNING, parent=self)
            return context
        finally:
            dialog.Destroy()


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

    def OnUnlockEncryptedTasks(self, _event):
        try:
            password_store = TaskEncryption()
            if not password_store.is_configured():
                wx.MessageBox('请先从“任务”菜单设置加密任务密码。',
                              '尚未设置密码', wx.OK | wx.ICON_INFORMATION, parent=self)
                return
        except ValueError as error:
            wx.MessageBox(str(error), '读取密码失败', wx.OK | wx.ICON_ERROR, parent=self)
            return

        dialog = TaskUnlockDialog(self)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            context = password_store.unlock(dialog.password.GetValue())
            if context is None:
                wx.MessageBox('密码不正确。', '无法解锁', wx.OK | wx.ICON_ERROR, parent=self)
                return
            try:
                password_store.remember_password(dialog.password.GetValue())
            except OSError as error:
                wx.MessageBox(f'已解锁任务，但无法保存本地凭据；下次操作仍需验证密码。\n{error}',
                              '本地密码保存失败', wx.OK | wx.ICON_WARNING, parent=self)
            self._encryption_context = context
            self._encrypted_tasks_unlocked = True
            self._SyncLockEncryptedTasksEnabled()
            self._ApplyTaskFilter(force=True)
        finally:
            dialog.Destroy()

    def OnLockEncryptedTasks(self, _event):
        """Clear the in-memory unlock state and immediately hide encrypted tasks."""
        if not self._encrypted_tasks_unlocked:
            return
        self._encrypted_tasks_unlocked = False
        self._encryption_context = None
        self._ClosePlaybackServers()
        self._SyncLockEncryptedTasksEnabled()
        self._ApplyTaskFilter(force=True)
        self._SetPrimaryStatusText('加密任务已隐藏。')

    def OnEncryptedTasksTool(self, event):
        if self._encrypted_tasks_unlocked:
            self.OnLockEncryptedTasks(event)
        else:
            self.OnUnlockEncryptedTasks(event)

    def _SyncLockEncryptedTasksEnabled(self):
        unlocked = self._encrypted_tasks_unlocked
        self.lockEncryptedTasksItem.Enable(unlocked)
        if self.toolBar is not None:
            tool_id = self._encryptedTasksTool.GetId()
            label = '隐藏加密任务' if unlocked else '显示加密任务'
            icon = 'lock' if unlocked else 'unlock'
            self.toolBar.SetToolNormalBitmap(tool_id, toolbar_icon(icon))
            self._encryptedTasksTool.SetLabel(label)
            self.toolBar.SetToolShortHelp(tool_id, label)
            self.toolBar.EnableTool(tool_id, True)

    def OnSetEncryptedPassword(self, _event):
        password_store = TaskEncryption()
        try:
            has_existing = password_store.is_configured()
        except ValueError as error:
            wx.MessageBox(str(error), '读取密码失败', wx.OK | wx.ICON_ERROR, parent=self)
            return

        dialog = TaskPasswordDialog(self, has_existing=has_existing)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            if has_existing and dialog.GetCurrentPassword() != dialog.GetPassword():
                encrypted_files_exist = False
                for task in self.tasks.repository.list_tasks():
                    if not task.encrypted or task.type not in (TaskType.MP4, TaskType.M3U8):
                        continue
                    candidates = [source for source, _destination, _complete
                                  in plan_task_artifacts(task, not task.encrypted)]
                    if any(is_encrypted_mp4(path) for path in candidates):
                        encrypted_files_exist = True
                        break
                if encrypted_files_exist:
                    wx.MessageBox('已有加密任务文件时暂不支持修改密码。请先解密任务文件后再修改。',
                                  '无法修改密码', wx.OK | wx.ICON_WARNING, parent=self)
                    return
            password_store.set_password(dialog.GetPassword(),
                                        current_password=dialog.GetCurrentPassword())
            self._encryption_context = password_store.unlock(dialog.GetPassword())
            self._encrypted_tasks_unlocked = True
            self._SyncLockEncryptedTasksEnabled()
            wx.MessageBox('加密任务密码已保存。', '设置完成',
                          wx.OK | wx.ICON_INFORMATION, parent=self)
        except (OSError, ValueError) as error:
            wx.MessageBox(str(error), '设置密码失败', wx.OK | wx.ICON_ERROR, parent=self)
        finally:
            dialog.Destroy()

    def _ApplyTaskFilter(self, force=False, expanded_keys=None):
        self.ClearTaskToolTip()
        """只筛选根任务，保持原始任务索引，避免筛选后暂停、删除或下载回调操作错行。"""
        text = self._filterText
        status = self.statusFilter.GetStringSelection()
        visible = None
        locked_encrypted = {index for index, task in enumerate(self.model.fileTree.items)
                            if task.encrypted and not self._encrypted_tasks_unlocked}
        if text or status != '全部状态' or locked_encrypted:
            visible = set()
            for index, task in enumerate(self.model.fileTree.items):
                if index in locked_encrypted:
                    continue
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
                self._UpdateRowNumbers()
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
                self._SetTaskExpanded(child, expand)
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
                    self._SaveExpandState(child, expandeds)
            child, cookie = self.model.GetNextChild(parent, cookie)

    def _RestoreExpandState(self, parent, expandeds):
        """根据上一次展开折叠状态，递归展开或折叠"""
        child, cookie = self.model.GetFirstChild(parent)
        while child.IsOk():            
            if self.model.IsContainer(child):
                obj = self.model.ItemToObject(child)
                if obj in expandeds:
                    self._SetTaskExpanded(child, True)
                    self._RestoreExpandState(child, expandeds)
            child, cookie = self.model.GetNextChild(parent, cookie)


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

    def _ConfiguredBrowserPort(self):
        if self.browser_bridge is not None:
            return self.browser_bridge.port
        path = pairing_path()
        if path.is_file():
            return load_or_create_pairing(path)[1]
        return 0

    def _PlaybackServiceStatus(self):
        if self._playback_service is not None:
            return self._playback_service.get_status()
        return {'running': False, 'port': None,
                'configured_port': SysSetting.GetAll()['playback_port'], 'sessions': 0}

    def _RememberPlaybackPort(self, port):
        values = SysSetting.GetAll()
        if values['playback_port'] == port:
            return
        values['playback_port'] = port
        SysSetting.Save(values)

    def OnShowLocalServiceStatus(self, _event):
        service = self._local_http_service.get_status()
        playback = self._PlaybackServiceStatus()
        bridge_status = self.browser_bridge.get_status() if self.browser_bridge is not None else None
        try:
            saved_browser_port = self._ConfiguredBrowserPort()
        except (OSError, ValueError):
            saved_browser_port = 0
        port = service['port'] or playback['configured_port'] or saved_browser_port
        endpoint_port = str(port) if port else '<自动分配端口>'
        sessions = playback['sessions']
        plugin_running = bridge_status is not None and bridge_status['running']
        listener = {'running': service['running'], 'port': service['port'] or port}
        features = [
            {'name': '加密视频播放',
             'state': f'{sessions} 个活动播放会话' if sessions else '当前没有活动播放会话',
             'endpoint': f'http://127.0.0.1:{endpoint_port}/play/{{令牌}}/{{文件名}}',
             'details': ('全部播放会话闲置 30 分钟后回收；若浏览器插件接收也未启用，'
                         '共用的 HTTP 服务随之停止。')},
            {'name': '浏览器插件接收',
             'state': (f"运行中 · {bridge_status['pending_captures']} 个待处理地址"
                       if plugin_running else '未启用'),
             'endpoint': f'http://127.0.0.1:{endpoint_port}/capture',
             'details': '配对配置保存后随程序启动；播放功能和插件接收共用上方监听端口。'},
        ]

        dialog = ServiceStatusDialog(self, listener, features)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
    
    def OnAddMU(self, event):
        workPath = SysSetting.GetWorkPath()
        dlg = DownloadDialogMU(self, workPath, task_service=self.tasks)
        result = dlg.ShowModal()
        if result == wx.OK:
            # 创建新任务成功，自动刷新页面
            self.OnRefresh(None)
        dlg.Destroy()

    def OnBrowserConnect(self, event):
        if self.browser_bridge is None:
            try:
                self.browser_bridge = BrowserBridge(start=False, http_service=self._local_http_service)
            except (OSError, ValueError) as error:
                wx.MessageBox(f'无法读取浏览器配对配置：{error}', '连接失败', parent=self)
                return
        if self.browser_bridge.server is None:
            try:
                self.browser_bridge.start()
            except (OSError, RuntimeError) as error:
                self._SetPrimaryStatusText(f'本机接收服务未开启：{error}，请检查端口是否可用')
        if self.browser_bridge.port_changed:
            self._SetPrimaryStatusText(
                f'浏览器插件端口已自动调整为 {self.browser_bridge.port}，请同步更新扩展。')
        dialog = BrowserConnectDialog(self, self.browser_bridge.token, reset_token=self.browser_bridge.reset_token,
                                      port=self.browser_bridge.port,
                                      listening=self.browser_bridge.server is not None,
                                      port_reassigned=self.browser_bridge.port_changed)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

    def RestoreBrowserConnection(self):
        """首次启用过连接后，重启恢复服务，不弹出配对窗口。"""
        if self.browser_bridge is not None or not pairing_path().is_file():
            return
        try:
            self.browser_bridge = BrowserBridge(http_service=self._local_http_service)
            if self.browser_bridge.port_changed:
                self._SetPrimaryStatusText(
                    f'浏览器插件端口已自动调整为 {self.browser_bridge.port}，请同步更新扩展。')
        except (OSError, ValueError) as error:
            self._SetPrimaryStatusText(f'浏览器连接未恢复：{error}')

    def _CheckBrowserInbox(self):
        if self.browser_bridge is None or self._capture_dialog_open:
            return
        if any(isinstance(window, wx.Dialog) and window.IsModal() for window in wx.GetTopLevelWindows()):
            return
        try:
            capture = self.browser_bridge.inbox.get_nowait()
        except Empty:
            return
        self._capture_dialog_open = True
        # 程序通知界面线程打开创建任务弹窗
        # 相当于告诉 wxPython：“下一次处理界面事件时，执行 self._OpenBrowserCapture(capture)。”
        wx.CallAfter(self._OpenBrowserCapture, capture)

    def _OpenBrowserCapture(self, capture):
        if self._closing or not self or self.IsBeingDeleted():
            return
        dialog = None
        try:
            # 表示取消主窗口的最小化状态。如果它原本没有最小化，通常不会改变当前状态。
            self.Iconize(False)
            # 将窗口提升到其他窗口前面
            self.Raise()
            if capture['type'] == 'm3u8':
                dialog = DownloadDialogMU(self, SysSetting.GetWorkPath(), self.tasks,
                                           title='从浏览器添加 M3U8', capture=capture)
            else:
                dialog = DownloadDialogMP4(self, SysSetting.GetWorkPath(), self.tasks,
                                            title='从浏览器添加 MP4', capture=capture)
            if dialog.ShowModal() == wx.OK:
                self.OnRefresh(None)
        finally:
            if dialog is not None:
                dialog.Destroy()
            self._capture_dialog_open = False

    def OnAddTS(self, event):
        workPath = SysSetting.GetWorkPath()
        dlg = DownloadDialogTS(self, workPath, task_service=self.tasks)
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
        restored_tasks = set()
        deferred_m3u8 = []
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
                task_structure_changed = previous is not None and (
                    len(previous.outputs) != len(current.outputs)
                    or previous.work_dir_missing != current.work_dir_missing
                    or previous.manifests.keys() != current.manifests.keys())
                structure_changed = structure_changed or task_structure_changed
                if previous is not None and previous.work_dir_missing and not current.work_dir_missing:
                    restored_tasks.add(task_id)
                if task_structure_changed:
                    # 新节点尚未进入原生控件，必须先重建，不能提前发送 ItemChanged。
                    if record.type == TaskType.M3U8:
                        deferred_m3u8.append(task_id)
                    continue
                if record.type == TaskType.M3U8:
                    # 只刷新变化的分片，不因每次状态变化重绘整棵树。
                    visible = self.model.visible_tasks is None or index in self.model.visible_tasks
                    expanded = visible and not current.work_dir_missing and all(
                        self.mcTree.IsExpanded(self.model.ObjectToItem(self.model._BuildKey(key)))
                        for key in ((index,), (index, -4), (index, -1)))
                    for offset, child in enumerate(current.childs if expanded else ()):
                        old = previous.childs[offset] if previous and offset < len(previous.childs) else None
                        if old is None or (old.fileSize, old.status) != (child.fileSize, child.status):
                            item = self.model.ObjectToItem(self.model._BuildKey((index, len(current.outputs) + offset)))
                            self.model.ItemChanged(item)
                    self._M3U8Changed(task_id)
                elif record.type == TaskType.MP4:
                    root = self.model.ObjectToItem(self.model._BuildKey((index,)))
                    if current.outputs and self.mcTree.IsExpanded(root):
                        self.model.ItemChanged(self.model.ObjectToItem(self.model._BuildKey((index, 0))))
                    self.model.ItemChanged(self.model.ObjectToItem(self.model._BuildKey((index,))))
                elif record.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PAUSED):
                    self.model.ItemChanged(self.model.ObjectToItem(self.model._BuildKey((index,))))
            except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
                self._SetPrimaryStatusText(f'读取下载进度失败：{error}')
        if structure_changed:
            # 合并完成会新增 MP4 子节点，保留展开状态后重建树，不能只重绘单元格。
            self._RefreshWithState()
            for index, task in enumerate(self.model.fileTree.items):
                if task.task_id in restored_tasks and (
                        self.model.visible_tasks is None or index in self.model.visible_tasks):
                    self._SetTaskExpanded(self.model.ObjectToItem(self.model._BuildKey((index,))), True)
            for task_id in deferred_m3u8:
                self._M3U8Changed(task_id)
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
        self._SetPrimaryStatusText(('暂停中' if snapshot['requesting'] else '已暂停') if paused
                                   else ('正在下载' if busy else '就绪'))

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

    def _ShowInformation(self, title, text, *, line_numbers=False):
        dlg = InformationDialog(self, title, text, usage=title == '使用帮助', line_numbers=line_numbers)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def OnHelp(self, event):
        self._ShowInformation('使用帮助', (
            '1. 添加任务\n'
            '通过“文件 → 下载M3U8”输入播放列表网址，或通过“下载TS”按分片命名规则创建任务。\n\n'
            '通过“文件 → 下载 MP4”（Ctrl+P）添加视频直链。三个下载窗口均可选择保存目录和文件名，'
            '默认使用设置中的下载目录，文件名自动生成、可修改，重名自动追加序号。\n'
            'MP4 直接保存在所选目录；M3U8、TS 的分片放入同名子目录，合并后的 MP4 位于外层保存目录。\n\n'
            '筛选区域可按文件名、路径和任务状态筛选；关键词也匹配任务下的 MP4 和分片。'
            '清空关键词并选择“全部状态”恢复全部任务。隐藏任务仍继续下载。\n\n'
            '2. 下载与合并\n'
            '任务行固定显示“开始/暂停/继续、重试、删除、更多”，不可用的操作会置灰。'
            '“更多”或右键菜单提供转 MP4、播放视频、打开文件夹和另存为。'
            '已完成的视频可另存到其他目录，原文件和任务记录保持不变。删除默认同时将任务文件移入回收站，可取消勾选以保留文件。'
            '播放视频会调用已选择的外部播放器；VLC 多窗口设置见“5. 播放器”。'
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
        self.ClearTaskToolTip()
        self.runner.reset_duration_failures()
        # 只从数据库恢复任务；读取失败保留当前列表，不退回扫描目录。
        try:
            tree = load_tree(self.tasks)
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'读取任务失败：{error}', '刷新失败', wx.OK | wx.ICON_WARNING, parent=self)
            return
        # 删除或新增记录会改变行号，先按任务身份保存展开状态，再映射到新行。
        expanded_ids = set()
        expanded_folders = set()
        for index, task in enumerate(self.model.fileTree.items):
            item = self.model.ObjectToItem(self.model._BuildKey((index,)))
            if self.mcTree.IsExpanded(item):
                expanded_ids.add(task.task_id or task.parent.fileName)
                for key in (-4, -1):
                    folder = self.model.ObjectToItem(self.model._BuildKey((index, key)))
                    if self.model.IsContainer(folder) and self.mcTree.IsExpanded(folder):
                        expanded_folders.add((task.task_id or task.parent.fileName, key))
        self.model.fileTree = tree
        expanded = {self.model._BuildKey((index,)) for index, task in enumerate(tree.items)
                    if (task.task_id or task.parent.fileName) in expanded_ids}
        expanded.update(self.model._BuildKey((index, key)) for index, task in enumerate(tree.items)
                        for key in (-4, -1) if (task.task_id or task.parent.fileName, key) in expanded_folders)
        self._ApplyTaskFilter(force=True, expanded_keys=expanded)

    def OnActivatedChanged(self, event):
        """双击/键盘激活展开目录，打开播放列表或已完成的视频。"""
        column = event.GetDataViewColumn()
        if column is not None and column.GetModelColumn() == 4:
            return  # The custom renderer handles action cells on a single click.
        item = event.GetItem()
        if not item.IsOk():
            return
        keys = self.model.ItemToObject(item)
        objs = self.model.ParseKey(keys)
        if self.model.IsContainer(item):
            self._SetTaskExpanded(item)
        elif len(objs) == 2 and objs[1] in self.model.fileTree.items[objs[0]].manifests:
            self._OpenLocalPath(self.model.fileTree.items[objs[0]].manifests[objs[1]].fileName)
        elif len(objs) == 2:
            task = self.model.fileTree.items[objs[0]]
            if 0 <= objs[1] < len(task.outputs):
                output = task.outputs[objs[1]]
                if Path(output.fileName).suffix.lower() == '.mp4' and (
                        task.task_type != TaskType.MP4 or task.task_status == TaskStatus.COMPLETED):
                    self._OpenVideo(output.fileName)

    # 窗口把点击位置转换为稳定的任务 UUID；执行规则由 TaskRunner 重新核对。
    def _SetTaskExpanded(self, item, expanded=None):
        """统一展开/收缩入口；未指定状态时切换，状态相同则不重复更新。"""
        if self._closing or not item.IsOk() or not self.model.IsContainer(item):
            return
        current = self.mcTree.IsExpanded(item)
        target = not current if expanded is None else expanded
        if current == target:
            return
        if target:
            self.mcTree.Expand(item)
        else:
            self.mcTree.Collapse(item)
        self._QueueRowNumbers()

    def _QueueRowNumbers(self, event=None):
        self.ClearTaskToolTip()
        if event is not None:
            event.Skip()
        if self._closing or getattr(self, '_rowNumbersPending', False):
            return
        self._rowNumbersPending = True
        # 等原生控件完成展开，并合并“全部展开”等连续操作的通知。
        wx.CallAfter(self._UpdateRowNumbers)

    def _UpdateRowNumbers(self):
        self._rowNumbersPending = False
        if self._closing or not self or self.IsBeingDeleted():
            return
        if self.model.UpdateRowNumbers(self.mcTree):
            self.mcTree.Refresh()
        if hasattr(self, '_defaultTaskWidth'):
            self._FitTaskColumns()

    def OnTaskAction(self, item, action_id='start'):
        """自定义操作分发入口，不是 wx 自动调用的重写方法。

        行内渲染器识别点击区域后传入 item 和操作 id；“转 MP4”菜单传入 merge。
        item 指明具体任务行，不依赖当前选中行；默认 start 表示开始/暂停/继续。
        start 表示开始/暂停/继续，retry 重试失败，delete 删除，more 打开菜单。
        """
        if not item.IsOk():
            return
        keys = self.model.ParseKey(self.model.ItemToObject(item))
        if len(keys) == 2:
            action = next((entry for entry in self.model.FileActions(*keys) if entry['id'] == action_id), None)
            if action is not None and action['enabled']:
                task = self.model.fileTree.items[keys[0]]
                if self._manualActionClicks:
                    self._QueueTaskPopup(task, action_id)
                else:
                    self.OnDeleteTask(task, scope='work' if action_id == 'delete_work' else 'output')
            return
        # 分片下载由另一个处理函数负责。
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
            if action_id == 'more':
                if self._manualActionClicks:
                    self._QueueTaskPopup(task, action_id)
                else:
                    self.OnTaskMenu(item)
                return
            if action_id == 'delete':
                # 删除处理函数会再次检查运行状态，并确认是否回收本地文件。
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
            actions = (self.model.FileActions(index, -4 if action_id == 'delete_work' else 0)
                       if action_id in ('delete_work', 'delete_output') else self.model.TaskActions(index))
            action = next((entry for entry in actions
                           if entry['id'] == action_id), None)
            if action is None or not action['enabled']:
                return
            if action_id == 'more':
                self.OnTaskMenu(self.model.ObjectToItem(self.model._BuildKey((index,))))
            elif action_id == 'delete':
                self.OnDeleteTask(task)
            elif action_id in ('delete_work', 'delete_output'):
                self.OnDeleteTask(task, scope='work' if action_id == 'delete_work' else 'output')
            return

    def OnDeleteTask(self, task, *, scope='task'):
        """确认是否同时回收任务文件；仅清理明确归属于该任务的路径。"""
        try:
            if scope == 'task' and self.runner.busy(task.task_id):
                raise ValueError('任务仍在下载、检测或合并中，暂时不能删除。')
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '无法删除', wx.OK | wx.ICON_WARNING, parent=self)
            return
        title = {'task': '删除任务', 'work': '删除工作文件夹', 'output': '删除 MP4 文件'}[scope]
        dialog = wx.RichMessageDialog(self, f'确定{title}？', title,
                                      wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        if scope == 'task':
            dialog.SetExtendedMessage('将从列表和数据库中删除此任务。勾选下方选项会将任务专属工作文件夹（含全部内容）、视频和对应临时文件移入回收站；工作文件夹以外的另存为副本保留。文件回收失败时保留任务记录。')
            dialog.ShowCheckBox('同时删除此任务的本地文件（移入回收站）', checked=True)
        elif scope == 'work':
            dialog.SetExtendedMessage('将此任务的专属工作文件夹及全部内容移入回收站，包括隐藏文件和临时文件。保留任务记录和文件夹外的 MP4。已暂停的等待队列会移除。')
        else:
            dialog.SetExtendedMessage('将此任务的 MP4 和对应临时文件移入回收站。保留任务记录和分片，分片完整时可以重新转 MP4；另存为副本保留。')
        dialog.SetYesNoLabels('删除任务' if scope == 'task' else '移入回收站', '取消')
        try:
            if dialog.ShowModal() != wx.ID_YES:
                return
            delete_files = dialog.IsCheckBoxChecked() if scope == 'task' else True
        finally:
            dialog.Destroy()
        try:
            if scope == 'task':
                self.runner.delete(task.task_id, delete_files=delete_files)
            else:
                # 主动删除产物后不自动合并重建，由用户选择转 MP4 或继续下载。
                self._completion_notified.add(task.task_id)
                self.runner.delete(task.task_id, scope=scope)
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
            task.task_type == TaskType.M3U8 and task.task_status in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED, TaskStatus.PARTIAL)
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
        if not self.model.CanDownloadSegment(keys[0], keys[1]):
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
        manual = task_id in getattr(self, '_manualDurationChecks', set())
        if manual:
            self._manualDurationChecks.discard(task_id)
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
        if task.duration_pending and not manual:
            self._QueueDurationCheck(task)
            return
        playlist_saved = self._CreateM3U8File(task.parent.fileName)
        failures = sum(s.duration_status == 'failed' for s in record.details.media_segments)
        if failures:
            self._SetPrimaryStatusText(f'{task.parent.displayName}：{failures} 个分片时长检测失败，保留原时长。')
        elif manual and playlist_saved:
            self._SetPrimaryStatusText('分片时长检测完成，本地播放列表已更新。')
        if manual:
            self._completion_notified.add(task_id)
        pending = M3U8Downloader.Snapshot()['pending']
        if (not manual and task.task_status == TaskStatus.WAITING_MERGE
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
            if task_id in getattr(self, '_manualDurationChecks', set()):
                break
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
        children = [task.childs[index] for index, _ in tasks]
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
