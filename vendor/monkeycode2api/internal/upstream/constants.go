// Package upstream 是 MonkeyCode(长亭百智云) 上游客户端。
// 本文件集中管理上游技术常量与端点（来自实测，改动请谨慎）。
package upstream

// 上游技术常量
const (
	// BaseAPI monkeycode-ai.com 前端把全部 /api/v1 请求打到同源后端，
	// 因此 2api 直接以该 Host 作为上游 Base。
	BaseAPI = "https://monkeycode-ai.com"

	// 会话 Cookie 名（登录后浏览器种下的 session cookie）。
	// monkeycode-ai.com 后端通过 same-origin cookie 认证（credentials: same-origin）。
	// 不同灾备 Host 下 cookie 名可能不同，均按 auth 文件中的 cookie 原样透传。
	ConfigCookieName = "monkeycode_ai_session"
)

// 端点常量（全部为 SPA /api/v1 逆向所得）
const (
	// 账号 / 会话
	EpLogin   = "/api/v1/users/login"        // GET → 302 跳百智云 OAuth 授权页
	EpLogout  = "/api/v1/users/logout"       // POST 登出
	EpUserMe  = "/api/v1/users/status"       // GET data.user（依赖 cookie 会话）
	EpSub     = "/api/v1/users/subscription" // GET 会员档位 / 到期
	EpMembers = "/api/v1/users/members"      // GET 成员列表

	// 钱包 / 积分 / 额度 / 签到（核心 到账检查 与 每日签到）
	EpWallet     = "/api/v1/users/wallet"                          // GET → { balance, daily_token_balance, daily_token_limit, ... }
	EpCheckin    = "/api/v1/users/wallet/checkin"                  // GET 状态; POST {captcha_token} 执行签到
	EpWalletTx   = "/api/v1/users/wallet/transaction"              // GET 流水
	EpWalletEx   = "/api/v1/users/wallet/exchange"                 // POST 兑换码
	EpWalletRech = "/api/v1/users/wallet/recharge"                 // POST 充值
	EpSubCredit  = "/api/v1/users/subscription/credit-consumption" // credit 补额开关

	// 模型
	EpModelsAvail  = "/api/v1/users/models/available" // GET 平台内置可选模型（含基础档）
	EpModels       = "/api/v1/users/models"           // GET/POST 用户自定义模型
	EpModelsHealth = "/api/v1/users/models/health-check"

	// 任务 / 对话（Agent 通道）
	EpTasks       = "/api/v1/users/tasks" // POST 创建任务 {content, cli_name, model_id, resource,...}
	EpTasksStop   = "/api/v1/users/tasks/stop"
	EpTasksCtl    = "/api/v1/users/tasks/control"
	EpTasksRounds = "/api/v1/users/tasks/rounds"
	// 任务流 WebSocket（对话正文）：安全 WebSocket 通道指向
	// /api/v1/users/tasks/stream?id=<task_id>&mode=develop
	EpTasksStream = "/api/v1/users/tasks/stream"
)

// 月度/每日额度常量（来自 monkeycode.docs.baizhi.cloud 额度说明）
const (
	TokenQuotaFreePerDay = 10_000_000 // 免费用户每日基础模型 Token（1000 万）
	CheckinCreditReward  = 100        // 每日签到积分数
)

// FreeModelAlias 基础档模型别名（免费额度仅覆盖基础模型）
var FreeModels = []string{
	"kimi-k2.5",
	"minimax-m2.5",
	"qwen3.5-plus",
}

// FreeModelIDs 基础档模型的名字 → 平台模型 UUID（来自 GET /api/v1/users/models/available）。
// 任务创建的 model_id 需要传 UUID 而非名字，否则上游 code=400 参数错误。
// 这些是平台公开的基础档模型，UUID 稳定。
var FreeModelIDs = map[string]string{
	"kimi-k2.5":                  "03dc8f96-8d9a-4094-a2b3-c4128b3a78a4",
	"minimax-m2.5":               "8e22c508-97ad-490b-b38e-113faeeca275",
	"qwen3.5-plus":               "d937e77c-6941-48cf-913c-ec51cce138bb",
	"qwen3.6-plus":               "7f292d79-1f0b-40de-9b98-46b1ba66f7a2",
	"monkeycode-basic/kimi-k2.5": "c82ac16d-aaf5-4197-9040-4227ec2299a5",
}
