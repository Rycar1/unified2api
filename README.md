<p align="center">
  <img src="images/unified2api-logo.png" alt="Unified2API Logo" width="116">
</p>

<h1 align="center">Unified2API</h1>

<p align="center">
  一个控制台管理多个 AI 平台账号，用统一的 OpenAI 风格 API 调用模型。
</p>

<p align="center">
  TRAE · CodeBuddy 国内 · WorkBuddy AI 国际站 · MonkeyCode · 自定义服务
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
| 智能路由 | 为多个模型设置别名，按顺序、轮询或延迟选路，并在请求开始前故障转移 |
| 自定义服务 | 填写 Base URL 和 API Key，接入 OpenAI 兼容上游 |
| 用量与诊断 | 查看 Token 消耗、模型分布、请求状态、耗时和上游错误 |
| 自动任务 | 定时签到、刷新余额，并通过 Webhook 接收失败或低余额提醒 |
| 备份恢复 | 导出加密的 `.ubak` 配置备份 |

### 平台与模型前缀

| 平台 | 添加方式 | 模型前缀 |
|---|---|---|
| TRAE | 网页登录或导入凭据 | `trae/` |
| CodeBuddy 国内 | 网页登录或导入凭据 | `codebuddy/` |
| WorkBuddy AI 国际站 | 官方网页授权；支持多个账号 | `workbuddy/` |
| MonkeyCode | Windows 登录助手或导入凭据 | `monkeycode/` |
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

在控制台的「账号」页选择平台并完成登录。TRAE 与 CodeBuddy 可以导入平台支持的凭据；MonkeyCode 可下载 Windows 登录助手。WorkBuddy AI 国际站使用官方网页授权，账号保存在独立的 Docker 数据卷中，无需启动桌面端。

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

智能路由把多个模型放在一个稳定别名下。例如创建 `code-stable` 路由后，客户端调用 `route/code-stable`。可选择顺序优先、轮询或低延迟策略；上游在开始响应前失败时，会按配置尝试下一个目标。

### 协议支持

| 模型前缀 | Chat Completions | Responses | Messages |
|---|:---:|:---:|:---:|
| `trae/` | ✓ | — | — |
| `codebuddy/` | ✓ | ✓ | ✓ |
| `workbuddy/` | ✓ | ✓ | — |
| `monkeycode/` | 文本对话 | — | — |
| 自定义前缀 | 视上游而定 | 视上游而定 | — |
| `route/` | 视目标而定 | 视目标而定 | 视目标而定 |

CodeBuddy 的 `deepseek-v4.1-flash` 在客户端未设置推理强度时默认使用 `reasoning_effort: high`；显式设置会覆盖默认值。

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

## 安全与部署

- `.env`、Cookie、账号导出文件和真实 API Key 不应提交到 GitHub。
- 管理会话使用 HttpOnly Cookie，写操作需要 CSRF Token；上游 Key 不会在管理列表中明文回显。
- WorkBuddy 国际站账号池只在 Docker 内部网络运行，不向公网暴露端口。
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

各组件继续适用其原许可证，详见 `vendor/`。
