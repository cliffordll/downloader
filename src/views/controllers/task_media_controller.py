"""任务创建弹窗、M3U8 分片下载入口和媒体产物操作控制器。"""

import sqlite3

import wx

from src.core.sys_setting import SysSetting
from src.schemas.task import TaskType
from src.storage.task_repository import TaskDataError
from src.views.dialogs.m3u8_dialog import DownloadDialogMU
from src.views.dialogs.ts_dialog import DownloadDialogTS
from src.views.dialogs.mp4_dialog import DownloadDialogMP4


class TaskMediaController:
    """负责创建任务及请求清单生成/MP4 合并等媒体操作。"""

    def __init__(self, frame):
        self.frame = frame

    def OnAddMU(self, event):
        workPath = SysSetting.GetWorkPath()
        dlg = DownloadDialogMU(self.frame, workPath, task_service=self.frame.tasks)
        result = dlg.ShowModal()
        if result == wx.OK:
            # 创建新任务成功，自动刷新页面
            self.frame.OnRefresh(None)
        dlg.Destroy()

    def OnAddTS(self, event):
        workPath = SysSetting.GetWorkPath()
        dlg = DownloadDialogTS(self.frame, workPath, task_service=self.frame.tasks)
        result = dlg.ShowModal()
        if result == wx.OK:
            # 创建新任务成功，自动刷新页面
            self.frame.OnRefresh(None)
        dlg.Destroy()

    def OnAddMP4(self, event):
        dialog = DownloadDialogMP4(self.frame, SysSetting.GetWorkPath(), self.frame.tasks)
        try:
            if dialog.ShowModal() == wx.OK:
                self.frame.OnRefresh(None)
        finally:
            dialog.Destroy()

    def _DownloadFiles(self, tsSeed: str, tasks: list):
        """先保存排队状态，再提交带稳定身份的分片请求。"""
        task = next((task for task in self.frame.model.fileTree.items
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
            count = self.frame.runner.start(task.task_id, sequences=[child.sequence for child in children])
            if count:
                self.frame._completion_notified.discard(task.task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'无法启动分片下载：{error}', '下载失败', parent=self.frame)
            return 0
        self.frame._SyncDownloads()
        return count

    def _CreateM3U8File(self, tsSeed):
        """本地播放列表是可重建文件，丢失后从任务库恢复。"""
        task = next((task for task in self.frame.model.fileTree.items
                     if task.parent and task.parent.fileName == tsSeed), None)
        try:
            if task is None:
                raise ValueError('任务不存在，请刷新后重试。')
            self.frame.runner.write_playlist(task.task_id)
            return True
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'生成本地播放列表失败：{error}', '提示')
            return False

    def _CreateMP4File(self, tsSeed: str, item):
        """菜单事件只定位任务并显示错误；清单准备、合并及结果落库由 TaskRunner 负责。"""
        task = next((task for task in self.frame.model.fileTree.items if task.parent.fileName == tsSeed), None)
        if task is None:
            return
        try:
            self.frame.runner.merge(task.task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '合并失败', parent=self.frame)
        self.frame._SyncDownloads()

