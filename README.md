# Inkwell — 原生 Markdown 阅读器

一个本地运行的桌面 Markdown 阅读器（Windows：pywebview + WebView2；macOS：pywebview + WKWebView），界面简洁、自适应窗口，
专为「读」与「复制到飞书/Word」优化。

## 功能

- **无边框现代界面**：自定义标题栏（文件名 + 工具按钮 + 窗口控制），浅色 / 深色主题（记忆上次选择）。
- **目录 / 搜索**：左侧自动生成目录，滚动高亮当前位置、点击跳转；`Ctrl+F` 全文搜索（高亮 + 上下跳转）。
- **公式、图示与代码渲染**：KaTeX 离线渲染 LaTeX；` ```mermaid ` 离线绘制图示，标题栏可切换「图示 / 原始代码」，点击图示（或「放大」按钮）进入灯箱放大查看；Pygments 语法高亮；带文件名的代码块（``` lang:path）。
- **Base64 内嵌图片**：支持 `![图](data:image/png;base64,...)` 这类 data URI 直接渲染，无需图片文件存在于本地路径；内嵌图片会自动解码落盘到临时目录，经内置服务器提供，避免 base64 常驻 DOM。
- **图片查看与复制**：点击图片后按当前窗口比例自适应放大；底部按钮或 `Ctrl+滚轮` 可继续缩放，右键按住拖动可平移查看；图片右上角可直接复制像素内容到飞书、Word 等应用。Mermaid 图示共用同一灯箱（含缩放、右键平移、复制为 PNG）。
- **飞书友好复制**：选中正文复制后，粘贴到飞书 / Word **不带底色、不带彩色文字**（自动净化为黑字无背景），同时保留标题 / 加粗 / 列表 / 表格结构。
- **公式可复制为 LaTeX**：复制公式得到 `$...$` / `$$...$$` 源码，可直接粘贴到支持 LaTeX 的文档；每条块级公式另有「复制公式」按钮。
- **代码双击高亮**：在代码块内双击任意标识符，高亮该 token 的所有出现（VSCode 风格），`Esc` 或点击别处清除。
- **自适应布局**：任意宽高比窗口都可用；窗口变窄时目录自动收起为抽屉。
- **实时刷新**：源文件被外部修改时自动重载（保留滚动位置）；编辑模式中会暂停监视，避免覆盖未保存内容。
- **编辑模式（Obsidian 风格 Live Preview）**：标题栏铅笔按钮（或 `Ctrl+E`）进入。文档以**最终渲染效果**呈现；点击段落/标题/表格/代码/公式块后在**原渲染位置无框就地**编辑 Markdown（无白底编辑框、无左右分栏），离开后恢复最终效果。切换块时尽量只更新相关块，减少整页跳动。可插图/内嵌图、删图、插入代码/Mermaid/公式；`Ctrl+S` 保存。真相源始终是完整 Markdown。

## 快捷键

| 快捷键 | 功能 |
| --- | --- |
| `Ctrl+F` / `⌘F` | 搜索（`Enter`/`Shift+Enter` 或 `F3` 上下跳转） |
| `Ctrl+B` / `⌘B` | 折叠/展开目录 |
| `Ctrl+O` / `⌘O` | 打开文件 |
| `Ctrl+E` / `⌘E` | 进入 / 退出编辑模式 |
| `Ctrl+S` / `⌘S` | 编辑模式中保存 |
| `Ctrl+Shift+P` / `⇧⌘P` | 编辑模式中刷新渲染 |
| `Ctrl+滚轮` / `⌘滚轮` | 正文中调整字号；图片 / Mermaid 灯箱中缩放内容 |
| `Ctrl++` / `⌘+` 等 | 灯箱中增量缩放 / 恢复适配；正文中调整 / 重置字号 |
| `Alt+←/→` / `⌘[` `⌘]` | 后退 / 前进 |
| 右键按住拖动 | 灯箱内平移查看（图片与 Mermaid 图示通用） |
| `Esc` | 关闭搜索 / 灯箱 / 退出编辑（有未保存修改时确认）/ 清除双击高亮 |
| 双击标题栏 | 最大化/还原 |

## 目录结构

```
inkwell/
  app.py        宿主：无边框窗口、js_api 桥、文件监视、窗口控制
  host.py       跨平台入口（数据目录 / 打开文件 / 剪贴板 / 窗口后端）
  host_win.py   Windows：Win32 无边框缩放 / Snap / CF_DIB 剪贴板
  host_mac.py   macOS：Cocoa 窗口 / NSPasteboard / Finder 打开文档
  server.py     内置 HTTP 服务器：页面 + 资源 + 图片
  render.py     渲染管线：图片本地化 / LaTeX 保护 / 代码块 / Markdown→HTML
  page.py       组装完整 HTML 文档（外链资源）
  assets/       app.css / app.js / katex（离线）/ pygments-*.css / icon.ico
build.py        PyInstaller 打包（onedir，Windows）
Inkwell.spec    打包配置
gen_pygments.py 生成代码高亮主题
gen_icon.py     生成应用图标（ico / png / icns）
scripts/        Windows 安装脚本；macOS：install_macos.py、macos/launcher.c
tests/          sample.md 测试文档
tools/          serve_only.py / probe*.py 验证脚本
```

## 开发运行（源码态）

```powershell
# Windows：直接运行（需 Python 3.12，并装好 pywebview + markdown + pygments）
python -m inkwell "tests\sample.md"

# 仅起服务器、用浏览器看前端
python tools\serve_only.py "tests\sample.md"   # http://127.0.0.1:8799/
```

```bash
# macOS：安装依赖、生成本机 .app，并注册「打开方式」
python3 scripts/install_macos.py

# 源码态运行
.venv/bin/python -m inkwell tests/sample.md
open Inkwell.app
```

## 构建与安装

```powershell
# 1) 打包成 dist\Inkwell\（onedir，约 1~200MB，含 WebView2/.NET 依赖）
python build.py

# 2) 安装：清理旧版 MarkdownReader/MDReader → 拷到 %LOCALAPPDATA%\Programs\Inkwell
#    → 注册 .md/.markdown 默认打开方式为 Inkwell → 建开始菜单快捷方式
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#  （部分旧版的系统级 HKLM 关联需管理员：右键 PowerShell「以管理员身份运行」再执行）

# 卸载
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
```

## 依赖与环境

- Python 3.12，`pywebview`、`markdown`、`pygments`、`Pillow`；Windows 打包用 `pyinstaller`。
- Windows 运行依赖系统 **WebView2 运行时**（Windows 11 默认自带；安装脚本会检测并提示）。
- macOS 使用系统 **WKWebView**（Safari 引擎），无需 WebView2；配置写在 `~/Library/Application Support/Inkwell/`。
- KaTeX 与 Mermaid 均已离线打包（`assets/katex/`、`assets/mermaid/`），无需联网；Mermaid 只在文档实际包含 Mermaid 围栏时加载。

## 技术说明

- 渲染窗口是原生 WebView（Windows：**WebView2 / Edge Chromium**；macOS：**WKWebView**），非浏览器、非网页版。
- 页面与资源由内置 `http.server` 在回环地址提供，避免 `NavigateToString` 的 ~2MB 上限并让 KaTeX 字体相对路径可解析。
- 启动首帧先显示轻量页面外壳；Markdown/Pygments 加载、base64 解码与首篇文档渲染在后台完成，避免大图片文档阻塞窗口出现。
- 内嵌图片（data URI）在渲染时解码并写入临时目录，再替换为 `/__img__/<name>` 引用，与本地图片走同一套本地化管线；该目录下的响应额外带 `sandbox` CSP，防止 SVG 被当作顶级文档打开时执行脚本。
- 复制净化：拦截 `copy` 事件，克隆选区后拆除所有 `span`（去除 Pygments 着色）、删除 `class` 与彩色内联样式、把渲染公式还原为 `$tex$`，再写入 `text/html` + `text/plain`。
