<p align="center">
  <img src="images/unified2api-logo.png" alt="Unified2API Logo" width="116">
</p>

<h1 align="center">Unified2API</h1>

<p align="center">
  一个控制台管理多个 AI 平台账号，用统一的 OpenAI 风格 API 调用模型。
</p>

<p align="center">
  TRAE · CodeBuddy 国内 · WorkBuddy AI 国际站 · MonkeyCode · Qoder 国内／国际 · Antigravity · 自定义服务
</p>

<p align="center">
  <a href="#界面预览">界面预览</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#平台与模型前缀">平台接入</a> ·
  <a href="#协议支持">协议支持</a> ·
  <a href="#网页升级与回滚">升级与回滚</a>
</p>

## 界面预览

截图来自当前 React 控制台，由 Playwright 截取。账号、余额、用量和记录均为演示数据，不包含真实凭据或对话内容。

### 概览

一屏查看近 7 天 Token 趋势、各平台账号可用率、正在处理的请求，以及最近自动任务。

![Unified2API 控制台概览](images/overview.png)

<details>
<summary><strong>深色模式</strong> · 跟随系统或手动切换</summary>

顶部一键切换浅色／深色，也可在设置中选择跟随系统。主题自动记忆，支持从点击位置圆形扩散的切换动画。

![Unified2API 深色控制台](images/overview-dark.png)

</details>

<details>
<summary><strong>账号与模型</strong> · 独立账号池、模型前缀分类与快速搜索</summary>

### 多平台账号池

国内 CodeBuddy 与国际 WorkBuddy、Qoder 国内与国际分别展示。账号行提供签到／每日活跃、刷新和余额查询；更多菜单支持额度明细、停用与删除。实际支持的操作取决于平台。

![多平台账号池与账号操作](images/accounts.png)

### 模型目录

按 `trae/`、`codebuddy/`、`workbuddy/`、`qoder-cn/`、`qoder/` 等前缀分类，输入关键词快速筛选。能力标签区分协议支持、格式转换与工具调用；详情可查看模型规格和来源。

![按前缀分类的模型目录](images/models.png)

</details>

<details>
<summary><strong>调用与运维</strong> · 智能路由、请求诊断与自动任务</summary>

### 智能路由

账号列表的「⋯ → 测试调用」可选择模型并锁定当前账号发送测试，支持 TRAE、CodeBuddy 国内、WorkBuddy 国际、MonkeyCode、Qoder 国内/国际和 Antigravity。失败不会切换其他账号；结果显示 HTTP 状态、耗时、实际账号确认和错误详情。测试遵守账号与模型冷却规则，会消耗少量额度；路由返回 429 不代表所有账号受限，可用此功能逐个确认。

CodeBuddy 国内与 WorkBuddy 国际遇到账号 429 时，会在同一平台尝试其他可用账号，每个账号在该请求中最多尝试一次，不占智能路由的跨渠道重试次数。限流按账号与模型记录，遵守上游明确的恢复时间，国内 CodeBuddy 同时支持 `Retry-After`；缺少恢复时间时暂缓 60 秒。有余额的限流账号还会降低优先级 30 分钟，期限结束或成功调用后恢复。已输出正文或工具调用的流不会换号重放。路由预览可查看各账号的模型冷却原因与恢复时间。

WorkBuddy 也会跳过余额保留线、每日用量及模型每日上限触发的账号。内部账号池明确拒绝某个账号时，普通调用在输出前继续尝试其他账号；单账号测试仍保持锁定。诊断保留目标账号与固定的拒绝原因，不把这些本地限制归为官方服务故障，也不额外增加限流冷却。

用一个 `route/` 别名连接多个目标，可选择顺序、轮询、低延迟或自适应策略，并设置并发限制。

所有策略都会跳过已确认无可用账号和冷却中的渠道，先筛选接口兼容性，再计算重试次数。已保存的目标不会因额度不足而从模型目录暂时消失就被删除或拦截；额度刷新恢复、冷却到期后会自动重新参与。上游明确返回模型不存在或额度不足时会暂时冷却该目标，普通请求参数错误不会禁用整个模型。

客户端取消或连接未完整结束不会触发整个渠道冷却。限流等故障仅使渠道短暂退避（最多 10 秒），账号自身的额度、凭据和限流保护继续由账号池处理；明确的模型不存在仍按路由配置冷却。没有可用目标时，错误会列出冷却、并发或账号池不可用等实际原因。

点击「预览」查看当前目标顺序、候选账号状态、冷却时间及跳过原因；预览不发送模型请求，也不改变轮询顺序。页面下方可设置首个有效响应等待与流式空闲超时，下次请求生效。已经输出内容的请求不会自动重放。

### 自定义服务账号池

#### OpenCode Zen 轻量直连

在「自定义服务 → 添加服务」中选择 **OpenCode Zen · 轻量直连**，点击「读取模型列表」后保存。无需填写 Base URL；不填官网 Key 可使用已开放的免 Key 模型。既有 `opencode-runtime/` 模型前缀保持兼容，例如 `opencode-runtime/glm-5.3-flash-free`。

可选填写 **OpenCode 官网 API Key** 并点击「保存官网 Key」，再读取模型列表。所有内置 OpenCode 服务共用这份凭据；可替换或清除，清除后恢复免 Key 模式。Key 使用 AES-GCM 加密保存，管理接口仅返回是否配置，不返回原文。直连模式的更新对后续请求生效，不中断已有流。填入 Key 不代表所有模型免费，费用和权限以官网为准。仅对接 Chat 上游模型；原生 Responses、Anthropic 和 System One 专用模型可通过普通自定义服务接入。

默认使用参考 [12errh/zen-proxy](https://github.com/12errh/zen-proxy) 的 Python 直连适配，无须启动 Node 或 OpenCode 子进程。上游统一使用流式请求，由现有 Python 转换层提供 Chat Completions、Responses（含 `previous_response_id` 续接）和 Messages 的流式／非流式响应；不支持 Codex 原生远程压缩。指定模型失败会回显真实错误，不自动替换成其他模型。

适配器补充会话头和上游工具定义，所有工具调用均由客户端执行，服务器不执行内置文件或命令工具。收到客户端未声明的工具时返回明确错误，不静默丢弃。Responses 续接按 API Key、服务和模型隔离，内存缓存最多 256 条／16 MiB，有效期一小时，服务重启后失效。全局代理在每次新请求时应用。

原 [TiaraBasori/OpenCode2API](https://github.com/TiaraBasori/OpenCode2API) 2.5.0 与 OpenCode 1.19.0 运行时保留，可设置 `OPENCODE_BACKEND=runtime` 后重启服务切回，默认值为 `zen`。模型目录、地区限制与免费活动随上游调整，能否调用以真实推理响应为准，不把目录可见或 HTTP 健康检查当作模型调用成功。直连模式优先保留上游返回的 Token 用量。普通自定义服务的「启用 OpenCode 支持」开关仅负责会话头兼容，与此方式分别配置。

#### 通用服务

每个服务可添加多个 Key，保持现有 `服务前缀/模型名` 不变。点击服务行的「账号池」添加、编辑、停用或删除账号，设置账号并发；请求按账号轮换，失败账号冷却 60 秒后重新参与。

余额查询支持 DeepSeek、NewAPI 和 Sub2API。DeepSeek 与 Sub2API 使用账号 Key；NewAPI 需要独立的控制台 Access Token，可配置用户 ID。查询失败保留上次余额，界面标明更新时间和上游单位，不把原始 quota 强行换算为金额。

![智能路由列表](images/routes.png)

### 调用记录

显示实际模型、状态、首响应、总耗时和 Token。点击「诊断」查看请求 ID、尝试链及结束原因，不保存提示词或回复正文。

![调用记录与诊断信息](images/request-logs.png)

### 自动任务

每日签到、福利领取和余额刷新可独立开启，支持 Webhook 失败与低余额通知，并保留执行结果。

可设置最多 10 条签到计划，每条选择执行时间和平台；统一按北京时间（UTC+8）调度。支持账号单独设置 5–1440 分钟的余额查询间隔，单独设置的账号不会被默认刷新重复查询。执行状态持久化，重启后延续进度；当天错过的签到补执行，失败签到退避重试，当天最多尝试 3 次。页面显示下次执行、上次结果与失败原因；首次安装默认关闭自动签到，已有设置沿用。

![每日签到、福利领取与余额刷新设置](images/automation.png)

</details>

<details>
<summary><strong>用量与福利</strong> · Token 热力图、任务进度、执行时间线与额度圆环</summary>

### Token 用量统计

查看今日、近 7 天、近 30 天和累计用量；活动热力图、日／周／累计趋势与模型占比圆环一起呈现。Token 数量不足 1 亿使用 `M`，达到 1 亿使用「亿」，悬停可查看精确数值。

![Token 活动热力图、趋势与模型占比](images/usage.png)

### 福利任务

按账号查看签到、成长任务和平台福利，用进度条展示完成情况；只展示任务、奖励、条件和进度等相关信息。

![账号福利与任务进度](images/benefits.png)

### 执行记录

时间线展示执行时间、账号、任务、结果与已确认奖励，支持搜索、状态筛选和分页。

![福利任务执行时间线](images/benefit-history.png)

### 额度发放

汇总 WorkBuddy 国际站额度包，用圆环展示整体使用比例，并逐包显示已用、剩余与有效期。内部编号、技术字段、空值和重复信息不会铺满页面。

![额度使用圆环与额度包明细](images/credit-grants.png)

</details>

## 能做什么

| 功能 | 说明 |
|---|---|
| 多平台账号池 | 集中管理账号；国内 CodeBuddy 与国际 WorkBuddy 分开展示 |
| 统一模型入口 | 通过 `/v1/models` 查看模型，用平台前缀选择上游 |
| 智能路由 | 顺序、轮询、低延迟或自适应选路；结合余额、并发和近期健康情况，在有效输出前故障转移 |
| 自定义服务 | 填写 Base URL 和 API Key，接入 OpenAI 兼容上游 |
| 密钥权限 | 每日／每月 Token 限额、并发、每分钟请求数、模型白名单和暂停开关 |
| 用量与诊断 | Token 热力图、趋势与模型占比；请求 ID、目标与账号、尝试链、首响应和结束原因；不记录对话正文 |
| 模型能力 | 展示协议、工具、图片、上下文和真实压缩能力，支持人工标记与合成请求检测 |
| 福利与自动任务 | 手动领取平台福利、查看执行时间线与额度包；定时签到、领取福利、刷新余额及 Webhook 提醒 |
| 备份恢复 | 加密 `.ubak` 备份包含各平台账号与配置，可选包含调用记录和 WorkBuddy 用量 |
| 外观与交互 | 浅色／深色／跟随系统、圆形扩散切换、可中断筛选动画与移动端导航 |
| 网页升级 | 检查可信仓库版本，构建后切换；健康检查失败自动恢复，也可手动回滚 |

网页升级会显示已下载的源码大小与重试次数；源码下载最多尝试 3 次，总时限为 3 分钟。超时或失败会显示原因，保留当前服务。刷新或重新打开版本管理页面后，进度会继续更新。

### 接入与管理

- **模型规格**：展示上下文、最大输出、推理强度选项、上游默认值、价格系数和不可用原因。保留渠道声明与官方规格的来源；未知参数留空。展示默认强度不会向未设置该字段的请求强制添加强度。
- **平台福利**：Qoder 每日活动与 Pro 福利；国内 CodeBuddy 成长任务、连续签到奖励和猫猫旅行；WorkBuddy 国际站网页活跃。奖励以官方返回和实际额度变化为准。
- **账号保护**：WorkBuddy 国际站支持保留积分、每账号每日 Token／积分上限、每账号每模型每日 Token 上限。设置为 `0` 关闭限制；国内和国际账号池独立。
- **联网工具**：可在设置中开启 WorkBuddy 网页搜索与抓取，由中转执行工具并将结果返回给模型；关闭时保留原工具调用方式。
- **性能与费用**：缓存命中率、首 Token 耗时、生成速度、每日趋势、按模型估算的等价费用。估算费用与平台积分分别显示；没有价格或历史计量字段时显示未知。
- **批量导入**：先预览，再导入；默认跳过已有账号，可显式覆盖。最多 100 条，逐条显示结果，预览不回传凭据。
- **完整备份**：加密备份包含各平台账号、WorkBuddy 解密所需文件、配置和可选用量记录。恢复前检查所有目录，替换失败时跨目录回滚；保留当前版本管理状态，数据库导出使用一致性快照。
- **客户端助手**：支持 Codex、Claude Code、OpenCode、DeepSeek Harness、Crush 和 Zcode。下载配置包后在客户端电脑运行，原配置自动备份并可恢复。Windows 包含独立可执行程序；macOS／Linux 使用 Python 3。

在「自动任务」中可分别开启每日签到和自动领取福利；自动领取福利默认关闭。领奖结果以官方返回和实际额度变化为准，不将“执行完成”当成已发放积分。

WorkBuddy `/v1/messages` 使用格式转换，`/v1/messages/count_tokens` 返回显式标记的估算值。格式转换不等于原生 Codex 远程压缩支持；客户端仍需选择支持对应接口的模型。

模型规格参考：[GLM-5.3-Flash](https://docs.z.ai/guides/vlm/glm-5.3-flash)、[DeepSeek 模型接口](https://api-docs.deepseek.com/zh-cn/api/list-models/)。Messages 的输入统计包含普通输入、缓存写入和缓存读取，遵循 [Claude 缓存计量说明](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)。

### 稳定性与配置管理

- **模型别名与校验**：在「模型 → 模型别名」设置完整别名 ID 和实际目标，显示名称独立保存。拼写错误会返回候选模型，不会静默换模型。使用白名单密钥时，别名和目标都需要有访问权限。别名可指向路由，路由目标只选择真实平台模型，避免形成循环引用。
- **错误诊断**：「调用记录 → 诊断」区分模型不存在、参数不兼容、渠道校验、额度不足、限流和流式中断，显示已获取的上游错误码与处理建议。只保存分类和诊断元数据；旧记录中未保存的错误码不会凭空补回。
- **兼容性巡检**：运维导航新增独立巡检页，按模型检测普通调用、流式结束、Responses、Messages、工具调用和推理输出。定时巡检默认关闭；可设置每日请求与 Token 预算，按北京时间重置。所有检测使用合成请求，结果与用户用量分开保存；失败状态变化可通过已有 Webhook 提醒。
- **临期额度**：在「账号 → 临期额度」设置优先天数与每日提醒。WorkBuddy 国际及 CodeBuddy 国内依据额度包真实有效期优先选取账号，保留暂停、冷却、并发和会话约束。未知或超过 24 小时未更新的数据不作为优先依据，登录凭据有效期不等于额度有效期。
- **配置历史**：运维导航新增修改记录与差异预览，可恢复单条路由、服务、密钥规则、模型别名或任务设置。服务凭据随版本加密保存，预览隐藏凭据；恢复前会检查配置是否已被其他操作修改。保留最近 90 天、最多 500 次修改，完整加密备份包含历史所需的密钥文件。

### 平台与模型前缀

| 平台 | 添加方式 | 模型前缀 |
|---|---|---|
| TRAE | 网页登录或导入凭据 | `trae/` |
| CodeBuddy 国内 | 网页登录或导入凭据 | `codebuddy/` |
| WorkBuddy AI 国际站 | 官方网页授权；支持多个账号 | `workbuddy/` |
| MonkeyCode | Windows 登录助手或导入凭据 | `monkeycode/` |
| Qoder 国内／国际 | 官方网页授权、PAT 或 JSON；分区多账号池 | `qoder-cn/`、`qoder/` |
| Antigravity | Google 网页授权、refresh token 或 JSON；独立多账号池 | `antigravity/` |
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

接入 OpenCode Go 时，在「添加服务」或「编辑服务」勾选 **启用 OpenCode 支持**。该开关仅对当前服务生效，默认关闭；开启后，Chat / Responses 转发保留客户端会话头与真实 User-Agent，自动补齐 `x-opencode-session`。控制台测试请求也适用，Responses 续接会关联已返回的 response ID。

建议客户端明确发送每个对话的会话 ID。缺少时，中转对调用密钥、服务和开场上下文进行 HMAC 运算生成匿名 ID，不保存对话正文；相同开场可能共用路由标识，压缩改变开场时也可能改变标识。Responses 续接关联在内存中保留一小时。身份凭据使用配置的上游 Key，客户端 Cookie 和无关请求头不会被转发。要求见 [OpenCode Go 官方说明](https://opencode.ai/docs/go/#where-can-i-use-it)。

智能路由把多个模型放在一个稳定别名下。例如创建 `code-stable` 路由后，客户端调用 `route/code-stable`。可选择顺序优先、轮询、低延迟或「自适应：余额、并发与健康」策略。

自适应策略跳过已确认额度耗尽、冷却和并发已满的目标，结合近期成功率和首响应耗时分配请求。未知余额仍可尝试，各平台积分不会直接跨平台比较。可设置每个目标与每个账号的并发上限；请求结束或取消后释放占用。故障转移仅发生在有效内容或工具调用输出之前，已经输出的请求不会切换上游。

### 密钥限额与请求诊断

在「API 密钥」中点击密钥旁的三个点，选择「用量限制」。每日／每月 Token、并发和每分钟请求数设为 `0` 表示不限，模型白名单留空表示允许全部；也可暂停密钥。限制检查客户端提交的完整模型 ID，允许一个路由别名也就允许其配置的目标。

调用前预占输入估算与输出上限，完成后按上游用量结算；重试中上游明确报告的用量也会累计。上游未提供用量且已经输出时，保守扣除预占数并标为估算。每日和每月按北京时间划分，清空日志或重启服务不会重置已消耗限额。估算会留出余量，接近限额时可降低请求的输出上限。

每个 API 请求返回 `X-Request-ID`。在「调用记录」搜索模型或请求 ID，再点击「诊断」，可查看实际目标、账号、尝试链、HTTP 状态、首响应时间和结束原因。HTTP 200 中的流内错误或缺失完成标记会标为失败。历史记录保留原统计字段，新增诊断字段从升级后开始采集。

「首响应」是服务器收到完整请求后，到第一段有效输出的耗时；有效输出可能是文本、推理内容或工具调用。它包含中转与通道处理，不包含客户端连接服务器、上传请求或最终接收输出的网络时间。非流式请求只能在完整响应返回后记录该时间。

### 模型能力与 Codex 压缩

在「模型 → 能力」查看或设置工具、图片和上下文长度。「检测能力」只发送少量合成请求，可能消耗上游额度；不能确认的项目保留为未知。目录、检测和人工标记分别保留来源，支持恢复自动结果。

Responses 原生、格式转换与 Codex 远程压缩分别标记。内置平台不会因人工标签而获得未实现的协议。自定义服务的 `/v1/responses/compact` 直接转发上游，只有真实返回一个 `compaction` 项的检测才标记支持。路由压缩只选已确认支持的目标；DeepSeek 等格式转换渠道不会生成假的压缩项。

### Antigravity

在「账号 → 添加账号」选择 **Antigravity**，通过 Google 网页完成授权，或在「导入凭据」粘贴 Google `refresh_token`（通常以 `1//` 开头）。也接受包含 `refresh_token` / `refreshToken` 的 JSON，可选提供 `project_id` / `projectId`。导入时验证令牌、发现 Cloud Code Assist project，并获取账号自己的 Gemini 模型目录。

本机登录完成后自动添加账号。远程部署时，Google 回调仍使用本机 `localhost` 地址；如果授权后页面无法打开，将浏览器地址栏中的完整回调地址粘贴回控制台，点击「完成授权并添加」。授权状态一次有效、绑定当前管理员会话，10 分钟后过期。

账号支持轮换、暂停、删除、刷新和额度查询；额度按各周期的剩余百分比展示，不换算成其他平台的积分。临期自动续期；单账号限额在输出前尝试其他账号，已经开始输出的请求不会重放。工具调用保存 thought signature 并保持原账号，防止多账号轮换破坏工具续接。Antigravity 没有签到活动，因此不显示签到按钮。

模型目录优先展示 Google 当前默认推荐的型号。部分型号虽然仍在目录中，但具体服务环境可能返回 404 或容量限制；中转会在尚未输出时尝试同型号的其他 Google 服务入口，保留实际型号。所有入口均不可用时，错误中会显示入口状态；额度或容量限制、限流冷却会明确返回 429，方便选择其他模型或稍后重试。

模型使用 `antigravity/` 前缀，例如 `antigravity/gemini-3-flash`；实际可用名称以模型页和 `/v1/models` 为准。支持 Chat Completions，以及转换后的 Responses、Messages；工具、思考内容和 Token 用量随响应返回，不提供原生 Responses 压缩。输入图片使用 base64 data URL。缺少上游完成标记会回显流中断错误。

Google 服务需要可用的出网网络；后端默认读取系统代理环境变量，也可配置 `ANTIGRAVITY_PROXY=http://proxy-host:port`。Docker 中的 `localhost` 指容器自身，宿主机代理可使用 `host.docker.internal` 或实际代理地址。首次使用需要在服务端 `.env` 中配置 `ANTIGRAVITY_CLIENT_ID`、`ANTIGRAVITY_CLIENT_SECRET`，使用与 refresh token 匹配的 OAuth 应用参数。仓库不内置这些值；自己的 OAuth 应用也必须具备所需的服务访问权限。

账号凭据使用 AES-GCM 加密保存在管理数据卷中；恢复时需要同一份 `.antigravity.key`，已纳入现有加密 `.ubak` 备份。实现参考 [agent2api](https://github.com/aimod-cc/agent2api)，其许可与使用声明保存在 [第三方说明](THIRD_PARTY_NOTICES.md)。

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
| `codebuddy/` | ✓ | 格式兼容 | 格式兼容 |
| `workbuddy/` | ✓ | 格式兼容 | 格式兼容 |
| `monkeycode/` | 文本对话 | — | — |
| `qoder/` | ✓ | 格式兼容 | 格式兼容 |
| `qoder-cn/` | ✓ | 格式兼容 | 格式兼容 |
| 自定义前缀 | 视上游而定 | 视上游而定 | — |
| `route/` | 视目标而定 | 视目标而定 | 视目标而定 |

「格式兼容」表示由中转转换请求和响应，不代表上游原生支持 Responses 或 Codex 远程压缩。以模型目录中的能力标记和实际检测结果为准。

## 统计、自动任务与备份

调用记录保存模型、实际路由目标、HTTP 状态、耗时和 Token 数量，**不保存提示词或回复正文**。控制台展示按日、按周和累计用量；早于启用记录功能的请求无法回填。

自动任务支持每日签到及按间隔刷新账号状态和余额。Webhook 通知不包含凭据。单个账号签到或查询失败不会中断其他账号；调用测试会显示已过滤敏感信息的上游错误。

控制台导出的 `.ubak` 文件使用密码派生密钥与 AES-GCM 加密，包含各平台账号、WorkBuddy 解密所需文件与配置。勾选「包含调用记录」时，还包含调用历史和 WorkBuddy 用量；迁移到另一台电脑时可直接通过「备份恢复」导入。使用旧版备份或手动迁移时，应另行确认 WorkBuddy 独立数据卷是否已完整保留。

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

### 出站代理

在「设置 → 出站代理」填写 HTTP/HTTPS 代理地址，可包含用户名和密码，保存后新请求立即生效：

- **Antigravity 独立代理**：只用于 Google 授权、令牌刷新、模型和额度查询，不改变其他平台的网络出口。
- **全局代理**：用于 TRAE、CodeBuddy、WorkBuddy、MonkeyCode、Qoder 和自定义服务。Antigravity 独立代理、WorkBuddy 已有的账号代理优先于全局代理。
- 清除独立代理后 Antigravity 跟随全局代理；清除全局代理后其余平台直接连接。内部管理接口和容器健康检查不走代理。

地址与认证信息加密存储在管理数据卷中，接口仅返回是否已配置。导出完整备份时请保留对应加密密钥。也可通过 `ANTIGRAVITY_PROXY` / `UNIFIED_OUTBOUND_PROXY` 提供初始配置；网页保存的设置优先。

容器中的 `127.0.0.1` 指向容器自身。使用宿主机代理时，填写容器可访问的宿主机地址，并限制代理监听范围和认证，避免向公网开放。Mihomo 可使用独立 HTTP 入口，无需开启 TUN 或改变服务器默认路由；配置方式见 [Mihomo 官方文档](https://wiki.metacubex.one/en/config/general/)。

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
