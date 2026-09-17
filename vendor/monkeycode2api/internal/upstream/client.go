package upstream

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"

	"monkeycode2api/internal/cred"
)

// Client 上游 HTTP 客户端。
type Client struct {
	Base string
	HTTP *http.Client
	// Models 动态模型目录（可选）。为 nil 时 ResolveModelID 退回到静态 FreeModelIDs。
	Models *ModelCatalog
}

func New() *Client {
	return &Client{
		Base: BaseAPI,
		HTTP: &http.Client{},
	}
}

// apiResp 统一响应外壳（code 非 0 即失败）。
type apiResp struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
}

type apiError struct {
	Kind string
	Code int // 上游业务错误码（来自 {code} 外壳；0 表示 HTTP 层错误）
	msg  string
}

func (e *apiError) Error() string { return e.msg }

func (e *apiError) KindOf(k string) bool { return e.Kind == k }

// 上游可识别错误类型
const (
	ErrAuth      = "auth"       // cookie 失效 / 401 → 需要重新登录
	ErrQuota     = "quota"      // 额度不足（到账/刷额度）
	ErrRateLimit = "rate_limit" // 429
	ErrNotFound  = "not_found"  // 404
	ErrBusy      = "busy"       // 已有任务在跑，需等它结束再建新任务（瞬态）
	ErrUpstream  = "upstream"   // 其他 5xx
)

// quotaCodes 上游已知的「额度/需升级」业务错误码。
// 注意：10811 实测含义是「已有一个正在运行的任务」（非额度不足），
// 因此不作为 quota 处理，而是映射为瞬态 ErrBusy（见 task.go）。
// 4002 记为配额/升级类兜底。
var quotaCodes = map[int]bool{4002: true}

// busyCodes 上游「当前账号忙/有任务在跑」的业务错误码 → ErrBusy（短冷却、让位）。
var busyCodes = map[int]bool{10811: true}

// quotaCode 判断某业务码是否为「额度不足」。
func quotaCode(code int) bool { return quotaCodes[code] }

// busyCode 判断某业务码是否为「账号忙（已有运行任务）」。
func busyCode(code int) bool { return busyCodes[code] }

// doJSON 带 cookie 调用上游 JSON 接口。a 可为 nil（公开端点，如验证码）。
func (c *Client) doJSON(ctx context.Context, a *cred.Account, method, path string, body any, out any) error {
	var rd io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return err
		}
		rd = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.Base+path, rd)
	if err != nil {
		return err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if a != nil {
		if a.CookieHeader() != "" {
			req.Header.Set("Cookie", a.CookieHeader())
		}
		if csrf := a.Session.CSRF; csrf != "" {
			req.Header.Set("X-CSRF-Token", csrf)
		}
	}
	ua := ""
	if a != nil {
		ua = a.Session.UserAgent
	}
	if ua == "" {
		ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", c.Base+"/")
	req.Header.Set("Accept", "application/json")

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	b, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if err != nil {
		return err
	}

	// 非 2xx 归类
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return &apiError{Kind: ErrAuth, msg: fmt.Sprintf("upstream %d %s", resp.StatusCode, strings.TrimSpace(string(b)))}
	}
	if resp.StatusCode == http.StatusTooManyRequests {
		return &apiError{Kind: ErrRateLimit, msg: "upstream 429"}
	}
	if resp.StatusCode == http.StatusNotFound {
		return &apiError{Kind: ErrNotFound, msg: "upstream 404"}
	}
	if resp.StatusCode >= 500 {
		return &apiError{Kind: ErrUpstream, msg: fmt.Sprintf("upstream %d %s", resp.StatusCode, truncate(string(b), 200))}
	}

	var ar apiResp
	if err := json.Unmarshal(b, &ar); err != nil {
		// 有些接口直接返回裸对象（如 login 302 场景），原样回传
		if out != nil {
			_ = json.Unmarshal(b, out)
		}
		return nil
	}
	// 判断是否为 {code,...} 外壳：JSON 对象存在 "code" 键时才当作业务外壳。
	// 像验证码这样的 pass-through 接口直接返回裸对象，需要落到 out。
	var top map[string]json.RawMessage
	_ = json.Unmarshal(b, &top)
	if _, hasCode := top["code"]; !hasCode {
		// 裸对象：直接解到 out
		if out != nil {
			return json.Unmarshal(b, out)
		}
		return nil
	}
	if ar.Code != 0 {
		return &apiError{Kind: ErrUpstream, Code: ar.Code, msg: fmt.Sprintf("upstream code=%d msg=%s", ar.Code, ar.Message)}
	}
	if out != nil && len(ar.Data) > 0 {
		return json.Unmarshal(ar.Data, out)
	}
	return nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// User 当前登录用户。
type User struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Email       string `json:"email"`
	Plan        string `json:"plan"`
	HasPassword bool   `json:"has_password"`
	Team        *struct {
		ID   string `json:"id"`
		Name string `json:"name"`
	} `json:"team"`
}

// GetUser 查询当前用户（校验 cookie 有效性）。
func (c *Client) GetUser(ctx context.Context, a *cred.Account) (*User, error) {
	var u struct {
		User *User `json:"user"`
	}
	// The current website returns {code:0,data:{user:{...},teams:[]}}.
	err := c.doJSON(ctx, a, http.MethodGet, EpUserMe, nil, &u)
	if err != nil {
		if ae, ok := err.(*apiError); ok && ae.Kind == ErrAuth {
			return nil, cred.ErrAuthInvalid
		}
		return nil, err
	}
	if u.User == nil || u.User.ID == "" {
		return nil, cred.ErrAuthInvalid
	}
	return u.User, nil
}

// Wallet 钱包 + 每日额度（来自 GET /api/v1/users/wallet）。
type Wallet struct {
	Balance           int64  `json:"balance"`             // 积分余量
	DailyTokenBalance int64  `json:"daily_token_balance"` // 今日额度剩余
	DailyTokenLimit   int64  `json:"daily_token_limit"`   // 今日额度上限
	Currency          string `json:"currency,omitempty"`
}

// GetWallet 拉取钱包（额度到账检查与轮转都基于它）。
func (c *Client) GetWallet(ctx context.Context, a *cred.Account) (*Wallet, error) {
	var w Wallet
	err := c.doJSON(ctx, a, http.MethodGet, EpWallet, nil, &w)
	if err != nil {
		if ae, ok := err.(*apiError); ok && ae.Kind == ErrAuth {
			return nil, cred.ErrAuthInvalid
		}
		return nil, err
	}
	return &w, nil
}

// CheckinStatus 每日签到状态。
type CheckinStatus struct {
	CheckedIn bool  `json:"checked_in"`
	Streak    int   `json:"streak_days,omitempty"`
	Reward    int64 `json:"reward,omitempty"`
}

// GetCheckinStatus GET /api/v1/users/wallet/checkin
func (c *Client) GetCheckinStatus(ctx context.Context, a *cred.Account) (*CheckinStatus, error) {
	var st CheckinStatus
	err := c.doJSON(ctx, a, http.MethodGet, EpCheckin, nil, &st)
	if err != nil {
		if ae, ok := err.(*apiError); ok && ae.Kind == ErrAuth {
			return nil, cred.ErrAuthInvalid
		}
		return nil, err
	}
	return &st, nil
}

// DoCheckin POST /api/v1/users/wallet/checkin ({captcha_token})。
// 会先求解 Cap.js PoW 验证码（internal/upstream/captcha.go）再提交签到；
// 返回的 code 非 0 时，业务错误原样归类。
func (c *Client) DoCheckin(ctx context.Context, a *cred.Account) error {
	capToken, err := c.SolveCaptcha(ctx)
	if err != nil {
		return fmt.Errorf("captcha solve: %w", err)
	}
	var out map[string]any
	err = c.doJSON(ctx, a, http.MethodPost, EpCheckin, map[string]string{"captcha_token": capToken}, &out)
	if err != nil {
		if ae, ok := err.(*apiError); ok && ae.Kind == ErrAuth {
			return cred.ErrAuthInvalid
		}
		return err
	}
	return nil
}

// Model 平台可选模型条目。
type Model struct {
	ID           string `json:"id"`
	Name         string `json:"name"`
	AccessLevel  string `json:"access_level,omitempty"` // basic|pro|ultra
	isFree       bool   `json:"-"`
	InputPrice   int64  `json:"input_price,omitempty"` // 单价（积分/厘）
	SupportImage bool   `json:"support_image"`
	IsHidden     bool   `json:"is_hidden"`
	Tier         string `json:"tier,omitempty"` // basic|pro|flagship（旧字段，保留兼容）
	Provider     string `json:"provider,omitempty"`
	Description  string `json:"description,omitempty"`
	ContextLen   int64  `json:"context_length,omitempty"`
	Thinking     bool   `json:"thinking_enabled,omitempty"`
}

// GetModels GET /api/v1/users/models/available —— 平台内置可选模型。
func (c *Client) GetModels(ctx context.Context, a *cred.Account) ([]*Model, error) {
	var list []*Model
	err := c.doJSON(ctx, a, http.MethodGet, EpModelsAvail, nil, &list)
	if err != nil {
		if ae, ok := err.(*apiError); ok && ae.Kind == ErrAuth {
			return nil, cred.ErrAuthInvalid
		}
		return nil, err
	}
	sort.Slice(list, func(i, j int) bool { return list[i].ID < list[j].ID })
	return list, nil
}
