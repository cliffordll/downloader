# Downloader 运行时图标

本目录的通用图标只保存当前界面使用的副本。通用图标库及设计、生成工具在独立 icons 项目中维护：本地 `D:\opendemo\icons`，仓库 https://github.com/cliffordll/icons。`logo/` 属于 downloader 专有资源，包含当前版本、原稿、临时及历史文件，只在本项目维护。

## 保留资源

- `24/`、`30/`、`36/`、`48/`：工具栏及界面使用的 PNG。
- `source/tools/`、`source/files/`：同名高分辨率 PNG，供密码控件等其他 DIP 尺寸缩放使用。
- `logo/`：downloader 专属 Logo、原稿、小图及历史版本；其中 `logo/logo.svg` 是当前窗口、任务栏、Dock 和对话框使用的应用 Logo。专属资源说明见 [logo/README.md](logo/README.md)。

通用运行时图标由 `src/views/components/icons.py` 的 `ICON_FILES` 映射决定；仅保留当前工具栏、加密状态切换和密码显示控件引用的资源。`logo/` 私有资源及其历史文件不受这项通用图标精简规则限制。

## 增加或更新图标

1. 在 icons 项目中选择或维护图标。
2. 将通用图标的 source PNG 和 24、30、36、48 四档 PNG 复制到本目录的相同相对位置。downloader 专属应用图标在本项目自己的 `icons/logo/` 目录维护。
3. 在 `ICON_FILES` 中登记资源，并在对应界面中使用；本项目不依赖图标库的绝对路径或 Git 子模块。
4. 运行 `python -m pytest tests/test_icon_resources.py`，检查资源完整性、尺寸和运行时加载。

通用图标的创作、生成、全量清单、总览及排版测试归 icons 项目维护；downloader 专属应用图标及其临时、历史文件不进入通用 icons 仓库。
