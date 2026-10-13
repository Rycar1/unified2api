<p align="center">
  <img src="images/unified2api-logo.png" alt="Unified2API Logo" width="116">
</p>

<h1 align="center">Unified2API</h1>

<p align="center">
  一个控制台管理多个 AI 平台账号，用统一的 OpenAI 风格 API 调用模型。
</p>

<p align="center">
  TRAE · CodeBuddy 国内 · WorkBuddy AI 国际站 · MonkeyCode · Qoder 国内／国际 · 自定义服务
</p>

## 界面预览

![Unified2API 控制台概览](images/overview.png)

<details>
<summary>查看账号池、智能路由和用量统计</summary>

### 多平台账号池

![TRAE、CodeBuddy 与 WorkBuddy 独立账号池](images/accounts.png)

### 智能路由

![智能路由列表](images/routes.png)

### Token 用量统计

![Token 活动日历与模型分布](images/usage.png)

### 调用记录

![调用记录与诊断信息](images/request-logs.png)

### 自动任务

![签到、余额刷新和 Webhook 设置](images/automation.png)

</details>

## 能做什么

| 功能 | 说明 |
|---|---|
| 多平台账号池 | 集中管理账号；国内 CodeBuddy 与国际 WorkBuddy 分开展示 |
| 统一模型入口 | 通过 `/v1/models` 查看模型，用平台前缀选择上游 |
| 智能路由 | 顺序、轮询、低延迟或自适应选路；结合余额、并发和近期健康情况，在有效输出前故障转移 |
| 自定义服务 | 填写 Base URL 和 API Key，接入 OpenAI 兼容上游 |
| 密钥权限 | 每日／每月 Token 限额、并发、每分钟请求数、模型白名单和暂停开关 |
| 用量与诊断 | 请求 ID、目标与账号、尝试链、首响应时间、结束原因和 Token；不记录对话正文 |
| 模型能力 | 展示协议、工具、图片、上下文和真实压缩能力，支持人工标记与合成请求检测 |
| 自动任务 | 定时签到、刷新余额，并通过 Webhook 接收失败或低余额提醒 |
| 备份恢复 | 导出加密的 `.ubak` 配置备份 |
| 网页升级 | 检查可信仓库版本，构建后切换；健康检查失败自动恢复，也可手动回滚 |

### 完整接入与新控制台

- **模型规格**：展示上下文、最大输出、推理强度选项、上游默认值、价格系数和不可用原因。保留渠道声明与官方规格的来源；未知参数留空。展示默认强度不会向未设置该字段的请求强制添加强度。
- **福利任务**：Qoder 每日活动与 Pro 福利；国内 CodeBuddy 成长任务、连续签到奖励和猫猫旅行；WorkBuddy 国际站完整网页活跃。执行记录与额度发放明细可分别查看。
- **账号保护**：WorkBuddy 国际站支持保留积分、每账号每日 Token／积分上限、每账号每模型每日 Token 上限。设置为 `0` 关闭限制；国内和国际账号池独立。
- **联网工具**：可在设置中开启 WorkBuddy 网页搜索与抓取，由中转执行工具并将结果返回给模型；关闭时保留原工具调用方式。
- **性能与费用**：缓存命中率、首 Token 耗时、生成速度、每日趋势、按模型估算的等价费用。估算费用与平台积分分别显示；没有价格或历史计量字段时显示未知。
- **额度明细**：在账号的更多菜单查看套餐、余额及上游提供的明细；福利页可查询 WorkBuddy 额度发放记录。
- **批量导入**：先预览，再导入；默认跳过已有账号，可显式覆盖。最多 100 条，逐条显示结果，预览不回传凭据。
- **完整备份**：加密备份包含各平台账号、WorkBuddy 解密所需文件、配置和可选用量记录。恢复前检查所有目录，替换失败时跨目录回滚；保留当前版本管理状态，数据库导出使用一致性快照。
- **客户端助手**：支持 Codex、Claude Code、OpenCode、DeepSeek Harness、Crush 和 Zcode。下载配置包后在客户端电脑运行，原配置自动备份并可恢复。Windows 包含独立可执行程序；macOS／Linux 使用 Python 3。
- **React 界面**：参考 ARTEX 和 Rycar 博客的清晰导航、圆角及半透明组件。筛选和列表位移使用可中断动画，适配手机、深色模式和系统减少动态效果设置。

在「自动任务」中可分别开启每日签到和自动领取福利；自动领取福利默认关闭。领奖结果以官方返回和实际额度变化为准，不将“执行完成”当成已发放积分。

WorkBuddy `/v1/messages` 使用格式转换，`/v1/messages/count_tokens` 返回显式标记的估算值。格式转换不等于原生 Codex 远程压缩支持；客户端仍需选择支持对应接口的模型。

模型规格参考：[GLM-5.3-Flash](https://docs.z.ai/guides/vlm/glm-5.3-flash)、[DeepSeek 模型接口](https://api-docs.deepseek.com/zh-cn/api/list-models/)。Messages 的输入统计包含普通输入、缓存写入和缓存读取，遵循 [Claude 缓存计量说明](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)。

### 平台与模型前缀

| 平台 | 添加方式 | 模型前缀 |
|---|---|---|
| TRAE | 网页登录或导入凭据 | `trae/` |
| CodeBuddy 国内 | 网页登录或导入凭据 | `codebuddy/` |
| WorkBuddy AI 国际站 | 官方网页授权；支持多个账号 | `workbuddy/` |
| MonkeyCode | Windows 登录助手或导入凭据 | `monkeycode/` |
| Qoder 国内／国际 | 官方网页授权、PAT 或 JSON；分区多账号池 | `qoder-cn/`、`qoder/` |
| 自定义服务 | Base URL + API Key | 自定前缀 |
| 智能路由 | 在控制台选择目标模型 | `route/` |

实际可用模型取决于账号权限和上游服务，以 `/v1/models` 返回结果为准。

## 快速开始

需要 Docker。Windows 建议使用 Docker Desktop；Linux 和 macOS 使用 Docker Compose。

### Windows

```powershell
git clone https://github.com/Rycar1/unified2api.git
cd unified2api
.\setup.ps1
```

`setup.ps1` 会生成管理密钥与客户端 API 密钥，构建镜像并启动服务。

### Linux / macOS

```bash
git clone https://github.com/Rycar1/unified2api.git
cd unified2api
cp .env.example .env
# 编辑 .env，设置随机的 ADMIN_KEY 和 UNIFIED_API_KEY
docker compose up -d --build
```

打开 **[管理控制台](http://localhost:8080/admin/)**，使用 `.env` 中的 `ADMIN_KEY` 登录。客户端使用 `UNIFIED_API_KEY` 调用 API。

| 地址 | 用途 |
|---|---|
| `http://localhost:8080/admin/` | 管理控制台 |
| `http://localhost:8080/v1` | API Base URL |
| `http://localhost:8080/healthz` | 健康检查 |

默认只绑定本机 `127.0.0.1`；`8787`、`7864`、`18080` 是兼容端口，指向同一个服务。

## 添加账号与调用模型

在控制台的「账号」页选择平台并完成登录。TRAE 与 CodeBuddy 可以导入平台支持的凭据；MonkeyCode 可下载 Windows 登录助手。WorkBuddy AI 国际站使用官方网页授权，可连续添加多个账号，在账号池中分别查询余额、刷新、停用或删除。调用 `workbuddy/` 模型时由国际站账号池自动选择账号，无需创建自定义服务。账号保存在独立的 Docker 数据卷中，无需启动桌面端。

WorkBuddy 国际站的「每日活跃」创建新的简短网页活动会话，通过 ACP 驱动该轮直至完成，再刷新余额。原账号池巡检、Unified 的「全部签到」和「每日自动签到」均复用这条链路，并跳过当天已完成的活动。中转不查询已有用户会话；活动只保存状态和计数，奖励是否发放以余额明细为准。

新建客户端密钥后，可以用任何支持 OpenAI Chat Completions 的客户端连接：

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer YOUR_UNIFIED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "trae/MODEL_NAME",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

先查询当前可用模型，替换示例中的 `MODEL_NAME`：

```bash
curl http://localhost:8080/v1/models \
  -H "Authorization: Bearer YOUR_UNIFIED_API_KEY"
```

### 自定义服务与智能路由

自定义服务需要服务前缀、Base URL、上游 API Key 和模型列表。例如前缀为 `myapi`，上游模型为 `org/model`，调用时使用 `myapi/org/model`。

智能路由把多个模型放在一个稳定别名下。例如创建 `code-stable` 路由后，客户端调用 `route/code-stable`。可选择顺序优先、轮询、低延迟或「自适应：余额、并发与健康」策略。

自适应策略跳过已确认额度耗尽、冷却和并发已满的目标，结合近期成功率和首响应耗时分配请求。未知余额仍可尝试，各平台积分不会直接跨平台比较。可设置每个目标与每个账号的并发上限；请求结束或取消后释放占用。故障转移仅发生在有效内容或工具调用输出之前，已经输出的请求不会切换上游。

### 密钥限额与请求诊断

在「API 密钥」中点击密钥旁的三个点，选择「用量限制」。每日／每月 Token、并发和每分钟请求数设为 `0` 表示不限，模型白名单留空表示允许全部；也可暂停密钥。限制检查客户端提交的完整模型 ID，允许一个路由别名也就允许其配置的目标。

调用前预占输入估算与输出上限，完成后按上游用量结算；重试中上游明确报告的用量也会累计。上游未提供用量且已经输出时，保守扣除预占数并标为估算。每日和每月按北京时间划分，清空日志或重启服务不会重置已消耗限额。估算会留出余量，接近限额时可降低请求的输出上限。

每个 API 请求返回 `X-Request-ID`。在「调用记录」搜索模型或请求 ID，再点击「诊断」，可查看实际目标、账号、尝试链、HTTP 状态、首响应时间和结束原因。HTTP 200 中的流内错误或缺失完成标记会标为失败。历史记录保留原统计字段，新增诊断字段从升级后开始采集。

### 模型能力与 Codex 压缩

在「模型 → 能力」查看或设置工具、图片和上下文长度。「检测能力」只发送少量合成请求，可能消耗上游额度；不能确认的项目保留为未知。目录、检测和人工标记分别保留来源，支持恢复自动结果。

Responses 原生、格式转换与 Codex 远程压缩分别标记。内置平台不会因人工标签而获得未实现的协议。自定义服务的 `/v1/responses/compact` 直接转发上游，只有真实返回一个 `compaction` 项的检测才标记支持。路由压缩只选已确认支持的目标；DeepSeek 等格式转换渠道不会生成假的压缩项。

### Qoder

在「账号 → 添加账号」选择 **Qoder**，打开官方登录页完成授权，账号会自动保存到独立账号池。重复登录可添加其他账号；调用时轮换使用可用账号，连接建立前失败会尝试其他账号。

在添加账号时选择国内版或国际版。也可粘贴官方网页「设置 → 个人访问令牌」中创建的 PAT（`pt-` 开头），或导入旧版 `auth_user_info_raw` JSON、Qoder2API-Hub 的 `uid` / `accessToken` / `refreshToken` JSON。凭据只保存到持久化管理目录，并纳入加密备份。

Qoder 接入复用 [Qoder2API-Hub](https://github.com/shuishuipingan/qoder2api-hub) 的 Python 模块，仍在同一个容器中运行。国内账号与国际账号、模型目录及登录参数分别管理：国内模型使用 `qoder-cn/`，国际模型使用 `qoder/`。模型 ID 使用易读名称，例如 `qoder/qwen3.8-flash` 和 `qoder-cn/qwen3.8-flash`，控制台统一显示一行；旧的 `qoder/qfmodel` 等上游键名仍可调用。旧账号首次启动自动迁移，保留 ID 和备注，并在数据目录留存迁移前备份。登录或点击「刷新」后读取官方模型目录；首次目录查询完成前使用上游项目的分区快照，并标注目录来源。额度统计订阅、加油包及有效专属资源包；未知显示「—」，明确为零显示 `0`。调用资格以模型的实际响应为准。

支持 Chat、Responses 和 Messages 格式转换。Responses 并非上游原生 OpenAI 接口，也不提供 Codex 的远程压缩能力。流内错误会立即回显；缺少完成标记的中断不会被报告为成功。

Responses 的 `previous_response_id` 续接信息只在内存保留，最多 1 小时，并限制缓存容量；重启或过期后请提供完整 `input`。

支持网页登录、PAT 交换、凭据自动续期和手动签到。「自动任务」中开启每日自动签到后，也会执行 Qoder 每日积分活动；未开启时不会自动领取。国际版签到复用上游官方活动检测与领取逻辑。Docker 构建会从官方 `@qoder-ai/qodercli@1.1.65` 提取 UMID 原生组件，校验固定 SHA-512 和目标架构后打入镜像，不需要另起容器或安装桌面端；组件提取或校验失败会中止构建。活动请求使用该组件生成的真实机器身份，运行时需要联网。

账号行的「签到」、账号页的「全部签到」和「自动任务」的每日签到均覆盖 Qoder 国内／国际账号。返回实际 `earned_credit` 和最新余额；已领取、活动关闭、资格不足或同一设备／身份已领取会显示官方结果，不把跳过计为新获得的积分。同设备多账号是否能领取由官方规则决定。

续期失败且登录过期后，需要重新授权。接入不会启动上游独立控制台、读取本机其他客户端凭据或另存对话正文。

模型页支持按前缀分类、数量显示和分类内搜索，Qoder 国内／国际、自定义服务与智能路由分别归类。

### 协议支持

| 模型前缀 | Chat Completions | Responses | Messages |
|---|:---:|:---:|:---:|
| `trae/` | ✓ | — | — |
| `codebuddy/` | ✓ | ✓ | ✓ |
| `workbuddy/` | ✓ | ✓ | — |
| `monkeycode/` | 文本对话 | — | — |
| `qoder/` | ✓ | 格式兼容 | 格式兼容 |
| `qoder-cn/` | ✓ | 格式兼容 | 格式兼容 |
| 自定义前缀 | 视上游而定 | 视上游而定 | — |
| `route/` | 视目标而定 | 视目标而定 | 视目标而定 |

## 统计、自动任务与备份

调用记录保存模型、实际路由目标、HTTP 状态、耗时和 Token 数量，**不保存提示词或回复正文**。控制台展示按日、按周和累计用量；早于启用记录功能的请求无法回填。

自动任务支持每日签到及按间隔刷新账号状态和余额。Webhook 通知不包含凭据。单个账号签到或查询失败不会中断其他账号；调用测试会显示已过滤敏感信息的上游错误。

控制台导出的 `.ubak` 文件使用密码派生密钥与 AES-GCM 加密，可恢复内置账号和配置。**WorkBuddy 国际站账号位于独立数据卷，迁移时还需单独保留该数据卷。**

| Docker 数据卷 | 内容 |
|---|---|
| `unified2api_trae-auth` | TRAE 凭据 |
| `unified2api_trae-data` | TRAE 账号池状态 |
| `unified2api_buddy-data` | CodeBuddy、MonkeyCode、路由、记录和客户端密钥 |
| `unified2api_hub-accounts` | WorkBuddy 国际站账号 |
| `unified2api_hub-usage` | WorkBuddy 国际站账号池用量 |

停止服务时不要使用 `docker compose down -v`，该命令会删除数据卷。

## 网页升级与回滚

「版本管理」也可从「设置」进入。默认检查 `Rycar1/unified2api` 的 `main` 分支；点击检查更新后，确认一个固定提交再升级。首次启用需要使用新版 Dockerfile 和 Compose 配置构建启动，使容器具有网关标记、健康检查和本机 Docker Socket 挂载：

```bash
docker compose up -d --build
```

版本操作仅管理员可用，写操作校验 CSRF。Docker Socket 让网关具备管理本机容器的能力，因此管理密钥应只交给管理员；此控制接口不会新增公网监听端口。

升级先从配置的可信仓库下载固定提交并构建，再保留原端口、运行环境和数据卷切换容器。旧容器停机保留用于回滚，新容器健康检查失败会恢复旧容器。升级任务由临时 Python 容器执行，网关重启不会中止任务；平时仍只有一个运行中的网关容器。完成后可能需要重新登录控制台。

「回滚上一版本」恢复程序版本并保留当前数据卷，不会把账号、日志和配置恢复成旧快照。涉及不兼容数据迁移的版本应先导出备份。升级源必须包含管理升级模块，缺少该模块的旧版提交不会作为升级目标。

## 安全与部署

- `.env`、Cookie、账号导出文件和真实 API Key 不应提交到 GitHub。
- 管理会话使用 HttpOnly Cookie，写操作需要 CSRF Token；上游 Key 不会在管理列表中明文回显。
- 默认只运行一个 Docker 容器。WorkBuddy 国际站的 Python 账号池由主程序管理，在容器内监听回环地址，不向公网暴露端口。沿用原有数据卷，升级无需重新添加账号。
- 控制台、智能路由、CodeBuddy、WorkBuddy 和 Qoder 使用 Python；TRAE 和 MonkeyCode 暂保留进程内的 Go 桥接库。
- 对外部署时使用 HTTPS、设置 `SECURE_COOKIE=true`，并限制管理端的访问来源。

本项目用于个人账号与本地服务管理。请遵守对应平台的服务条款。

## 开发与维护

控制台源码位于 `ui/`，使用 React、Vite 和 Lucide；构建结果写入 `unified/static/`。运行已构建的服务不需要 Node.js。

```bash
cd ui
pnpm install
pnpm run build
```

查看服务状态：

```bash
docker compose ps
docker compose logs --tail 100
```

## 上游项目

本仓库整合并保留以下项目的源码快照与许可证：

- [JeffHu0912/trae2api](https://github.com/JeffHu0912/trae2api)
- [ShouZhuo0413/codebuddy2api](https://github.com/ShouZhuo0413/codebuddy2api)
- [ZFXing-lite/monkeycode2api](https://github.com/ZFXing-lite/monkeycode2api)
- [ardeyouxipianyi/workbuddy2api-hub](https://github.com/ardeyouxipianyi/workbuddy2api-hub)
- [shuishuipingan/qoder2api-hub](https://github.com/shuishuipingan/qoder2api-hub)（MIT；Qoder 国内／国际接入）

各组件继续适用其原许可证，详见 `vendor/`。
