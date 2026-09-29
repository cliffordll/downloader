# 实现下载视频功能

pip install wx -i https://pypi.tuna.tsinghua.edu.cn/simple
## 下载设置

在“文件 → 设置”（Ctrl+,）中调整下载目录、最大并发数、请求启动间隔、失败重试次数、连接/读取超时、FFmpeg 路径和自动合并。

- 默认：3 个并发、请求间隔 0.5 秒、失败重试 2 次、连接超时 10 秒、读取超时 30 秒，自动合并关闭。
- FFmpeg 路径留空时从系统 PATH 自动检测。设置自定义路径时请选择现有可执行文件。
- Windows 设置保存于 `%APPDATA%/M3U8Downloader/settings.json`；其他系统保存于 `~/.config/M3U8Downloader/settings.json`。默认下载目录为项目下的 `downloads`，不随启动目录变化。
- 保存后对后续请求生效；降低并发数不会中断已有请求。下载或转换进行中不能切换下载目录。
- 请求间隔约束全局请求启动频率，包含重试。429 会暂停新请求并遵守 `Retry-After`；403 不自动重试；网络错误和 5xx 使用有次数上限的退避重试。
- 自动合并会在任务的全部分片下载完成后调用 FFmpeg，已有 `output.mp4` 时不自动覆盖。

回归测试：`python -m unittest discover -s tests -v`。测试使用临时配置和模拟 HTTP/FFmpeg，不修改个人设置、不发起真实下载。
