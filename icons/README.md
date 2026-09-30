# 统一 PNG 图标库

## 定版应用 Logo（2026-09-30）

- `source/app/logo.svg` / `logo.png`：三条平行斜杠的大图，PNG 为 512×512 透明背景。
- `source/app/logo-small.svg` / `logo-small.png`：标题栏专用简化版，保留加宽的中间蓝色块，PNG 为 512×512 透明背景。
- `source/app/logo1.png`：原始四斜杠蓝色 Logo，作为旧版备份保留，不参与运行时图标加载。
- 只保留图标库使用的 24、30、36、48 四档目录；各档不再保存 `logo.png`、`logo2.png` 或 `logo-small.png`。图标预览页的 Logo 统一读取 `source/app`，定版 PNG 也保留在该目录。
- Windows 标题栏单独使用小图；任务栏使用大图。SVG 源文件继续保留，程序当前仍直接从 SVG 绘制应用图标。

共 156 个命名图标：116 个文件类型、33 个工具操作、7 个应用／导航图标。
本目录是程序唯一使用的图标库。原图、旧工具栏资源和设计稿已归档到 `../backups/icons-before-consolidation.zip`。

## 设计规则

- 工具图标：深灰 `#444444`，以 24 像素下约 1.35 像素的线宽为基准，无额外彩色色块。
- 文件类型：灰色折角文档、每种文件类型各不相同的浅色圆形色块、深灰窄体格式名，圆形直径为 24px 画布下 15px，保持正圆，只与左边框交叉，底部保留间距。
- 文件轮廓使用等宽灰色线条。文字碰到任意一侧边框时，左右同时留出缺口；两侧均未碰到时保留完整边框。不按字符数判断，不单独压窄短标签。
- 一般图形约占 20 像素；打开图标约 23 像素，双尖括号约 16 像素，按视觉大小调整留白。
- 应用 logo 保留原有填充造型和蓝色／灰色两个版本，避免把应用标识误当成工具按钮。
- 全部格式名使用相同窄体字形比例、字高和基线，允许长标签适度超出文档边框；24 像素下的长标签仍应配合文件名或提示阅读。

## 目录与替换

```text
source/{tools,files,app}/名称.png  高分辨率透明 PNG
24/{tools,files,app}/名称.png      100% 缩放
30/{tools,files,app}/名称.png      125% 缩放
36/{tools,files,app}/名称.png      150% 缩放
48/{tools,files,app}/名称.png      200% 缩放
manifest.json                    完整名称、用途及来源清单
gallery.html                     可搜索、切换分类和显示尺寸的本地图标总览
```

程序统一从本目录读取 PNG，不再使用 `icons/toolbar/`、`icons/unified/` 或旧的根目录图标。
工具栏读取 24、30、36、48 四档图片；窗口及导航的其他尺寸从 `source` 缩放。路径相对于项目目录解析，不依赖启动时的工作目录。
替换普通图标时，请同步替换四档 PNG 和 `source` 中的同名 PNG；Logo 仅维护 `source/app` 中的资源。工具栏 M3U8 对应 `files/m3u8.png`，TS 对应 `files/ts.png`。

折叠图标统一使用 `tools/collapse.png`。
TS 统一保留紫色色块版本，文件名为 `files/ts.png`。

## 本次补齐

工具：开始、暂停、停止、重试、搜索、保存、复制、链接、完成、关闭、警告、外部打开、排序、筛选、全部下载、合并。
首页、下载、上传、列表、设置也提供工具目录别名。

文件类型：M3U、MPD、WEBM、M4A、FLAC、OPUS、WEBP、SVG、HEIC、AVIF、TAR、TGZ、BZ2、XZ、LOG、INI、YAML、YML、TOML、SQL、DB、SQLITE、EPUB、SEED。
这些是图标素材，新增图标不代表程序已实现对应操作或格式处理能力。

## 素材说明

文件标签采用 Arial Narrow Bold，按目标尺寸直接绘制；24px 下字号 10px、字高 7px、基线 16px，采用清晰的实色像素笔画，不再额外压窄字形；基础设计 PNG 保存在旧资源备份中。
加载入口为 `src/views/main_frame.py`，窗口、导航和工具栏共用相同的资源目录。
