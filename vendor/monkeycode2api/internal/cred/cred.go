// Package cred 负责 MonkeyCode 账号凭证(auth)文件的加载、原子写回与模块校验。
//
// auth 文件格式（JSON，auths/monkeycode-<uid>.json）：
//
//	{
//	  "session": {
//	    "cookie":  "<type>;<name>=<value>;...",   // 登录后从浏览器拷贝的 Cookie（必需）
//	    "csrf":    "<可选> 后端校准时需要时传入",
//	    "user_agent": "<可选> push 时使用的 UA，默认 Go-http-client"
//	  },
//	  "account": {
//	    "uid": "<用户ID>",
//	    "nickname": "<昵称>",
//	    "plan": "basic|pro|ultra",
//	    "email": "<绑定邮箱>"
//	  },
//	  "wallet": {                 // 上次观测到的钱包快照（用于到账检查差分）
//	    "balance": 123,
//	    "daily_token_balance": 0,
//	    "daily_token_limit": 10000000
//	  }
//	}
package cred

import (
	"errors"
	"strings"
	"sync"
	"time"
)

// Error 一个表示业务可识别错误的类型，用于判定“是否需要重新登录”。
type Error struct {
	msg string
}

func (e *Error) Error() string { return e.msg }

var ErrAuthInvalid = &Error{msg: "auth invalid (re-login required)"}

func IsAuthInvalid(err error) bool {
	var ae *Error
	if errors.As(err, &ae) {
		return strings.Contains(ae.msg, "re-login")
	}
	return errors.Is(err, ErrAuthInvalid)
}

// Session 上游会话凭证。
type Session struct {
	// Cookie 为浏览器登录 monkeycode-ai.com 后种下的会话 Cookie 的原文。
	// 格式自由，原样放进 http.Request 的 Cookie 头。多个 k=v 用 "; " 分隔。
	Cookie    string `json:"cookie"`
	Host      string `json:"host,omitempty"`
	UserAgent string `json:"user_agent,omitempty"`
	// CSRF 可选。后端要求时，请求会带上 X-CSRF-Token。
	CSRF string `json:"csrf,omitempty"`
}

// Wallet 钱包/额度快照（到账检查用）。
type Wallet struct {
	Balance           int64 `json:"balance"`             // 积分（厘）
	DailyTokenBalance int64 `json:"daily_token_balance"` // 今日剩余每日令牌额度
	DailyTokenLimit   int64 `json:"daily_token_limit"`   // 今日每日令牌上限（免费=1e7）
}

// Account a MonkeyCode 账号 credential。
type Account struct {
	UID      string `json:"uid"`
	Nickname string `json:"nickname"`
	Email    string `json:"email,omitempty"`
	// Plan 会员档位: free|pro|ultra
	Plan string `json:"plan,omitempty"`

	Session Session   `json:"session"`
	Wallet  Wallet    `json:"wallet,omitempty"`
	Updated time.Time `json:"updated_at,omitempty"`
	mu      sync.Mutex
}
