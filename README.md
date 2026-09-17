# Unified2API

把 TRAE、CodeBuddy、MonkeyCode 多账号和自定义 OpenAI 兼容服务集中到一个控制台，通过统一的 OpenAI 风格 API 调用。

> 本项目用于个人账号与本地服务管理。请遵守对应平台的服务条款，并妥善保管账号凭据和 API 密钥。

## 功能

- 一个后端进程和一个管理控制台
- TRAE、CodeBuddy、MonkeyCode 多账号管理
- 三个平台启用账号一键签到并刷新额度
- 平台内账号池、状态切换和失败重试
- 自定义 `Base URL + API Key` 接入
- 统一模型目录和平台前缀路由
- OpenAI Chat Completions 兼容接口
- CodeBuddy Responses 和 Messages 接口
- 独立客户端 API 密钥
- Windows MonkeyCode 登录助手
- React 响应式管理界面

## 页面与端口

| 用途 | 默认地址 |
|---|---|
| 管理控制台 | `http://localhost:8080/admin/` |
| API Base URL | `http://localhost:8080/v1` |
| 健康检查 | `http://localhost:8080/healthz` |

`8787`、`7864` 和 `18080` 作为兼容端口映射到同一个服务。默认仅监听本机 `127.0.0.1`。

## 快速启动

### Windows

需要 Windows 10/11 x64 和 Docker Desktop。

```powershell
git clone https://github.com/Rycar1/unified2api.git
cd unified2api
.\setup.ps1
```

脚本会生成随机管理密钥和客户端密钥，构建镜像并启动服务。生成的密钥保存在本机 `.env`，该文件已被 Git 忽略。

### Linux / macOS

```bash
cp .env.example .env
# 编辑 .env，替换其中的占位值
docker compose up -d --build
```

`ADMIN_KEY` 建议使用至少 20 个随机字符。`UNIFIED_API_KEY` 是客户端请求统一 API 时使用的 Bearer Token。

## 添加账号

打开管理控制台，使用 `.env` 中的 `ADMIN_KEY` 登录。

账号页面的“一键签到”会依次处理三个平台中的所有启用账号。已经签到的账号不会重复领取；单个账号失败不会中断其他账号，完成后会汇总成功和失败数量并刷新额度。

### TRAE / CodeBuddy

选择对应平台后，可以通过网页登录或导入该平台支持的凭据文件添加账号。网页登录过程在平台官方页面完成。

### MonkeyCode

Windows 用户可以从控制台下载登录助手。登录助手会打开独立浏览器会话，在官网登录完成后将本次授权提交给本机服务。

也可以手动导入 Cookie 或 JSON。请勿把 Cookie、会话 ID 或导出的凭据提交到 GitHub。

### 自定义服务

填写服务名称、模型前缀、OpenAI 兼容 Base URL、上游 API Key 和模型列表。

假设服务前缀是 `myapi`，上游模型为 `org/model`，客户端调用时使用：

```json
{
  "model": "myapi/org/model"
}
```

上游 API Key 只保存在服务端，管理接口不会回显完整 Key。

## API 示例

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

读取实际可用模型：

```bash
curl http://localhost:8080/v1/models \
  -H "Authorization: Bearer YOUR_UNIFIED_API_KEY"
```

## 协议支持

| 模型前缀 | Chat Completions | Responses | Messages |
|---|---:|---:|---:|
| `trae/` | 支持 | — | — |
| `codebuddy/` | 支持 | 支持 | 支持 |
| `monkeycode/` | 文本对话 | — | — |
| 自定义前缀 | 透传 | 透传 | — |

模型能力和可用性取决于账号权限、剩余额度和上游服务。

## 前端开发

控制台源码位于 `ui/`，使用 React、Vite 和 Lucide。构建结果写入 `unified/static/`，运行服务时不需要安装 Node.js。

```bash
cd ui
pnpm install
pnpm run build
```

## 项目结构

```text
unified2api/
├─ unified/       # FastAPI 统一后端、路由和控制台静态文件
├─ ui/            # React 控制台源码
├─ native/        # TRAE 与 MonkeyCode 原生桥接
├─ vendor/        # 上游项目源码快照及许可证
├─ login-helper/  # MonkeyCode Windows 登录助手源码
├─ tests/         # 后端和连接测试
├─ Dockerfile
└─ compose.yaml
```

## 数据保存

Docker 使用以下命名卷保存数据：

| 数据卷 | 内容 |
|---|---|
| `unified2api_trae-auth` | TRAE 凭据 |
| `unified2api_trae-data` | TRAE 账号池状态 |
| `unified2api_buddy-data` | CodeBuddy、MonkeyCode、自定义服务和客户端密钥 |

停止服务时不要使用 `docker compose down -v`，否则会删除这些数据卷。

## 安全说明

- 不要提交 `.env`、Cookie、账号导出文件或真实 API Key。
- 管理会话使用 HttpOnly Cookie，写操作需要 CSRF Token。
- 默认配置仅适合本机使用。
- 对外部署时应配置 HTTPS、设置 `SECURE_COOKIE=true`，并限制管理端访问来源。
- 登录助手每次使用独立临时浏览器会话，不应读取日常浏览器配置目录。

## 常用维护命令

```bash
docker compose ps
docker compose logs --tail 100
docker compose down
```

运行测试：

```bash
docker run --rm \
  --mount "type=bind,source=$(pwd)/tests,target=/app/tests,readonly" \
  unified2api-app python -m unittest discover -s tests -v
```

## 上游项目

本项目整合并保留以下项目的源码快照与许可证：

- [JeffHu0912/trae2api](https://github.com/JeffHu0912/trae2api)
- [ShouZhuo0413/codebuddy2api](https://github.com/ShouZhuo0413/codebuddy2api)
- [ZFXing-lite/monkeycode2api](https://github.com/ZFXing-lite/monkeycode2api)

各上游组件继续适用其原许可证，详见 `vendor/` 中的许可证文件。
