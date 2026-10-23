"""任务列表的筛选、刷新、展开状态和行布局控制器。"""

import sqlite3
from pathlib import Path

import wx
import wx.dataview as dv

from src.models.tree_model import load_tree
from src.core.sys_setting import SysSetting
from src.schemas.task import TaskStatus, TaskType
from src.storage.task_repository import TaskDataError


class TaskListController:
    """集中管理列表交互和从任务模型到控件的同步。"""

    def __init__(self, frame):
        self.frame = frame

    def _SearchItems(self, text):
        """应用关键词筛选；命中任务、MP4 或分片路径均保留整个任务。"""
        self.frame._filterText = text.strip().casefold()
        self.frame._ApplyTaskFilter()

    def _UpdateFilterCount(self):
        total = len(self.frame.model.fileTree.items)
        visible = total if self.frame.model.visible_tasks is None else len(self.frame.model.visible_tasks)
        label = f'显示 {visible} / {total} 个任务'
        if self.frame.filterCount.GetLabel() != label:
            self.frame.filterCount.SetLabel(label)
        filtered = bool(self.frame._filterText) or self.frame.statusFilter.GetSelection() > 0
        # 无任务时保持空列表；仅筛选无匹配结果时显示清除筛选提示。
        empty = total > 0 and visible == 0 and filtered
        self.frame.clearFiltersButton.Show(filtered)
        if self.frame.emptyPanel.IsShown() != empty:
            self.frame.emptyPanel.Show(empty)
            self.frame.emptyPanel.GetParent().Layout()
        if empty:
            self.frame.emptyPanel.Layout()

    def _ApplyTaskFilter(self, force=False, expanded_keys=None):
        self.frame.ClearTaskToolTip()
        """只筛选根任务，保持原始任务索引，避免筛选后暂停、删除或下载回调操作错行。"""
        text = self.frame._filterText
        status = self.frame.statusFilter.GetStringSelection()
        visible = None
        locked_encrypted = {index for index, task in enumerate(self.frame.model.fileTree.items)
                            if task.encrypted and not self.frame._encrypted_tasks_unlocked}
        if text or status != '全部状态' or locked_encrypted:
            visible = set()
            for index, task in enumerate(self.frame.model.fileTree.items):
                if index in locked_encrypted:
                    continue
                if text and not (
                    (task.parent and (text in task.parent.fileName.casefold()
                                     or text in task.parent.displayName.casefold()))
                    or any(text in file.fileName.casefold() for file in task.outputs)
                    or any(text in file.fileName.casefold() for file in task.childs)
                ):
                    continue
                if status != '全部状态' and self.frame.model.TaskInfo(index)['status'].split(' · ', 1)[0] != status:
                    continue
                visible.add(index)
        if force or visible != self.frame.model.visible_tasks:
            expanded = set() if expanded_keys is None else set(expanded_keys)
            root = dv.NullDataViewItem
            if expanded_keys is None:
                self.frame._SaveExpandState(root, expanded)
            # 刷新时只给新任务应用默认值，已存在任务保留用户手动展开/折叠的状态。
            if SysSetting.GetAll()['default_expand_tasks']:
                for index, task in enumerate(self.frame.model.fileTree.items):
                    if task.parent and task.parent.fileName not in self.frame._knownTaskPaths:
                        expanded.add(self.frame.model._BuildKey((index,)))
            selected = self.frame.mcTree.GetSelection()
            selected_key = self.frame.model.ItemToObject(selected) if selected.IsOk() else None
            self.frame.mcTree.Freeze()
            try:
                self.frame.model.visible_tasks = visible
                self.frame.model.Cleared()
                self.frame._RestoreExpandState(root, expanded)
                self.frame._UpdateRowNumbers()
                if selected_key is not None and not force:
                    index = self.frame.model.ParseKey(selected_key)[0]
                    if visible is None or index in visible:
                        self.frame.mcTree.Select(self.frame.model.ObjectToItem(selected_key))
            finally:
                self.frame.mcTree.Thaw()
        self.frame._UpdateFilterCount()
        self.frame._knownTaskPaths = {task.parent.fileName for task in self.frame.model.fileTree.items if task.parent}
        if hasattr(self.frame, '_defaultTaskWidth'):
            self.frame._FitTaskColumns()

    def _RecursiveExpand(self, item, expand):
        """递归展开/折叠"""
        child, cookie = self.frame.model.GetFirstChild(item)
        while child.IsOk():            
            if self.frame.model.IsContainer(child):
                self.frame._SetTaskExpanded(child, expand)
            else:
                self.frame._RecursiveExpand(child, expand)
            child, cookie = self.frame.model.GetNextChild(item, cookie)

    def _RefreshWithState(self):
        """重新加载视图时仍应用当前条件，并保留可见任务的展开状态。"""
        self.frame._ApplyTaskFilter(force=True)

    def _SaveExpandState(self, parent, expandeds):
        """递归保存展开状态"""
        child, cookie = self.frame.model.GetFirstChild(parent)
        while child.IsOk():            
            if self.frame.model.IsContainer(child):
                if self.frame.mcTree.IsExpanded(child):
                    obj = self.frame.model.ItemToObject(child)
                    expandeds.add(obj)
                    self.frame._SaveExpandState(child, expandeds)
            child, cookie = self.frame.model.GetNextChild(parent, cookie)

    def _RestoreExpandState(self, parent, expandeds):
        """根据上一次展开折叠状态，递归展开或折叠"""
        child, cookie = self.frame.model.GetFirstChild(parent)
        while child.IsOk():            
            if self.frame.model.IsContainer(child):
                obj = self.frame.model.ItemToObject(child)
                if obj in expandeds:
                    self.frame._SetTaskExpanded(child, True)
                    self.frame._RestoreExpandState(child, expandeds)
            child, cookie = self.frame.model.GetNextChild(parent, cookie)

    def OnFind(self, event):
        """菜单和 Ctrl+F 共用：聚焦现有筛选框，选中文字以便直接输入新条件。"""
        self.frame.searchCtrl.SetFocus()
        self.frame.searchCtrl.SelectAll()

    def OnSearch(self, event):
        """点击搜索按钮或按回车立即筛选，取消尚未执行的延迟筛选。"""
        self.frame._searchTimer.Stop()
        self.frame._SearchItems(event.GetEventObject().GetValue())

    def OnSearchText(self, event):
        """输入停止 200 毫秒后才筛选；清空时立即移除关键词条件。"""
        self.frame._searchTimer.Stop()
        self.frame._searchText = event.GetString()
        if self.frame._searchText.strip():
            self.frame._searchTimer.StartOnce(200)
        else:
            self.frame._SearchItems('')

    def OnSearchKey(self, event):
        """只处理筛选框内的 Esc，不注册全局快捷键，也不清除状态筛选。"""
        if event.GetKeyCode() == wx.WXK_ESCAPE and not event.HasAnyModifiers():
            self.frame.OnClearSearch(event)
        else:
            event.Skip()

    def OnClearSearch(self, event):
        self.frame._searchTimer.Stop()
        self.frame._searchText = ''
        self.frame.searchCtrl.ChangeValue('')
        self.frame._SearchItems('')

    def OnClearAllFilters(self, event):
        """空结果提示中的恢复入口，同时清除关键词、状态及尚未执行的筛选。"""
        self.frame.statusFilter.SetSelection(0)
        self.frame.OnClearSearch(event)

    def OnStatusFilter(self, event):
        self.frame._searchTimer.Stop()
        self.frame._SearchItems(self.frame.searchCtrl.GetValue())

    def OnSearchTimer(self, event):
        """计时结束时只搜索最新输入；清空输入或关闭窗口会取消计时。"""
        if self.frame._closing:
            return
        self.frame._SearchItems(self.frame._searchText)

    def OnExpandAll(self, event):
        """展开所有节点"""
        # root = self.frame.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self.frame._RecursiveExpand(root, True)

    def OnCollapseAll(self, event):
        """折叠所有节点"""
        # root = self.frame.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self.frame._RecursiveExpand(root, False)

    def OnRefresh(self, event):
        self.frame.ClearTaskToolTip()
        self.frame.runner.reset_duration_failures()
        # 只从数据库恢复任务；读取失败保留当前列表，不退回扫描目录。
        try:
            tree = load_tree(self.frame.tasks)
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'读取任务失败：{error}', '刷新失败', wx.OK | wx.ICON_WARNING, parent=self.frame)
            return
        # 删除或新增记录会改变行号，先按任务身份保存展开状态，再映射到新行。
        expanded_ids = set()
        expanded_folders = set()
        for index, task in enumerate(self.frame.model.fileTree.items):
            item = self.frame.model.ObjectToItem(self.frame.model._BuildKey((index,)))
            if self.frame.mcTree.IsExpanded(item):
                expanded_ids.add(task.task_id or task.parent.fileName)
                for key in (-4, -1):
                    folder = self.frame.model.ObjectToItem(self.frame.model._BuildKey((index, key)))
                    if self.frame.model.IsContainer(folder) and self.frame.mcTree.IsExpanded(folder):
                        expanded_folders.add((task.task_id or task.parent.fileName, key))
        self.frame.model.fileTree = tree
        expanded = {self.frame.model._BuildKey((index,)) for index, task in enumerate(tree.items)
                    if (task.task_id or task.parent.fileName) in expanded_ids}
        expanded.update(self.frame.model._BuildKey((index, key)) for index, task in enumerate(tree.items)
                        for key in (-4, -1) if (task.task_id or task.parent.fileName, key) in expanded_folders)
        self.frame._ApplyTaskFilter(force=True, expanded_keys=expanded)

    def OnActivatedChanged(self, event):
        """双击/键盘激活展开目录，打开播放列表或已完成的视频。"""
        column = event.GetDataViewColumn()
        if column is not None and column.GetModelColumn() == 4:
            return  # The custom renderer handles action cells on a single click.
        item = event.GetItem()
        if not item.IsOk():
            return
        keys = self.frame.model.ItemToObject(item)
        objs = self.frame.model.ParseKey(keys)
        if self.frame.model.IsContainer(item):
            self.frame._SetTaskExpanded(item)
        elif len(objs) == 2 and objs[1] in self.frame.model.fileTree.items[objs[0]].manifests:
            task = self.frame.model.fileTree.items[objs[0]]
            self.frame._OpenTaskManifest(task, task.manifests[objs[1]].fileName)
        elif len(objs) == 2:
            task = self.frame.model.fileTree.items[objs[0]]
            if 0 <= objs[1] < len(task.outputs):
                output = task.outputs[objs[1]]
                if Path(output.fileName).suffix.lower() == '.mp4' and (
                        task.task_type != TaskType.MP4 or task.task_status == TaskStatus.COMPLETED):
                    self.frame._OpenVideo(output.fileName, task.task_id)

    def _SetTaskExpanded(self, item, expanded=None):
        """统一展开/收缩入口；未指定状态时切换，状态相同则不重复更新。"""
        if self.frame._closing or not item.IsOk() or not self.frame.model.IsContainer(item):
            return
        current = self.frame.mcTree.IsExpanded(item)
        target = not current if expanded is None else expanded
        if current == target:
            return
        if target:
            self.frame.mcTree.Expand(item)
        else:
            self.frame.mcTree.Collapse(item)
        self.frame._QueueRowNumbers()

    def _QueueRowNumbers(self, event=None):
        self.frame.ClearTaskToolTip()
        if event is not None:
            event.Skip()
        if self.frame._closing or getattr(self.frame, '_rowNumbersPending', False):
            return
        self.frame._rowNumbersPending = True
        # 等原生控件完成展开，并合并“全部展开”等连续操作的通知。
        wx.CallAfter(self.frame._UpdateRowNumbers)

    def _UpdateRowNumbers(self):
        self.frame._rowNumbersPending = False
        if self.frame._closing or not self.frame or self.frame.IsBeingDeleted():
            return
        if self.frame.model.UpdateRowNumbers(self.frame.mcTree):
            self.frame.mcTree.Refresh()
        if hasattr(self.frame, '_defaultTaskWidth'):
            self.frame._FitTaskColumns()

