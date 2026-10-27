# Downloader 专属 Logo 资源

本目录属于 downloader 项目。当前 Logo、原稿、小图版本和历史版本均保留在本项目，不放入通用 icons 仓库。

| 文件 | 用途 |
| --- | --- |
| `logo.svg` | 当前窗口、任务栏、Dock 和对话框运行时使用的 Logo |
| `logo.png` | 高分辨率 PNG 版本 |
| `logo-small.svg`、`logo-small.png` | 小尺寸设计版本，作为项目资源保留 |
| `logo1.png`、`logo2.png` | 原稿／历史设计版本 |

实际运行时加载以 `src/views/components/icons.py` 为准。已手动删除的非 Logo 应用导航图标，其 source 和四档 PNG 已同步清理。

项目中的临时工作稿位于 `.tmp-test/`，历史素材备份位于 `backups/icons-before-consolidation.zip`；这些文件都归 downloader 自身，不导入通用图标项目。
