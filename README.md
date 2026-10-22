# 抖音智能体 v0.1

一个本地单账号的抖音操作台：用专用 Chrome 读取推荐流，模型判断是否点赞、评论、私信或分享，再由策略引擎决定能不能执行。

> 仅用于操作自己的账号。低频、预演优先；不绕过验证码，不做反检测，不批量私信/评论。

## 功能

- **推荐流读取**：标题、描述、作者、点赞/评论数、可见评论；直播或无作品 ID 的卡片跳过。
- **自动回复**：服务启动后常驻检查已有 1 对 1 会话，最后一条是对方发的且未处理过就回复；已读未回也会处理，群聊/系统会话跳过。
- **评论回复**：检查“评论”通知，打开对应作品定位那条评论，点回复并用模型生成短回复；只处理自己作品下的评论，受策略和 dry-run 控制，已处理过的不重复。
- **定时分享**：每天两个时间点扫描推荐流，按兴趣和封面挑视频；先发介绍，再用抖音原生“分享 → 私信”发视频卡片给指定接收账号。
- **私信点播**：私信里有人要视频时，走顶部搜索框的“视频”结果，按关键词 + 封面挑**一条**，先发介绍再原生分享给当前请求者；同一个问题重复问会排除之前分享过的视频。
- **评论 @ 接收账号**：评论开头 @ 接收账号，会尝试从抖音 @ 下拉列表真实选中联系人；选不到时退化为纯文本 @。
- **粉丝回关/取关**：运行台读取自己的粉丝头像、昵称、简介和互关状态；可手动回关，已互关可二次确认取关。只支持单个手动操作，不批量。
- **浮动私信面板**：运行台右下角的消息球显示未读数；打开后可查看会话列表、双方消息（含 AI 回复）、图片和视频，并在 1 对 1 会话里手动发送文本。未读提示会保留到你在面板里查看对应会话。
- **封面视觉判断**：下载视频封面随文案一起交给支持视觉的模型（MiniMax-M3 / DeepSeek Flash）。
- **可审计**：SQLite 记录每次动作的策略原因、结果和截图；SSE 实时推送运行状态。
- **自动快照**：每次开始一轮会静默保存 DOM 和截图，便于页面改版后校准选择器。
- **角色设定**：设置页第一个卡片可设置智能体角色名字和人设；角色名字会写进模型提示词，作为它的自我称呼。
- **静默浏览器**：登录后在 Windows 上离屏启动并隐藏专用 Chrome；需要扫码/验证时才显示窗口。

## 技术栈

- 后端：FastAPI + Uvicorn + Playwright + SQLite
- 前端：React 19 + Vite + TypeScript
- 模型：OpenAI 兼容接口（MiniMax Coding Plan / DeepSeek）
- 运行环境：Windows 优先（静默窗口使用 Win32 API），Python 3.12

## 架构

```text
React/Vite 控制台
      │  HTTP + SSE
      ▼
FastAPI ─────────────── SQLite (data/agent.db)
      ├── Playwright ── 专用 Chrome ── douyin.com
      └── OpenAI-compatible API ── MiniMax / DeepSeek
```

## 环境要求

| 组件 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11（其他系统需要额外处理有头浏览器和窗口隐藏） |
| Python | 3.12 |
| uv | Python 包/虚拟环境管理 |
| Node.js | 20+，仅构建前端时需要 |
| Google Chrome | 专用登录态和页面操作使用 |
| Git | 克隆仓库需要 |

Windows 可以用 winget 安装：

```powershell
winget install Git.Git
winget install astral-sh.uv
winget install OpenJS.NodeJS.LTS
winget install Google.Chrome
```

装完重开终端，确认版本：

```bash
git --version
uv --version
python --version
node --version
npm --version
```

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/DavidBlon/douyin-agent.git
cd douyin-agent
```

### 2. 安装后端依赖

```bash
uv sync
```

`uv` 会自动创建 `.venv`，不需要手动激活。

### 3. 构建前端

```bash
cd web
npm install
npm run build
cd ..
```

后端会直接托管 `web/dist`，生产运行不需要常驻 Vite。改了前端源码后需要重新执行这三步。

### 4. 可选配置

最简单的方式是启动后在设置页里填 API Key，不需要 `.env`。也可以复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

然后在 `.env` 里填：

- `DEEPSEEK_API_KEY=`：模型 API Key
- `CHROME_CHANNEL=chrome`：默认使用系统 Google Chrome
- `HEADLESS=false`：扫码登录需要有界面，保持 false

如果机器没有 Google Chrome，可以安装 Playwright 的 Chromium，并把 `CHROME_CHANNEL` 留空：

```bash
uv run playwright install chromium
```

### 5. 启动

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8787
```

或者 Windows 直接双击 `start.bat`。它调用 `scripts/start.ps1`，在 `web/dist/index.html` 不存在时会自动构建前端，然后启动服务。

打开 <http://127.0.0.1:8787>。

### 6. 首次登录和试跑

1. 设置 → 模型：填 API Key，点“测试模型”。
2. 设置 → 账号与浏览器：点“登录浏览器”，用手机扫码登录抖音。
3. 设置 → 动作边界：先打开预演模式，额度调低。
4. 运行台点“开始一轮”，看动作记录里的判断和草稿；确认后再关闭预演。

具体配置见下方「第一次使用」。

### 常见启动问题

- **`uv` 找不到**：安装后重开终端，或把 `%USERPROFILE%\.local\bin` 加入 PATH。
- **`npm` 找不到**：安装 Node.js 20+ 后重开终端。
- **Chrome 启动失败**：安装 Google Chrome，或执行 `uv run playwright install chromium` 并清空 `CHROME_CHANNEL`。
- **登录二维码看不到**：确认 `HEADLESS=false`，并通过设置页“登录浏览器”打开；Windows 服务方式运行时必须能在当前用户桌面/RDP 会话中显示窗口。
- **8787 被占用**：停掉旧进程，或换一个端口启动。
- **页面是旧的**：重新执行前端构建步骤，再刷新浏览器。
- **更新代码**：

```bash
git pull
uv sync
cd web
npm install
npm run build
cd ..
```

开发模式（Vite 热更新 + 后端）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
```

## 第一次使用

1. **设置 → 智能体**：填角色名字（可选），以及人设与说话方式；角色名字会进入模型提示词。
2. **设置 → 模型**：填 API Key，点“测试模型”。
   - MiniMax Coding Plan：Key 必须是以 `sk-cp-` 开头的订阅 Key；Base URL 用 `https://api.minimax.cn/v1`，模型 `MiniMax-M3.1-Flash` 或 `MiniMax-M3`。
   - DeepSeek：模型 `deepseek-flash`；Base URL 用 `https://api.deepseek.com/v1`；Key 用 DeepSeek 平台的 `sk-…`。
3. **设置 → 账号与浏览器**：点“登录浏览器”，在专用 Chrome 里用手机扫码登录抖音。登录态只落到 `data/chrome-profile`。
4. **设置 → 动作边界**：先打开预演模式，额度调低，跑一轮看动作记录里的判断和草稿。
5. **运行台**：点“开始一轮”。预演只读取、判断和记录，不点击发送。
6. 确认选择器和文案都合理后，再关闭预演，逐步放开评论、分享和主动私信。

## 使用要点

### 自动回复

- 服务启动后按间隔常驻检查 1 对 1 会话，不需要点“开始一轮”。
- 自动回复 `reply_dm` 不设次数上限，也不占“主动私信”额度。
- 主动私信额度只限制视频扫描中由模型主动发给作者、且通过白名单/互关检查的私信。
- 评论回复已接通：通知 → 对应作品 → 定位评论 → 回复并发送；受 `dry_run`、敏感词/链接/长度策略和去重控制。运行台的实时动作会显示本轮私信跳过原因，只有原因变化时才提示，避免每 20 秒刷屏。

### 分享

- 定时分享有两个手动时间点，到点扫描推荐流；先发视频介绍，再发原生视频卡片。
- 私信点播走顶部搜索框，一次只分享一条；同一个问题会换一条，不会重复分享同一视频。
- 搜索触发人机验证时停止，不绕过。

### 粉丝

- 运行台可读取自己的粉丝头像、昵称、简介和互关状态。
- 回关是逐个手动操作；取关需要点两次确认，防误触。
- 不做批量关注/取关。

## 数据与隐私

运行数据全部在本机：

- `data/agent.db`：设置、动作记录、分享历史
- `data/secrets.json`：API Key（设置页不回显）
- `data/chrome-profile`：抖音登录态
- `data/debug/`、`data/media/shots/`：DOM 快照和截图

`data/`、`.env`、`.venv/`、`web/node_modules/`、`web/dist/`、`preview-ui/` 都已加入 `.gitignore`，不会上传到 GitHub。

发给模型服务的只有视频标题、描述、评论文本和封面图片；不会发送 Cookie 或登录态。

HTTP 服务默认只绑定 `127.0.0.1`，没有内置登录鉴权。不要把 8787 直接暴露到公网；远程访问请使用 Tailscale/SSH 隧道，或在 Nginx 后面增加 HTTPS 和认证。

## 安全边界

- 默认预演，必须手动关闭后才会真实发送。
- 评论、主动私信有每日额度和同视频去重；同一句评论不复用。
- 主动私信只对白名单或互关账号；互关会打开作者主页核对「互相关注」。
- 链接、手机号、受限词会被拦截。
- 页面出现验证码/风控提示立即停止，不重试、不绕过。
- 单账号运行，不做多账号、陌生人批量私信或评论刷量。

## 已知限制

- 抖音页面改版可能导致选择器失效；可通过每次运行自动保存的 DOM 快照校准 `app/selectors.py`。
- 搜索可能触发人机验证，程序会停止，需要人工处理。
- 评论回复只处理自己作品下的评论；通知找不到对应作品、评论已手动处理或模型判断不需要回复时会跳过。同一作者在同一作品下的相同评论文字只回复一次。
- 私信视频使用抖音播放器的临时直链；链接过期或浏览器不支持视频编码时，面板会保留封面并提供“在抖音打开”入口。
- 发布和语音转写（ASR）尚未实现。
- 云服务器 IP 更容易触发抖音验证；Windows 本机/小主机通常更稳。
- Linux 可以跑，但现有静默窗口逻辑是 Windows 专用的；需要 Xvfb/VNC 或有头 Chrome，扫码也要通过远程桌面完成。

## 测试

```bash
uv run pytest -q
```

测试覆盖策略引擎、mock 页面读取/点赞/评论/下滑、自动回复范围/去重和一轮 agent 预演。mock 只能验证代码逻辑，不能保证抖音真实页面当天的选择器可用。

## 项目结构

```text
app/
  main.py       FastAPI 路由、SSE、静态页面
  agent.py      一轮运行循环（读取 → 判断 → 策略 → 执行 → 记录）
  browser.py    Playwright 持久化 Chrome、读取与页面动作
  policy.py     额度/去重/白名单/内容检查
  prompts.py    模型提示词
  db.py         SQLite
  selectors.py  抖音页面选择器（优先改这里）
web/            React + Vite 控制台
tests/          策略、mock 浏览器、agent 预演
scripts/        启动/开发/窗口控制脚本
data/           本机运行数据（不进入 git）
```

## 部署提示

- 最省事：Windows VPS 或家里常开的 Windows 小主机 + NSSM 注册服务；扫码/验证时 RDP 进桌面。
- Linux：需要 Xvfb + VNC/noVNC，或者改 headless；并接受更高的验证码概率。
- 迁移时带走 `data/agent.db`、`data/secrets.json`、`data/chrome-profile`；不要两个实例同时使用同一个 Chrome 配置目录。
- 对外访问务必加认证和 HTTPS，不要直接开放 8787。

## 免责声明

本项目用于个人账号的本地自动化实验。平台条款、账号风险和内容责任由使用者承担；请遵守平台规则，低频使用，先预演再放开。

## License

暂未附带开源许可证，默认保留所有权利。如需开源，请补充 `LICENSE`。
