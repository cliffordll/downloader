# AVDownloader

基于 Python 和 wxPython 的桌面下载器，支持 M3U8 播放列表、按命名规则添加 TS 分片、MP4 直链下载，以及使用 FFmpeg 合并 MP4。

最近发布版本：**v0.1.0**。当前正在开发 AVDownloader 新版，新建 M3U8/TS/MP4 任务和任务列表已接入 SQLite，不再扫描下载目录发现任务。MP4 直链下载已接入，RTMP 直播录制尚未实现。

列表按任务类型展示：M3U8 可展开查看分片，MP4/RTMP 仅显示一行。MP4 按字节显示进度（总大小未知时只显示已下载体积），RTMP 显示录制时长和体积。“展开/折叠”和“转 MP4”仅用于 M3U8。RTMP 暂未提供添加入口，其执行操作保持置灰。

通过“文件 → 下载 MP4”（Ctrl+P）或工具栏添加 HTTP/HTTPS 视频直链，指定独立任务子目录后开始下载。
行内支持暂停、继续、失败重试及删除，全部暂停/继续也包含 MP4；删除仅移除记录，保留文件。
MP4 和 TS 共用设置中的并发上限及请求间隔。下载中保存为 `video.mp4.part`，完成后发布为 `video.mp4`，无需 FFmpeg。
续传使用 Range 和 If-Range 校验；无文件标识、服务器不支持续传或文件已改变时重新下载，避免错误拼接。
MP4 暂停会保留已写入内容；等待网络响应期间可能要等到响应返回或配置的超时后才显示“已暂停”。
退出保存中断状态，重启后手动继续；完整文件重命名与数据库更新之间发生退出，也可在启动时恢复。

## 源码目录

```text
src/
├─ schemas/      # task.py、segment_base.py：业务数据结构及校验
├─ storage/      # task_repository.py：SQLite 读写
├─ core/         # 应用配置、路径与任务管理
│  ├─ app_paths.py           # 应用数据目录和数据库路径
│  ├─ sys_setting.py         # 设置读取、校验和保存
│  ├─ task_service.py        # 任务创建、状态更新和恢复
│  └─ path_manager.py        # 下载路径处理
├─ media/        # 媒体下载、解析、检测与转换
│  ├─ downloader.py         # 公共并发、请求间隔与服务器限流
│  ├─ mp4/
│  │  └─ mp4_downloader.py
│  └─ m3u8/
│     ├─ m3u8_downloader.py
│     ├─ m3u8_parser.py
│     └─ ffmpeg_converter.py     # 时长检测、合并清单与 MP4 转换
├─ models/       # tree_model.py、presentation.py、file_base.py：列表模型和展示数据
└─ views/        # 主窗口（包含任务操作和回调）、渲染器及弹窗
   ├─ components/ # icons.py、renderers.py：图标加载和单元格绘制
   └─ dialogs/   # m3u8_dialog.py、ts_dialog.py、settings_dialog.py：弹窗
      └─ panels/  # m3u8_form.py、ts_form.py、path_picker.py：表单和路径选择
```

当前入口使用 `views/main_frame.py`，旧界面和目录扫描实现已移除。核心任务服务返回任务记录，
列表行转换由 `models/tree_model.py` 负责；树形行结构及文件大小、日期格式化位于
`models/file_base.py`。任务操作和异步回调集中在 `views/main_frame.py`，
回调绑定到主窗口，窗口销毁检查仍然有效。设置窗口位于 `views/dialogs/settings_dialog.py`，
M3U8 添加界面使用 `m3u8_dialog.py`、`panels/m3u8_form.py`，FFmpeg 合并清单生成位于
`media/m3u8/ffmpeg_converter.py`，由 `FFmpegConverter` 类统一封装清单生成、分片时长检测与后台转换。
两个具体下载器共用 `media/downloader.py` 的并发与限流控制。

## 安装与运行

已验证环境：Windows、Python 3.10.11。建议使用 Python 3.10 创建独立环境，在项目根目录执行：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

源码包不包含 Python、Windows 可执行程序或 FFmpeg。仅下载分片无需 FFmpeg；合并 MP4 时需要自行安装 FFmpeg，并按下面的设置说明配置。

## 使用

1. 在“文件 → 设置”中选择默认下载目录，仅用于之后新建的任务。
2. 使用“下载 M3U8”添加播放列表，或使用“下载 TS”按分片命名规则添加任务。输入框旁的“？”提供说明。
3. 在操作列开始下载、暂停、继续、失败重试或删除任务。“更多”中提供转 MP4、打开文件夹和播放视频。
4. 双击任务仅展开或折叠。关键词匹配任务、MP4 和分片的文件名或路径，并可与状态组合筛选；隐藏任务仍正常下载。

“任务”菜单提供全部暂停、全部继续；后者恢复当前队列以及重启后处于“已暂停 / 已中断”的未完成下载，不会启动“未开始”任务。当前队列里只下载部分分片时，继续仍保留原队列范围。已发出的请求允许完成，暂停只阻止后续请求。

新任务需要独立且尚不存在的子目录，避免覆盖其他任务或已有文件。分片按序号存放于 `segments` 子目录，`download.m3u8` 是可重建的本地播放列表，不再生成 `.seed`。不会自动导入旧版目录里的任务。

“查看 → 默认展开任务”保存新任务的展示偏好；“全部展开/折叠”只改变当前列表。删除操作会确认后移除数据库记录，保留本地文件；刷新不会重新导入被删除的任务。

## 快捷键

| 操作 | 快捷键 |
| --- | --- |
| 打开下载文件夹 | Ctrl+O |
| 添加 M3U8 / TS | Ctrl+M / Ctrl+T |
| 设置 | Ctrl+, |
| 刷新 / 查找筛选框 | F5 / Ctrl+F |
| 全部暂停 / 继续 | Ctrl+Shift+P / Ctrl+Shift+R |
| 全部展开 / 折叠 | Ctrl+Shift+E / Ctrl+Shift+C |
| 下载 MP4 | Ctrl+P |
| 使用说明 | F1 |
| 清空关键词（筛选框内） | Esc |

## 下载设置

在“文件 → 设置”（Ctrl+,）中调整默认下载目录、最大并发数、请求启动间隔、失败重试次数、连接/读取超时、FFmpeg 路径和自动合并。

- 默认：3 个并发、请求间隔 0.5 秒、失败重试 2 次、连接超时 10 秒、读取超时 30 秒，自动合并关闭。
- FFmpeg 优先使用手动配置的路径；留空时先查找项目的 `scripts/ffmpeg.exe`（其他系统为 `scripts/ffmpeg`），再查找系统 PATH。可执行文件作为本地依赖，不纳入 Git。
- 设置保存于当前用户主目录下的 `.avdownloader/settings.json`（例如 `C:\Users\lianaipeng\.avdownloader\settings.json`），任务数据库也存放在该目录。不依赖 APPDATA 或程序启动目录，新版不读取或迁移旧版设置。默认下载目录为项目下的 `downloads`，不随启动目录变化。
- 保存后对后续请求生效；降低并发数不会中断已有请求。下载或合并期间也可以修改默认下载目录，已有任务的下载、重试和合并继续使用原目录，不移动已有文件。工具栏“打开下载文件夹”打开当前默认目录；任务“更多 → 打开文件夹”打开该任务的目录。
- 请求间隔约束全局请求启动频率，包含重试。429 会暂停新请求并遵守 `Retry-After`；403 不自动重试；网络错误和 5xx 使用有次数上限的退避重试。
- 自动合并会在任务的全部分片下载完成后调用 FFmpeg，已有 `output.mp4` 时不自动覆盖。
- 状态、分片结果、文件大小及错误信息会保存到任务库。重启保留暂停和失败记录，将未结束的下载/合并标为“已中断”，不自动发起下载；下载可手动继续，合并需重新选择“转 MP4”。
- FFmpeg 先写 `output.mp4.part.mp4`，成功后才发布 `output.mp4`。临时文件不会显示为已完成的视频，合并失败可以重试。

## 已知限制

- 每个任务保存独立的绝对目录，切换默认下载目录不改变已有任务。进度按完整分片保存，正在下载的单个分片不支持字节级断点续传。
- 暂不支持加密、字节范围或带初始化片段的 M3U8；主播放列表需要先选择具体清晰度的媒体列表。
- 合并使用 FFmpeg 流复制，无法保证修复原始分片的时间戳或编码问题。
- 部分 Windows 环境首次启动时可能仍出现操作列短暂闪烁；已减少无效刷新，但尚未确认完全消除。

## 新版任务存储（开发中）

`src/managers/task_repository.py` 提供 `TaskRepository`。默认数据库位于应用数据目录的 `downloads.db`，主窗口首次加载列表时初始化。测试可传入独立数据库路径，不接触用户数据。

- `create(task)` 新增，`get(id)` 读取，`list_tasks()` 恢复全部任务，`update(task)` 更新，`delete(id)` 删除记录。
- `tasks` 保存公共字段，`task_m3u8`、`task_mp4`、`task_rtmp` 保存各类型详情，`task_segments` 保存分片，`task_outputs` 保存输出文件。详情与进度字段使用 JSON，并通过统一任务模型校验。
- 保存目录属于每个任务。存储操作不扫描下载目录，不创建下载文件，也不启动下载；删除记录不会删除磁盘文件。
- 一次任务写入在同一事务内完成，失败全部回滚。`update` 使用 `updated_at` 检查旧快照，成功后返回带新时间的任务；调用方必须保留返回值，遇到 `TaskConflictError` 时重新读取并处理冲突。
- 下载回调通过 `mutate` 在事务内读取最新记录，只写变化的分片/输出行，避免并发结果覆盖。回调使用任务 UUID 和分片序号，记录删除后迟到的回调不会重新创建任务。
- 数据库使用版本号保护，遇到未知版本或损坏的任务数据会报错，不自动清空重建。不提供旧任务导入或旧数据迁移。

`TaskService` 负责新建任务、状态流转、启动恢复及列表投影。刷新只检查库中登记的具体文件，不扫描目录寻找任务；已写完但未回调的分片会恢复为完成，丢失的文件会修正进度。状态未变时不重复写库；写库失败会暂停后续请求并提示。播放列表丢失时可从数据库重建。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

v0.1.0 发布前通过 57 项测试。测试使用临时配置和模拟 HTTP/FFmpeg，不发起真实下载；界面相关测试需要可用的桌面环境。

版本变更见 [CHANGELOG.md](CHANGELOG.md)。
