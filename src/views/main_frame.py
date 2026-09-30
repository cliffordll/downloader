import json
import sqlite3
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
from src.managers.task_repository import TaskDataError, TaskConflictError
from src.schemas.task import TaskStatus, TaskType

ICON_ROOT = Path(__file__).resolve().parents[2] / "icons"
ICON_FILES = {
    "open": "tools/open.png",
    "playlist": "files/m3u8.png",
    "segment": "files/ts.png",
    "expand": "tools/expand.png",
    "collapse": "tools/collapse.png",
    "refresh": "tools/refresh.png",
    "pause": "tools/pause.png",
    "start": "tools/start.png",
    "settings": "tools/settings.png",
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


class TaskProgressRenderer(dv.DataViewCustomRenderer):
    """在任务行内绘制进度条及居中的进度文字，不创建独立的 wx.Gauge 控件。"""

    def __init__(self, frame):
        super().__init__('string', dv.DATAVIEW_CELL_INERT, wx.ALIGN_CENTER)
        self.frame = frame
        self.label = ''
        self.percent = 0

    def SetValue(self, value):
        """重写：wx 传入模型第 5 列的文字，例如“50% · 1/2”；分片行为空。"""
        self.label = value
        # 未知总大小和直播时长没有百分比，仅显示文字。
        prefix, separator, _ = value.partition('%')
        self.percent = max(0, min(100, int(prefix))) if separator and prefix.isdigit() else None
        return True

    def GetValue(self):
        """重写：返回当前单元格的进度文字，保持模型的 string 类型不变。"""
        return self.label

    def GetSize(self):
        """重写：绘制宽度跟随所属列，避免居中布局把进度条限制在固定宽度内。"""
        column = self.GetOwner()
        width = column.GetWidth() if column is not None else self.frame.FromDIP(180)
        return wx.Size(max(1, width - self.frame.FromDIP(8)), self.frame.FromDIP(24))

    def Render(self, cell, dc, state):
        """重写：按模型提供的百分比填充；未知总量/直播仅绘制文字，不表示合并进度。"""
        if not self.label:
            return True
        rect = wx.Rect(cell)
        rect.Deflate(self.frame.FromDIP(3), self.frame.FromDIP(2))
        if rect.width <= 0 or rect.height <= 0:
            return True
        font = wx.Font(self.frame.mcTree.GetFont())
        font.SetWeight(wx.FONTWEIGHT_NORMAL)
        dc.SetFont(font)
        if self.percent is None:
            dc.SetTextForeground(wx.Colour('#172B4D'))
            clip = wx.DCClipper(dc, rect)
            dc.DrawLabel(self.label, rect, wx.ALIGN_CENTER)
            del clip
            return True
        # 进度条只占行内 16 DIP 高度，垂直居中，避免色块撑满整行。
        bar_height = min(rect.height, self.frame.FromDIP(16))
        bar = wx.Rect(rect.x, rect.y + (rect.height - bar_height) // 2, rect.width, bar_height)
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush('#E0E6EF'))
        dc.DrawRectangle(bar)
        filled = bar.width * self.percent // 100
        if filled:
            dc.SetBrush(wx.Brush('#008A36' if self.percent == 100 else '#0055FF'))
            dc.DrawRectangle(bar.x, bar.y, filled, bar.height)
        # 同一份文字始终在整条进度条中居中，避免随进度移动。
        # 分区裁剪绘制：已填充区域用白字，未填充区域用深色字，跨界文字也能看清。
        for region, colour in (
            (wx.Rect(bar.x, bar.y, filled, bar.height), '#FFFFFF'),
            (wx.Rect(bar.x + filled, bar.y, bar.width - filled, bar.height), '#172B4D'),
        ):
            if region.width <= 0:
                continue
            clip = wx.DCClipper(dc, region)
            dc.SetTextForeground(wx.Colour(colour))
            dc.DrawLabel(self.label, bar, wx.ALIGN_CENTER)
            del clip  # 恢复之前的裁剪区域，不影响其他单元格。
        return True


class TaskActionRenderer(dv.DataViewCustomRenderer):
    """绘制“操作”列，并把单元格点击转换成具体任务操作。

    这里没有创建按钮，而是按模型的操作数量划分单元格内的可点击区域。
    模型 GetValue → SetValue 接收数据 → Render 绘制文字；
    用户点击 → ActivateCell 判断区域 → 主窗口 OnTaskAction 执行业务操作。
    """
    def __init__(self, frame):
        # ACTIVATABLE 让 wx 将单元格的鼠标/键盘激活交给 ActivateCell。
        super().__init__('string', dv.DATAVIEW_CELL_ACTIVATABLE, wx.ALIGN_CENTER)
        self.frame = frame
        self.label = ''

    def SetValue(self, value):
        """重写父类方法：wx 在准备单元格内容时调用，传入模型提供的值。

        保存本次要绘制的数据；返回 True 表示成功接收，不是下载成功。
        这不是模型的 SetValue，不负责修改任务数据，也不需要业务代码手动调用。
        """
        # wx 自动传入模型 GetValue(item, 4) 的结果。
        # 任务行是包含 id、label、enabled 的 JSON 数组；分片行是“下载”或空串。
        # 渲染器由整列共用，label 会随当前绘制的单元格更新，不属于某个固定任务。
        self.label = value
        return True

    def GetValue(self):
        """重写父类方法：供 wx 读取渲染器当前保存的值。

        返回值与本渲染器声明的 string 类型一致；不会重新查询模型或触发刷新。
        """
        return self.label

    def GetSize(self):
        """重写父类方法：wx 布局时查询内容所需尺寸，返回 wx.Size。

        FromDIP 按屏幕缩放比例换算尺寸；实际绘制区域以 Render 的 cell 参数为准。
        """
        column = self.GetOwner()
        width = column.GetWidth() if column is not None else self.frame.FromDIP(200)
        return wx.Size(max(1, width - self.frame.FromDIP(8)), self.frame.FromDIP(24))

    def _ActionRects(self, cell, count=5):
        """左右留白后按 count 等分；M3U8 五项、MP4/RTMP 四项。

        这是本类自定义的辅助方法，不是 wx 的重写回调，由 Render/ActivateCell 调用。
        绘制与点击判断共用此方法，确保显示位置和点击区域始终对应。
        用相邻边界之差计算宽度，避免整数取整导致区域之间出现缝隙。
        """
        rect = wx.Rect(cell)
        rect.Deflate(self.frame.FromDIP(4), 0)
        return [wx.Rect(rect.x + rect.width * i // count, rect.y,
                        rect.width * (i + 1) // count - rect.width * i // count, rect.height)
                for i in range(count)]

    def Render(self, cell, dc, state):
        """重写父类方法：wx 重绘单元格时自动调用，不需要手动绑定绘制事件。

        cell 是绘制区域，dc 是绘图上下文，state 包含选中等显示状态。
        使用 SetValue 保存的数据绘制，返回 True 表示绘制已处理；不执行任务操作。
        """
        if not self.label:
            return True
        font = wx.Font(self.frame.mcTree.GetFont())
        font.SetWeight(wx.FONTWEIGHT_NORMAL)
        dc.SetFont(font)
        # 选中行使用浅蓝背景，操作仍用链接色，避免白字在浅底上看不清。
        colour = wx.SYS_COLOUR_HOTLIGHT
        if self.label.startswith('['):
            # 按该类型的操作数量等分；置灰不移除区域，防止状态变化时布局跳动。
            actions = json.loads(self.label)
            for action, rect in zip(actions, self._ActionRects(cell, len(actions))):
                dc.SetTextForeground(wx.SystemSettings.GetColour(
                    colour if action['enabled'] else wx.SYS_COLOUR_GRAYTEXT))
                dc.DrawLabel(action['label'], rect, wx.ALIGN_CENTER)
        else:
            dc.SetTextForeground(wx.SystemSettings.GetColour(colour))
            dc.DrawLabel(self.label, cell, wx.ALIGN_CENTER)
        return True

    def ActivateCell(self, cell, model, item, col, mouseEvent):
        """重写父类方法：wx 在可激活单元格被鼠标或键盘激活时调用。

        item/col 指明任务行和模型列，model 用于读取该行最新数据；
        mouseEvent 为 None 表示没有鼠标事件，否则用其中的坐标判断具体操作。
        返回 True 表示本次激活已处理，False 表示无操作或操作不可用，
        不代表异步下载、合并等业务执行成功。
        """
        # 点击时重新读取当前行的最新状态，不能用上次绘制其他行留下的 self.label。
        value = model.GetValue(item, col)
        if not value:
            return False
        if value.startswith('['):
            # 单文件任务也是任务行，但不可展开；按数据协议识别，不能按容器判断。
            actions = json.loads(value)
            if mouseEvent is None:
                # 键盘激活没有鼠标坐标：优先开始/继续，否则打开更多，绝不默认删除。
                action = actions[0] if actions[0]['enabled'] else actions[-1]
            else:
                # wx 提供的鼠标坐标相对于当前单元格左上角，因此区域也从 (0, 0) 算起。
                point = mouseEvent.GetPosition()
                rects = self._ActionRects(wx.Rect(0, 0, cell.width, cell.height), len(actions))
                # Contains 判断鼠标是否落在某一项的矩形内；左右留白没有对应操作。
                action = next((entry for entry, rect in zip(actions, rects) if rect.Contains(point)), None)
            if action is None or not action['enabled']:
                return False
            # item 指明哪一行，id 指明做什么；不依赖选中行或显示文字。
            # 主窗口还会重新检查 enabled，再分发下载、重试、删除或更多菜单。
            self.frame.OnTaskAction(item, action['id'])
        else:
            # 分片下载只从操作文字触发，双击普通单元格不再启动网络请求。
            self.frame.OnSegmentDownload(item)
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

        self._searchText = ''
        self._filterText = ''
        self._searchTimer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.OnSearchTimer, self._searchTimer)
        self._createMainPanel()
        # 记住默认窗口宽度；放大时按默认列比例扩展，恢复窗口时还原布局。
        self._defaultTaskWidth = self.GetClientSize().width
        self._task_display = {}
        self._storage_error = ''
        self._completion_notified = set()
        # 显示前完成布局、列宽和状态缓存，避免先画初始列宽再跳到适配后的宽度。
        self.Layout()
        self.mcTree.GetParent().Layout()
        self._FitTaskColumns()
        self.OnTaskProgress(None, notify=False)
        self._UpdatePauseTool()
        # 初始化完成后再监听尺寸变化；后续缩放仍合并为一次延迟调整。
        self.mcTree.Bind(wx.EVT_SIZE, self.OnTaskListSize)
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
        openItem    = fileMenu.Append(wx.ID_OPEN, "打开下载文件夹\tCtrl-O")
        fileMenu.AppendSeparator()
        addMUItem   = fileMenu.Append(wx.ID_ANY, "&下载M3U8\tCtrl-M")
        addTSItem   = fileMenu.Append(wx.ID_ANY, "&下载TS\tCtrl-T")
        fileMenu.AppendSeparator()
        settingItem  = fileMenu.Append(wx.ID_ANY, "设置\tCtrl-,")
        exitItem    = fileMenu.Append(wx.ID_EXIT, "&退出")
        # 绑定事件
        self.Bind(wx.EVT_MENU, self.OnOpen, openItem)
        self.Bind(wx.EVT_MENU, self.OnAddMU, addMUItem)
        self.Bind(wx.EVT_MENU, self.OnAddTS, addTSItem)
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
        self.showToolItem   = viewMenu.Append(wx.ID_ANY, "显示工具栏", kind=wx.ITEM_CHECK)
        self.showStatusItem = viewMenu.Append(wx.ID_ANY, "显示状态栏", kind=wx.ITEM_CHECK)
        self.Bind(wx.EVT_MENU, self.OnToggleToolBar, self.showToolItem)
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
        self.toolBar = self.CreateToolBar(style=wx.TB_DEFAULT_STYLE)
        self.toolBar.SetToolBitmapSize(self.toolBar.FromDIP(wx.Size(24, 24)))
        self.toolBar.SetToolPacking(self.toolBar.FromDIP(4))
        self.toolBar.SetToolSeparation(self.toolBar.FromDIP(8))
        self.toolBar.SetMargins(self.toolBar.FromDIP(wx.Size(4, 3)))

        def add_tool(tool_id, label, icon):
            bundle = toolbar_icon(icon)
            return self.toolBar.AddTool(tool_id, label, bundle, shortHelp=label)

        openButton = add_tool(wx.ID_OPEN, "打开下载文件夹", "open")
        muButton = add_tool(wx.ID_ANY, "下载M3U8", "playlist")
        tsButton = add_tool(wx.ID_ANY, "下载TS", "segment")
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

    def _createMainPanel(self):
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
            '待继续', '下载失败', '待合并', '合并中', '合并失败', '已完成'])
        self.statusFilter.SetSelection(0)
        self.filterCount = wx.StaticText(panel, label='')
        self.filterCount.SetMinSize(self.FromDIP(wx.Size(140, -1)))
        uriSizer.Add(self.searchCtrl, proportion=1, flag=wx.EXPAND|wx.TOP|wx.BOTTOM|wx.RIGHT, border=5)
        uriSizer.Add(self.statusFilter, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=10)
        uriSizer.Add(self.filterCount, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=5)
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
        emptySizer.Add(self.emptyText, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=12)
        emptySizer.Add(self.clearFiltersButton, flag=wx.ALIGN_CENTER_VERTICAL)
        emptySizer.AddStretchSpacer()
        self.emptyPanel.SetSizer(emptySizer)
        self.emptyPanel.Hide()

        # 多列树布局
        # self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_READONLY|wx.TE_RICH2)
        listSizer = wx.BoxSizer(wx.HORIZONTAL)
        # 创建并关联模型
        self.model = MultiColumnTreeModel(self)
        # 创建DataViewCtrl
        self.mcTree = dv.DataViewCtrl(panel, -1, style=wx.BORDER_THEME|dv.DV_ROW_LINES|dv.DV_VERT_RULES|dv.DV_VARIABLE_LINE_HEIGHT|dv.DV_ROW_LINES)
        # Windows 下原生 RendererNative 不能通过 Python 重写其绘制回调。
        # 在列表内部窗口的原生绘制结束后统一替换选中边框。
        self.mcTree.GetMainWindow().Bind(wx.EVT_PAINT, self.OnTaskListPaint)
        self.mcTree.AssociateModel(self.model)
        self._knownTaskPaths = {task.parent.fileName for task in self.model.fileTree.items if task.parent}
        self._UpdateFilterCount()
        # 添加多列
        self.mcTree.AppendTextColumn("序列", 0, width=60)
        # # 自定义列
        # renderer = dv.DataViewTextRenderer()
        # renderer.EnableEllipsize(wx.ELLIPSIZE_END)
        # self.mcTree.AppendColumn(dv.DataViewColumn("文件名", renderer, 1, width=180, align=wx.ALIGN_LEFT))
        # self.mcTree.AppendTextColumn("文件名", 1, width=500)
        self.mcTree.AppendTextColumn("文件名", 1, width=250)
        self.mcTree.AppendColumn(dv.DataViewColumn('下载进度', TaskProgressRenderer(self), 5,
                                                 width=self.FromDIP(180), align=wx.ALIGN_CENTER))
        self.mcTree.AppendTextColumn("状态", 6, width=self.FromDIP(80), align=wx.ALIGN_CENTER)
        self.mcTree.AppendTextColumn("文件大小", 2, width=90, align=wx.ALIGN_RIGHT)
        self.mcTree.AppendTextColumn("修改时间", 3, width=130)
        self.mcTree.AppendColumn(dv.DataViewColumn("操作", TaskActionRenderer(self), 4,
                                                 width=self.FromDIP(200), align=wx.ALIGN_CENTER))
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
        listSizer.Add(self.mcTree, proportion=10, flag=wx.EXPAND|wx.TOP, border=5)
        # listSizer.Add(self.mulist, proportion=10, flag=wx.EXPAND|wx.ALL, border=5)
        # self.list.SetBackgroundColour(wx.RED)

        # 双击任务只切换展开状态，下载由操作列触发。
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_ACTIVATED, self.OnActivatedChanged)
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_CONTEXT_MENU, self.OnTaskContextMenu)
        self.Bind(EVT_ALL_DOWNLOAD, self.OnAllTSDownload)

        sizer.Add(uriSizer, flag=wx.EXPAND, border=0)
        sizer.Add(self.emptyPanel, flag=wx.EXPAND | wx.TOP | wx.BOTTOM, border=12)
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND|wx.ALL, border=0)
        # 设置面板的sizer
        panel.SetSizer(sizer)

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

    def OnTaskListSize(self, event):
        event.Skip()
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
        if not self or not self.mcTree:
            return
        # 宽度及 DPI 缩放未变化就直接返回，避免重复设置列宽导致表头闪烁。
        size_key = (self.mcTree.GetSize().width, self.FromDIP(100))
        if getattr(self, '_columnSizeKey', None) == size_key:
            return
        # 预留垂直滚动条和边框空间，避免滚动条出现后把最后一列挤出可视区域。
        # 使用控件整体宽度，避免滚动条改变客户区宽度后触发列宽来回调整。
        available = (self.mcTree.GetSize().width
                     - wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X, self.mcTree)
                     - self.FromDIP(4))
        if available <= 0:
            return
        self._columnSizeKey = size_key
        # 按界面显示顺序：序列、文件名、下载进度、状态、文件大小、修改时间、操作。
        # 数字是 DIP，由 FromDIP 按系统缩放换算；文件名的 0 是占位，下面补入剩余宽度。
        widths = [self.FromDIP(value) for value in (50, 0, 180, 80, 85, 125, 200)]
        # 默认窗口的可用宽度是比例分配的基准，不随最大化/还原反复改变。
        baseline = (self._defaultTaskWidth
                    - wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X, self.mcTree)
                    - self.FromDIP(4))
        # 不超过默认宽度时，其他列保持基准值，剩余空间交给文件名列。
        widths[1] = max(1, min(available, baseline) - sum(widths))
        if available > baseline > 0:
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
            # 初始化可能短暂出现很小的控件尺寸，空间不足时将所有列一起压缩。
            total = sum(widths)
            widths = [max(1, value * available // total) for value in widths]
        # 批量调整时暂停重绘，完成后统一恢复，避免逐列调整产生闪烁。
        self.mcTree.Freeze()  # wx 方法：暂停该控件的屏幕重绘，期间仍可修改列宽。
        try:
            for index, width in enumerate(widths):
                column = self.mcTree.GetColumn(index)
                # 只写入真正变化的列宽，减少原生控件的布局和重绘开销。
                if column.GetWidth() != width:
                    column.SetWidth(width)
        finally:
            # wx 方法：恢复重绘，让批量修改后的列宽显示出来。
            # 与 Freeze 配对；放在 finally 中，确保调整过程出错时也能恢复界面刷新。
            self.mcTree.Thaw()

    def OnDestroy(self, event):
        if event.GetEventObject() is self:
            self._progressTimer.Stop()
            self._searchTimer.Stop()
            for task in self.model.fileTree.items:
                if task.task_id and task.task_status in (
                        TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSING, TaskStatus.MERGING):
                    try:
                        self.model.tasks.interrupt(task.task_id)
                    except (ValueError, OSError, sqlite3.Error, TaskDataError, TaskConflictError) as error:
                        # 即使退出写库失败，下次启动也会恢复库中遗留的活动状态。
                        print(f'保存退出状态失败：{error}')
        event.Skip()

    def OnTaskProgress(self, event, notify=True):
        """启动只建立缓存；后续只刷新变化的单元格，不反复使整行和全部表头失效。"""
        self._SyncTaskRuntime()
        display = {}
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
        status_changed = any(self._task_display.get(key, ('', ''))[1] != state[1]
                             for key, state in display.items())
        self._task_display = display
        if status_changed and self.statusFilter.GetSelection() > 0:
            self._ApplyTaskFilter()

    def _PersistTask(self, operation, *args):
        """保存成功才更新行内快照；写库失败暂停后续请求，并且只提示一次。"""
        try:
            record = operation(*args)
            self._storage_error = ''
            self.model.ApplyTaskRecord(record)
            return record
        except (ValueError, OSError, sqlite3.Error, TaskDataError, TaskConflictError) as error:
            Downloader.Pause()
            message = str(error)
            if self._storage_error != message:
                self._storage_error = message
                wx.MessageBox(f'任务数据保存失败，已暂停后续下载：{error}', '保存失败',
                              wx.OK | wx.ICON_ERROR, parent=self)
            return None

    def _SyncTaskRuntime(self):
        """只在排队/请求/暂停状态变化时写库；500ms 定时刷新不会重复写相同状态。"""
        snapshot = Downloader.Snapshot()
        for task in list(self.model.fileTree.items):
            if task.task_type != TaskType.M3U8 or task.task_id is None or task.task_status == TaskStatus.MERGING or task.outputs:
                continue
            keys = {Downloader.FileKey(child.fileName) for child in task.childs}
            pending = keys & snapshot['pending']
            if not pending:
                continue
            requesting = bool(pending & snapshot['requesting'])
            paused = snapshot['paused'] or pending <= snapshot['paused_files']
            status = ((TaskStatus.PAUSING if requesting else TaskStatus.PAUSED) if paused
                      else (TaskStatus.DOWNLOADING if requesting else TaskStatus.QUEUED))
            if task.task_status != status:
                if self._PersistTask(self.model.tasks.runtime_status, task.task_id, status) is None:
                    break

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
                self.OnTaskMenu(item)
                return
            if action_id == 'delete':
                # 删除处理函数会再次检查运行状态，并确认只删除任务记录。
                self.OnDeleteTask(task)
                return
        if action_id in ('start', 'retry'):
            if action_id == 'start' and info['pending']:
                # 当前任务已有排队/处理中的分片：切换暂停状态，不重新提交下载。
                # 只传入当前任务的 pending 文件键，不暂停其他任务。
                if info['paused']:
                    Downloader.ResumeFiles(info['pending'])
                else:
                    Downloader.PauseFiles(info['pending'])
                self._SyncTaskRuntime()
                self.model.ItemChanged(item)
                return
            # 无待处理队列时才创建下载请求。开始/继续下载所有缺失分片，
            # 重试仅选择失败集合中的缺失分片，已经存在的分片一律跳过。
            tasks = []
            for segment_index, child in enumerate(task.childs):
                key = Downloader.FileKey(PathManager.GetAbsPath(child.fileName))
                if child.fileSize == '-' and (action_id != 'retry' or key in info['failed']):
                    child_item = self.model.ObjectToItem(self.model._BuildKey(
                        (index, len(task.outputs) + segment_index)))
                    # 子节点中 MP4 排在分片前面，因此界面节点索引需加 outputs 偏移；
                    # 提交给下载逻辑的 segment_index 仍是播放列表中的分片索引。
                    tasks.append((segment_index, child_item))
            self._DownloadFiles(task.parent.fileName, tasks)
        # 通知 wx 重新读取本行数据，使操作文字、置灰状态和任务状态及时更新。
        self.model.ItemChanged(item)

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
        if task.outputs:
            for output in task.outputs:
                path = PathManager.GetAbsPath(output.fileName)
                add('播放视频：' + Path(path).name, lambda path=path: self._OpenLocalPath(path))
        else:
            add('播放视频', lambda: None, False)
        try:
            self.mcTree.PopupMenu(menu)
        finally:
            menu.Destroy()

    def OnDeleteTask(self, task):
        """本阶段只删除数据库记录；下载文件保留，不再递归删除任务目录。"""
        def busy():
            if task.task_type != TaskType.M3U8:
                return task.task_status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING,
                    TaskStatus.PAUSING, TaskStatus.RECORDING, TaskStatus.STOPPING)
            keys = {Downloader.FileKey(child.fileName) for child in task.childs}
            output = str(Path(task.parent.fileName).parent / 'output.mp4')
            return bool(keys & Downloader.Snapshot()['pending']) or Converter.IsConverting(output)

        if busy():
            wx.MessageBox('任务仍在下载、排队或合并中，暂时不能删除。', '无法删除',
                          wx.OK | wx.ICON_WARNING, parent=self)
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
            if busy():
                raise ValueError('任务状态已变化，请停止下载后再删除。')
            self.model.tasks.repository.delete(task.task_id)
            self.model.merge_failed.discard(task.parent.fileName)
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'删除未完成：{error}', '无法删除', wx.OK | wx.ICON_WARNING, parent=self)
            return
        self.OnRefresh(None)

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
        dlg = DownloadDialogMU(self, "M3U8 URI", workPath, task_service=self.model.tasks)
        result = dlg.ShowModal()
        if result == wx.OK:
            # 创建新任务成功，自动刷新页面
            self.OnRefresh(None)
        dlg.Destroy()

    def OnAddTS(self, event):
        workPath = SysSetting.GetWorkPath()
        dlg = DownloadDialogTS(self, "M3U8 TS", workPath, task_service=self.model.tasks)
        result = dlg.ShowModal()
        if result == wx.OK:
            # 创建新任务成功，自动刷新页面
            self.OnRefresh(None)
        dlg.Destroy()
    
    def OnSetting(self, event):
        from src.views.tab_setting import SettingsDialog
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

    def _GlobalDownloadActions(self):
        """固定菜单项各自判定可用状态；混合运行/暂停时两项均可用。"""
        snapshot = Downloader.Snapshot()
        can_pause = not snapshot['paused'] and bool(snapshot['pending'] - snapshot['paused_files'])
        tasks = self.model.fileTree.items if hasattr(self, 'model') else []
        can_resume = snapshot['paused'] or bool(snapshot['pending'] & snapshot['paused_files']) or any(
            task.task_type == TaskType.M3U8 and task.task_status in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED)
            and any(child.fileSize == '-' for child in task.childs) for task in tasks)
        return can_pause, can_resume

    def OnPauseAllDownloads(self, event):
        if self._GlobalDownloadActions()[0]:
            Downloader.Pause()
        self._SyncTaskRuntime()
        self.UpdateWindowUI(wx.UPDATE_UI_RECURSE)

    def OnResumeAllDownloads(self, event):
        if self._GlobalDownloadActions()[1]:
            Downloader.Resume()  # 同时清除全局暂停和单个任务的暂停标记。
            pending = Downloader.Snapshot()['pending']
            for task in list(self.model.fileTree.items):
                if task.task_type == TaskType.M3U8 and task.task_status in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED) and not any(
                        Downloader.FileKey(child.fileName) in pending for child in task.childs):
                    self._DownloadFiles(task.parent.fileName,
                                        [(index, None) for index, child in enumerate(task.childs) if child.fileSize == '-'])
        self._SyncTaskRuntime()
        self.UpdateWindowUI(wx.UPDATE_UI_RECURSE)

    def OnUpdateGlobalDownloadAction(self, event):
        can_pause, can_resume = self._GlobalDownloadActions()
        if event.GetId() == self.pauseAllItem.GetId():
            event.Enable(can_pause)
        else:
            event.Enable(can_resume)

    def OnUpdatePauseDownloads(self, event):
        paused = Downloader.IsPaused()
        busy = Downloader.IsBusy()
        can_pause, can_resume = self._GlobalDownloadActions()
        event.Enable(can_pause or can_resume)
        self._UpdatePauseTool()
        snapshot = Downloader.Snapshot()
        self.statusBar.SetStatusText(('暂停中' if snapshot['requesting'] else '已暂停') if paused
                                     else ('正在下载' if busy else '就绪'), 0)

    def _UpdatePauseTool(self):
        """菜单和工具栏共用下载状态；状态未变时不重复设置位图，避免工具栏闪动。"""
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
            '3. 下载设置\n'
            '通过“文件 → 设置”（Ctrl+,）调整默认下载目录、并发、请求间隔、重试、超时及自动合并。'
            '默认目录仅影响之后新建的任务，已有任务仍使用原目录，可在下载或合并期间修改。'
            '合并需要 FFmpeg，路径留空时先查找 scripts 目录，再查找系统 PATH。\n\n'
            '4. 界面显示\n'
            '通过“查看”菜单显示或隐藏工具栏、状态栏。'
            '下载窗口内的“？”可查看对应输入框的说明。'
        ))

    def OnAbout(self, event):
        self._ShowInformation('关于 AVDownloader', (
            'AVDownloader\n\n'
            '支持 M3U8 播放列表、TS 分片下载及 FFmpeg 合并 MP4。\n\n'
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
        # 只从数据库恢复任务；读取失败保留当前列表，不退回扫描目录。
        try:
            tree = self.model.tasks.load_tree()
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
        print("OnAllTSDownload", event.GetData())
        payload = event.GetData()
        if not payload:
            return
        fileName = payload.get("fileName", "")
        if payload.get('task_id') is not None:
            task = next((task for task in self.model.fileTree.items if task.task_id == payload['task_id']), None)
            if task is None:
                return  # 任务已删除，不处理之前排入 wx 队列的完成事件。
            fileName = task.parent.fileName
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

    def _DownloadCall(self, flag: bool, fileName: str, context):
        """下载回调携带 UUID 和序号；持久化失败时保留文件，供下次恢复核对。"""
        if not isinstance(context, tuple) or len(context) != 2:
            return
        task_id, sequence = context
        error = Downloader.Snapshot()['errors'].get(Downloader.FileKey(fileName))
        record = self._PersistTask(self.model.tasks.finish_segment, task_id, sequence, fileName, flag, error)
        if record is None:
            return  # 已删除的任务不会因迟到回调复活。
        self._SyncTaskRuntime()
        for index, task in enumerate(self.model.fileTree.items):
            if task.task_id != task_id:
                continue
            for offset, child in enumerate(task.childs):
                if child.sequence == sequence:
                    current = self.model.ObjectToItem(self.model._BuildKey((index, len(task.outputs) + offset)))
                    self.model.ItemChanged(current)
                    self.model.ItemChanged(self.model.GetParent(current))
                    break
            pending = Downloader.Snapshot()['pending']
            if (flag and task.childs and task.download == len(task.childs)
                    and task_id not in self._completion_notified
                    and not any(Downloader.FileKey(child.fileName) in pending for child in task.childs)):
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
        record = self._PersistTask(self.model.tasks.begin_download, task.task_id,
                                   [child.sequence for child in children])
        if record is None:
            return 0
        count = 0
        for child in children:
            context = (task.task_id, child.sequence)
            if not child.absUri:
                self._PersistTask(self.model.tasks.finish_segment, task.task_id, child.sequence,
                                  child.fileName, False, '分片缺少下载地址。')
                continue
            try:
                if Downloader.DownloadTSFile(child.absUri, child.fileName, self._DownloadCall, context):
                    count += 1
            except Exception as error:
                self._PersistTask(self.model.tasks.finish_segment, task.task_id, child.sequence,
                                  child.fileName, False, f'无法启动下载：{error}')
        if count == 0 and not any(Downloader.FileKey(child.fileName) in Downloader.Snapshot()['pending']
                                  for child in children):
            self._PersistTask(self.model.tasks.interrupt, task.task_id)
        self._SyncTaskRuntime()
        return count

    def _CreateM3U8File(self, tsSeed):
        """本地播放列表是可重建文件，丢失后从任务库恢复。"""
        task = next((task for task in self.model.fileTree.items
                     if task.parent and task.parent.fileName == tsSeed), None)
        try:
            if task is None:
                raise ValueError('任务不存在，请刷新后重试。')
            self.model.tasks.write_playlist(task.task_id)
            return True
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'生成本地播放列表失败：{error}', '提示')
            return False

    def _CreateMP4Call(self, flag: bool, fileName: str, task_id):
        """合并结果按任务 UUID 入库，不以行号或可能重复的显示名称定位。"""
        record = self._PersistTask(self.model.tasks.finish_merge, task_id, fileName, flag)
        if record is None:
            return
        self._RefreshWithState()
        if not flag:
            wx.MessageBox('视频文件合并失败，错误状态已保存，可重新转 MP4。', '提示')

    def _CreateMP4File(self, tsSeed: str, item):
        if not self._CreateM3U8File(tsSeed):
            return
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

        task = next((task for task in self.model.fileTree.items if task.parent.fileName == tsSeed), None)
        if task is None:
            return
        record = self._PersistTask(self.model.tasks.begin_merge, task.task_id)
        if record is None:
            return
        try:
            Converter.ConvertTSFile(playlist, outputFile, self._CreateMP4Call, task.task_id)
        except Exception:
            self._CreateMP4Call(False, outputFile, task.task_id)
