// Package pool 账号池：内存索引 + 冷却/禁用状态机 + state.json 持久化。
// 选号规则：优先选“还有免费额度（daily_token_balance>0）”且未冷却/未禁用的账号；
// 免费额度耗尽后，回退选剩余积分最多的账号。默认按剩余积分降序。
package pool

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"monkeycode2api/internal/cred"
)

// CoolKind 冷却类型。
type CoolKind int

const (
	CoolHard CoolKind = iota // 额度耗尽 → 长冷却（等隔日刷新）
	CoolSoft                 // 429 → 短冷却
	CoolErr                  // 连续错误 → 中冷却
)

// Status 单账号对外状态（脱敏）。
type Status struct {
	UID             string    `json:"uid"`
	Nickname        string    `json:"nickname,omitempty"`
	Plan            string    `json:"plan,omitempty"`
	Balance         int64     `json:"balance"`             // 积分（厘）
	DailyTokenBal   int64     `json:"daily_token_balance"` // 今日额度剩余
	DailyTokenLimit int64     `json:"daily_token_limit"`   // 今日额度上限
	Cooling         bool      `json:"cooling"`
	Until           time.Time `json:"until,omitempty"`
	Reason          string    `json:"reason,omitempty"`
	Disabled        bool      `json:"disabled"`
	ErrCount        int       `json:"err_count,omitempty"`
}

type entry struct {
	c        *cred.Account
	disabled bool
	reason   string
	until    time.Time
	errCount int
}

func (e *entry) healthy(now time.Time) bool {
	if e.disabled {
		return false
	}
	if !e.until.IsZero() && now.Before(e.until) {
		return false
	}
	return true
}

type stateEntry struct {
	Disabled bool      `json:"disabled"`
	Reason   string    `json:"reason,omitempty"`
	Until    time.Time `json:"until,omitempty"`
}

type stateFile struct {
	Accounts map[string]stateEntry `json:"accounts"`
}

// Pool 账号池。
type Pool struct {
	mu    sync.RWMutex
	path  string
	state *stateFile
	byUID map[string]*entry
	order []*entry // 稳定顺序
}

// New 构建账号池，从 state.json 恢复持久化状态。
func New(statePath string) *Pool {
	p := &Pool{path: statePath, byUID: make(map[string]*entry), order: nil}
	if statePath != "" {
		if raw, err := os.ReadFile(statePath); err == nil {
			var sf stateFile
			if json.Unmarshal(raw, &sf) == nil {
				p.state = &sf
			}
		}
	}
	return p
}

// Add 加入一个账号。
func (p *Pool) Add(a *cred.Account) {
	p.mu.Lock()
	defer p.mu.Unlock()
	e := &entry{c: a}
	if p.state != nil {
		if se, ok := p.state.Accounts[a.UID]; ok {
			e.disabled = se.Disabled
			e.reason = se.Reason
			e.until = se.Until
		}
	}
	if _, ok := p.byUID[a.UID]; !ok {
		p.byUID[a.UID] = e
		p.order = append(p.order, e)
	}
}

// SaveState 持久化冷却/禁用状态。
func (p *Pool) SaveState() {
	p.mu.RLock()
	defer p.mu.RUnlock()
	if p.path == "" {
		return
	}
	sf := stateFile{Accounts: map[string]stateEntry{}}
	for uid, e := range p.byUID {
		if e.disabled || !e.until.IsZero() {
			sf.Accounts[uid] = stateEntry{Disabled: e.disabled, Reason: e.reason, Until: e.until}
		}
	}
	if raw, err := json.Marshal(sf); err == nil {
		if dir := filepath.Dir(p.path); dir != "" && dir != "." {
			_ = os.MkdirAll(dir, 0o755)
		}
		tmp := p.path + ".tmp"
		_ = os.WriteFile(tmp, raw, 0o600)
		_ = os.Rename(tmp, p.path)
	}
}

// Pick 选一个健康账号（剩余免费额度最多优先，其次积分最多）。
func (p *Pool) Pick() *cred.Account {
	return p.PickExcluding(nil)
}

// PickExcluding 选一个健康且未被排除的账号。精选顺序：
// 先看谁还有免费额度（daily_token_balance>0）——取额度剩余最多；
// 大家都没额度时，退到积分最多的账号。全冷却/禁用返回 nil。
func (p *Pool) PickExcluding(exclude map[string]bool) *cred.Account {
	p.mu.RLock()
	defer p.mu.RUnlock()
	now := time.Now()
	skip := exclude
	if skip == nil {
		skip = map[string]bool{}
	}
	var best *entry
	bestScore := -1
	for _, e := range p.order {
		if skip[e.c.UID] {
			continue
		}
		if !e.healthy(now) {
			continue
		}
		// 分数：额度大头（1e9），积分小头
		score := int64(0)
		if bal := e.c.Wallet.DailyTokenBalance; bal > 0 {
			score = bal // 剩余免费额度
		} else {
			// 没额度时用积分兜底（callback 降权，避免饿死）
			score = e.c.Wallet.Balance / 1000
		}
		if score > int64(bestScore) {
			bestScore = int(score)
			best = e
		}
	}
	if best == nil {
		return nil
	}
	return best.c
}

// Disable 永久禁用某账号。
// 在释放写锁后再持久化，避免与 SaveState（RLock）竞争写锁。
func (p *Pool) Disable(uid, reason string) {
	p.mu.Lock()
	p.apply(func(e *entry) bool {
		e.disabled = true
		e.reason = reason
		return true
	}, uid)
	p.mu.Unlock()
	p.SaveState()
}

// Cooldown 冷却某账号。
// 在释放写锁后再持久化。
func (p *Pool) Cooldown(uid string, kind CoolKind, dur time.Duration, reason string) {
	p.mu.Lock()
	p.apply(func(e *entry) bool {
		e.until = time.Now().Add(dur)
		e.reason = reason
		return true
	}, uid)
	p.mu.Unlock()
	p.SaveState()
}

// apply 在调用方已持有 p.mu 写锁时修改某账号 entry；账号不存在则忽略。
func (p *Pool) apply(fn func(*entry) bool, uid string) {
	if e, ok := p.byUID[uid]; ok {
		fn(e)
	}
}

// NoteError 记一次错误，达到阈值（errThreshold）触发冷却。
func (p *Pool) NoteError(uid string, errThreshold int, coolDur time.Duration) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if e, ok := p.byUID[uid]; ok {
		e.errCount++
		if e.errCount >= errThreshold {
			e.until = time.Now().Add(coolDur)
			e.errCount = 0
			e.reason = "连续错误达阈值"
		}
	}
}

// NoteSuccess 成功清零错误计数。
func (p *Pool) NoteSuccess(uid string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if e, ok := p.byUID[uid]; ok {
		e.errCount = 0
	}
}

// SyncWallet 把账号钱包快照更新到池内（到账检查后调用）。
func (p *Pool) SyncWallet(a *cred.Account) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if e, ok := p.byUID[a.UID]; ok {
		e.c.UpdateWallet(a.Wallet)
	}
}

// Accounts 返回全部账号的凭证引用（供调度器遍历）。
func (p *Pool) Accounts() []*cred.Account {
	p.mu.RLock()
	defer p.mu.RUnlock()
	out := make([]*cred.Account, 0, len(p.order))
	for _, e := range p.order {
		out = append(out, e.c)
	}
	return out
}

// AllAccounts Accounts 的别名，命名友好。
func (p *Pool) AllAccounts() []*cred.Account { return p.Accounts() }

// List 返回所有账号状态（脱敏）。
func (p *Pool) List() []Status {
	p.mu.RLock()
	defer p.mu.RUnlock()
	out := make([]Status, 0, len(p.order))
	now := time.Now()
	for _, e := range p.order {
		s := Status{
			UID:             e.c.UID,
			Nickname:        e.c.Nickname,
			Plan:            e.c.Plan,
			Balance:         e.c.Wallet.Balance,
			DailyTokenBal:   e.c.Wallet.DailyTokenBalance,
			DailyTokenLimit: e.c.Wallet.DailyTokenLimit,
			Disabled:        e.disabled,
			ErrCount:        e.errCount,
		}
		if e.disabled {
			s.Reason = e.reason
		} else if !e.until.IsZero() && now.Before(e.until) {
			s.Cooling = true
			s.Until = e.until
			s.Reason = e.reason
		}
		out = append(out, s)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].UID < out[j].UID })
	return out
}
