# SentinelOps 桌面客户端

Windows 10/11 x64，Electron + 现有 Vue 前端。后台独立部署；客户端安装后填写服务根地址，修改地址无需重新打包。原 Web 入口及构建继续保留。

## 使用

0.1.4 同步当前巡检运行轨迹页面：运行中的记录默认显示整轮步骤，支持自动更新、展开工具输入输出与 Python 代码，以及完成后回看。2026-11-05 按用户要求交付源码与桌面单 ZIP，路径为 `artifacts/SentinelOps-deployment-desktop-0.1.4-20261105.zip`；包含后端源码部署目录、Windows x64 解包程序和统一部署说明，不包含本机配置与业务数据。桌面构建和 6 项网络测试通过，新环境配置初始化与 Compose 校验通过；当前环境新旧桌面程序自动启动均返回 `0x80000003`，本次桌面启动联调未通过，目标电脑仍需启动和连接验收。

0.1.3 将桌面内容默认缩放至 90%，窗口控制栏从 32px 增高至 44px，并同步调整标题字号与图标大小。窗口控制栏通过系统提供的区域尺寸占位，随最大化、还原及系统显示缩放调整。顶部消息提示从窗口栏下方开始显示，多条提示保持原有间距；Web 页面比例不变。

桌面左侧导航贴齐窗口左边与底边，与顶部窗口栏使用连续背景，去掉悬浮卡片外框及重复的顶部品牌标识。

0.1.2 的窗口标题栏跟随应用深浅主题，保留原生最小化、最大化和关闭按钮。已在 Windows 解包版上验证两种配色、标题栏热区、最大化与还原，以及本机后台连接。

本机的 0.1.2 安装包通过了完整性校验，但 NSIS 启动仍报临时文件错误。现有安装已备份并通过更新程序文件升级到 0.1.2，原快捷方式可以启动。此次更新沿用旧版卸载注册信息，Windows 应用列表可能仍显示旧版本。

1. 安装 `releases/SentinelOps-0.1.2-x64-setup.exe`。已有旧版时，选择原安装目录覆盖安装。
2. 首次启动填写地址，例如 `https://ops.example.com` 或 `http://192.168.1.100:18088`，点击“测试连接”“保存并连接”。
3. 使用后台已有账号登录。登录页与平台顶部的“服务器连接”可修改地址；切换后台会退出原登录并丢弃未保存内容。

后台入口须提供 `/api/v1/`、`/health/live`、`/health/ready`，并正确转发 SSE。填写服务根地址，不附加 `/api/v1` 或其他子路径。当前本机既有 Nginx 入口为 `http://127.0.0.1:18088`；其他电脑的客户端须填写它能访问到的服务器地址。

地址保存在当前 Windows 用户的 `%APPDATA%/SentinelOps/config/connection.json`。登录令牌沿用会话级存储，配置文件不保存密码或令牌。关闭窗口后客户端退出，后台任务继续运行。

安装包目前未配置发行证书，Windows 可能提示未知发布者。代码签名与发行信誉须使用正式签名材料配置，不能通过取消证书校验或关闭系统防护解决。

## 开发与构建

使用 Node 22.22.1 或兼容版本，以及项目指定的 pnpm 11.19.0。分别安装两个工程的冻结依赖：

```powershell
corepack pnpm@11.19.0 --dir frontend install --frozen-lockfile
corepack pnpm@11.19.0 --dir desktop install --frozen-lockfile
corepack pnpm@11.19.0 --dir desktop exec install-electron
corepack pnpm@11.19.0 --dir desktop dev
```

桌面开发服务使用 `127.0.0.1:5174`；原 Web 开发端口仍是 `5173`。`dev` 自动启动并在客户端关闭时停止其前端开发进程。

```powershell
corepack pnpm@11.19.0 --dir frontend test
corepack pnpm@11.19.0 --dir frontend build
corepack pnpm@11.19.0 --dir desktop test
corepack pnpm@11.19.0 --dir desktop build
```

桌面 UI 输出到 `desktop/dist/`，Web 输出到 `frontend/dist/`。安装包与解包程序位于 `desktop/releases/`。`build:dir` 仅生成解包后的桌面程序，适合本机验收。

NSIS 构建过程中会先生成一个同名的中间程序来提取卸载器。构建失败时，该文件可能留在输出目录。以构建退出码和最终产物为准，构建成功后再分发。2026-11-02 的中间文件为 153,404 字节，已从 `releases/` 移出。次日将打包进程的 `TEMP` 和 `TMP` 临时指向新建的用户临时子目录，重打包成功，最终安装包为 122,438,671 字节。

0.1.0 的主模块在顶层等待 `app.whenReady()`。Electron 等待主模块完成后才触发 `ready`，导致双方互相等待。0.1.1 将窗口初始化放在 `app.whenReady().then(start)` 中，允许主模块先完成。

桌面只向隔离的预加载脚本开放指定能力。渲染页面关闭 Node 集成，启用上下文隔离、进程沙箱及 CSP；网络由主进程转发，只访问当前配置的后台接口，正常校验 HTTPS 证书且不跟随 HTTP 重定向。

## 本机隔离验收

以下命令会创建并迁移 `sentinelops_desktop_test`，生成临时测试账号并启动真实 FastAPI。测试脚本强制要求库名以 `_test` 结尾；账号资料仅写入忽略提交的 `.local/desktop-acceptance/backend.json`。

```powershell
$env:POSTGRES_DB = 'sentinelops_desktop_test'
& '.tools/uv.exe' run --project backend python desktop/tests/backend_fixture.py
```

在另一个终端中执行：

```powershell
$env:SENTINELOPS_SMOKE_FIXTURE = (Resolve-Path '.local/desktop-acceptance/backend.json').Path
corepack pnpm@11.19.0 --dir desktop test
corepack pnpm@11.19.0 --dir frontend build:desktop
corepack pnpm@11.19.0 --dir desktop smoke
```

`SENTINELOPS_SMOKE_FIXTURE` 应改为当前检出目录下对应文件的绝对路径。`smoke` 自动启动本工程的 Electron，使用独立用户目录和本机调试端口 `19222`，验证真实登录、角色权限、令牌刷新、SSE、内部导航与服务切换；截图和结果留在 `.local/desktop-acceptance/`。正常启动不开放该调试端口。

验收打包后的程序时，将 `SENTINELOPS_SMOKE_APP` 设为 `desktop/releases/win-unpacked/SentinelOps.exe` 的绝对路径再运行 `smoke`。`SENTINELOPS_DESKTOP_TEST_DIR` 仅供本机隔离测试覆盖用户目录；正常使用无需设置。

## 选型记录

2026-11-02 首先构建 Tauri 2 原型，原生网络测试与真实后台集成通过。用户手动启动与自动验收均出现闪退；Windows Application 日志记录 `msedgewebview2.exe 156.0.4314.58` 在 `msedge_elf.dll` 发生 `0x80000003`，Tauri 返回 WebView2 初始化错误 `0x8000FFFF`。据此采用原计划中的 Electron 兼容性预案，携带自己的 Chromium 运行时。

仅维护 Electron 桌面工程。原型和诊断资料保留在本机忽略提交的 `.local/desktop-acceptance/tauri-prototype/`，不纳入发行包；未对系统 WebView2 或系统防护进行修改。
