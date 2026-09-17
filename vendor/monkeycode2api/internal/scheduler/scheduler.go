// Package scheduler 定时任务：每日签到 + 额度到账检查 + 账号健康巡检。
// 到账检查（quota arrival check）：每天自动拉取钱包，确认 daily_token_balance /
// daily_token_limit 非零（免费=1e7），即"1000万每日额度到账"，并把结果写回 auth 与 /status。
package scheduler

import (
	"context"
	"log"
	"time"

	"monkeycode2api/internal/cred"
	"monkeycode2api/internal/pool"
	"monkeycode2api/internal/upstream"
)

// Config 调度器配置。
type Config struct {
	Pool     *pool.Pool
	Upstream *upstream.Client
	AuthDir  string
	// CheckinHours 每日签到时刻（小时，0-23）。每个时刻触发一次"尽力签到"：
	// 若该账号当天已签到则跳过，未签到的才执行 —— 不会重复拿积分。
	// 默认 [9, 21]（早 9 点 + 晚 21 点两个窗口，确保首个时段漏掉也不会整天漏签）。
	CheckinHours []int
	// CheckinHour / CheckinMinute 向后兼容：若设置了 CheckinHours 则以它为准；
	// 否则退化为单时刻 [CheckinHour]@CheckinMinute。
	CheckinHour   int
	CheckinMinute int

	// 到账检查：每天执行时间（可配置），保证能观察到当日额度是否刷新。
	QuotaCheckHour   int
	QuotaCheckMinute int
}

// Scheduler 调度器。
type Scheduler struct {
	cfg Config
}

func New(cfg Config) *Scheduler {
	if len(cfg.CheckinHours) == 0 {
		if cfg.CheckinHour >= 0 {
			cfg.CheckinHours = []int{cfg.CheckinHour}
		} else {
			// 默认双窗口：早 9 点 + 晚 21 点
			cfg.CheckinHours = []int{9, 21}
		}
	}
	if cfg.QuotaCheckHour < 0 {
		cfg.QuotaCheckHour = 0
	}
	return &Scheduler{cfg: cfg}
}

// Run 启动调度循环。
func (s *Scheduler) Run(ctx context.Context) {
	// 启动即做一次到账检查（若还没做）
	s.checkAll(ctx)

	// 主循环：按最近的一个触发点（各签到时刻 / 到账检查时刻）触发
	for {
		now := time.Now()
		var nx time.Time
		// 每个签到时刻都是候选触发点
		for _, h := range s.cfg.CheckinHours {
			cand := nextAt(now, h, s.cfg.CheckinMinute)
			if nx.IsZero() || cand.Before(nx) {
				nx = cand
			}
		}
		nextQuota := nextAt(now, s.cfg.QuotaCheckHour, s.cfg.QuotaCheckMinute)
		if nx.IsZero() || nextQuota.Before(nx) {
			nx = nextQuota
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Until(nx)):
		}
		// 命中任意签到时刻 → 尽力签到（已签跳过）
		doCheckin := false
		for _, h := range s.cfg.CheckinHours {
			if hourEquals(nx, h, s.cfg.CheckinMinute) {
				doCheckin = true
				break
			}
		}
		if doCheckin {
			s.checkinAll(ctx)
		}
		if nx.Equal(nextQuota) {
			s.checkAll(ctx)
		}
	}
}

// hourEquals 判断某个时刻是否命中 hour:minute（按同一天内的时/分比较）。
func hourEquals(t time.Time, hour, minute int) bool {
	return t.Hour() == hour && t.Minute() == minute
}

// timeUntilNext returns duration until next occurrence of hour:minute (local).
func nextAt(now time.Time, hour, minute int) time.Time {
	// 上一个目标时刻
	next := time.Date(now.Year(), now.Month(), now.Day(), hour, minute, 0, 0, now.Location())
	if !next.After(now) {
		next = next.Add(24 * time.Hour)
	}
	return next
}

// checkinAll 批量签到。
func (s *Scheduler) checkinAll(ctx context.Context) {
	for _, a := range s.cfg.Pool.AllAccounts() {
		st, err := s.cfg.Upstream.GetCheckinStatus(ctx, a)
		if err != nil {
			if cred.IsAuthInvalid(err) {
				s.cfg.Pool.Disable(a.UID, "auth invalid")
			} else if kind, ok := upstream.Classify(err); ok && kind == upstream.ErrRateLimit {
				// 429 短冷却稍后重试；不累计错误
				log.Printf("checkin status %s: rate limited, retry later", a.UID)
				continue
			}
			log.Printf("checkin status %s: %v", a.UID, err)
			continue
		}
		if st.CheckedIn {
			log.Printf("checkin: %s 今日已签 (连续 %d 天)", a.Nickname, st.Streak)
			continue
		}
		// 未签到 → 求解 PoW 验证码并提交签到
		if err := s.cfg.Upstream.DoCheckin(ctx, a); err != nil {
			log.Printf("checkin: %s 失败(稍后重试): %v", a.UID, err)
			if cred.IsAuthInvalid(err) {
				s.cfg.Pool.Disable(a.UID, "auth invalid")
			} else if kind, ok := upstream.Classify(err); ok && kind == upstream.ErrRateLimit {
				s.cfg.Pool.Cooldown(a.UID, pool.CoolSoft, 10*time.Minute, "checkin 429")
			}
			continue
		}
		log.Printf("checkin: %s 签到成功 (+%d 积分)", a.Nickname, upstream.CheckinCreditReward)
		_ = a.SaveFile(s.cfg.AuthDir)
	}
	s.cfg.Pool.SaveState()
}

// checkAll 到账检查：拉钱包，记录 balance / daily_token_balance / daily_token_limit。
func (s *Scheduler) checkAll(ctx context.Context) {
	for _, a := range s.cfg.Pool.Accounts() {
		w, err := s.cfg.Upstream.GetWallet(ctx, a)
		if err != nil {
			if cred.IsAuthInvalid(err) {
				log.Printf("quota check: account %s auth invalid → disable (relogin needed)", a.UID)
				s.cfg.Pool.Disable(a.UID, "auth invalid")
				continue
			}
			log.Printf("quota check: %s: %v", a.UID, err)
			continue
		}
		prev := a.Wallet
		if a.UpdateWallet(cred.Wallet{
			Balance:           w.Balance,
			DailyTokenBalance: w.DailyTokenBalance,
			DailyTokenLimit:   w.DailyTokenLimit,
		}) {
			_ = a.SaveFile(s.cfg.AuthDir)
		}
		s.cfg.Pool.SyncWallet(a)
		// 到账判断：免费账号每日额度非 0 视为已到账
		ok := isArrived(w.DailyTokenBalance, w.DailyTokenLimit)
		log.Printf("quota check: %s 积分=%.2f 今日额度余额=%.0f / 上限=%.0f (上次 积分=%.0f / 上限=%.0f) 到账=%v",
			a.UID,
			float64(w.Balance)/1000,
			float64(w.DailyTokenBalance), float64(w.DailyTokenLimit),
			float64(prev.Balance)/1000, float64(prev.DailyTokenLimit),
			ok,
		)
	}
	s.cfg.Pool.SaveState()
}

// isArrived 判断"当日额度已到账"：
// 到账 = 今日额度上限>0（接口返回了具体额度），即额度机制对账号生效。
// 额度是自动发放的，无需去"领"，非 0 即为到账。
func isArrived(balance, limit int64) bool {
	return limit > 0 && balance >= 0
}
