# 控制台模块与接口

控制台提供密钥限额、请求诊断、自适应路由、模型能力及网页升级／回滚。
React 界面与 Python 后端复用现有账号、密钥和历史统计。

| 模块 | 职责 | 依赖 |
| --- | --- | --- |
| diagnostics | 请求 ID、尝试链、账号、首响应时间、结束原因；不存正文 | 原有请求统计 |
| key-limits | 每日／每月 Token、并发、每分钟请求数、模型白名单 | diagnostics |
| adaptive-routing | 结合可用余额、正在执行的请求和近期健康情况分配请求 | diagnostics、原有账号池 |
| model-capabilities | 协议、图片、工具、上下文和压缩能力；来源、人工覆盖和检测 | diagnostics |
| managed-releases | 检查可信仓库版本，构建、替换、健康检查与回滚 | Docker Engine API |

## 行为约定

- 限额默认关闭；配置立即生效，调用前预占预计 Token，完成后用上游报告的用量结算。未提供用量但已输出时，保守扣除预占额度，并标注估算。每日和每月按北京时间划分。日志清空、保留期变更和重启不能重置限额；断开连接必须释放并发。
- 模型白名单检查客户端请求的完整模型 ID；允许路由别名即允许其配置的目标，不允许借其他路由绕过白名单。
- 每次调用返回 `X-Request-ID`。诊断记录包含平台、账号标识、尝试链、HTTP 状态、首响应时间和结束原因；不记录提示词、回复、请求头、凭据或上游原始错误正文。旧统计数据保留。
- 新增自适应策略，原有策略保持兼容。余额单位不同，不直接跨平台比较积分数；未知余额不会被当成零，已确认耗尽、冷却和满并发的目标会跳过。已有输出后不切换渠道。
- 未证实的模型能力显示未知。原生 Responses、格式转换及 Codex 远程压缩分开标记；检测只发合成测试内容，不能用普通文本回复冒充工具、图片或压缩能力。
- 升级只访问配置的 GitHub 仓库和固定提交。只有管理员能操作，校验 CSRF。构建不带运行凭据；升级保持端口、环境变量和数据卷，先构建再切换。新版本健康检查失败自动恢复原容器，支持手动回滚。升级任务在临时 Python 容器执行，平时仍是一个运行容器；未接 Docker 控制接口的安装显示明确原因。

## 管理接口

以下接口均要求管理会话；写操作还要求 `X-CSRF-Token`。

| 接口 | 方法 | 用途 |
| --- | --- | --- |
| `/admin/api/unified/key-limits/{key_id}` | GET / PATCH | 查询和修改密钥限额 |
| `/admin/api/unified/diagnostics/{request_id}` | GET | 查看请求的诊断元数据 |
| `/admin/api/unified/model-capabilities/{model}` | GET / PATCH / DELETE | 模型能力查询、人工标记和恢复自动结果 |
| `/admin/api/unified/capability-probe` | POST | 用合成请求检测模型能力 |
| `/admin/api/unified/releases` | GET | 当前版本、上次版本和任务状态 |
| `/admin/api/unified/releases/check` | POST | 检查可信仓库中的最新提交 |
| `/admin/api/unified/releases/{action}` | POST | 执行 upgrade 或 rollback |
| `/admin/api/unified/releases/operations/{id}` | GET | 查看任务进度；亦可使用任务专属只读凭证 |

## 验证

后端使用 `python -m unittest discover -s tests -v`（容器中指定独立测试目录，不挂真实账号）。
前端使用 `ui/node_modules/vite/bin/vite.js build`，并以独立浏览器验证限额编辑、诊断详情、智能路由、能力配置与版本操作。
升级工作流使用隔离测试容器验证成功替换、保留挂载和故障回滚，不切换真实中转服务。

Docker 实测已验证镜像构建、成功替换、端口／环境／数据卷保留、临时任务容器手动回滚、进度查看凭证、故障镜像自动恢复及状态文件不落凭据。测试仅使用隔离容器、独立数据卷和合成凭据。

参考：[Docker Engine API](https://docs.docker.com/reference/api/engine/)、[GitHub Commit API](https://docs.github.com/en/rest/commits/commits)。
