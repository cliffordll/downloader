"""任务列表的进度条和操作单元格绘制。"""
import json
import wx
import wx.dataview as dv

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
    用户点击 → ActivateAt 判断区域 → 主窗口 OnTaskAction 执行业务操作。
    Mac 鼠标由窗口转换坐标，其余平台与键盘由 ActivateCell 接入。
    """
    def __init__(self, frame):
        # ACTIVATABLE 保留键盘激活，以及其他平台的原生鼠标激活。
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

    def MinimumWidth(self, dc):
        """按五项操作的最长文字预留间距，以及原生单元格的左右留白。"""
        labels = ('开始', '暂停', '继续', '重试', '删除', '展开', '折叠', '更多', '录制', '停止')
        text_width = max(dc.GetTextExtent(label)[0] for label in labels)
        if wx.Platform == '__WXMAC__':
            return max(self.frame.FromDIP(200),
                       5 * (text_width + self.frame.FromDIP(8)) + self.frame.FromDIP(16))
        return max(self.frame.FromDIP(240),
                   5 * (text_width + self.frame.FromDIP(16)) + self.frame.FromDIP(16))

    def ActivateCell(self, cell, model, item, col, mouseEvent):
        """非 Mac 鼠标使用单元格局部坐标；Mac 鼠标由窗口统一处理。"""
        if mouseEvent is not None and getattr(self.frame, '_manualActionClicks', False):
            return False
        point = mouseEvent.GetPosition() if mouseEvent is not None else None
        return self.ActivateAt(cell, model, item, col, point)

    def ActivateAt(self, cell, model, item, col, point):
        """绘制和点击共用区域划分；point 相对单元格，None 表示键盘激活。"""
        value = model.GetValue(item, col)
        if not value:
            return False
        if value.startswith('['):
            actions = json.loads(value)
            if point is None:
                # 键盘优先开始/继续，否则打开更多，绝不默认删除。
                action = actions[0] if actions[0]['enabled'] else actions[-1]
            else:
                rects = self._ActionRects(wx.Rect(0, 0, cell.width, cell.height), len(actions))
                action = next((entry for entry, rect in zip(actions, rects)
                               if rect.Contains(point)), None)
            if action is None or not action['enabled']:
                return False
            self.frame.OnTaskAction(item, action['id'])
        else:
            if point is not None and not wx.Rect(0, 0, cell.width, cell.height).Contains(point):
                return False
            self.frame.OnSegmentDownload(item)
        return True
