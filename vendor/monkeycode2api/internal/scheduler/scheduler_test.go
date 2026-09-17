package scheduler

import (
	"testing"
	"time"
)

// nextCheckinAt computes the next occurrence among the given checkin hours/minute.
// 不依赖 upstream/pool，纯逻辑测试多个签到窗口。
func nextCheckinIn(cfg Config, now time.Time) time.Time {
	var nx time.Time
	for _, h := range cfg.CheckinHours {
		c := nextAt(now, h, cfg.CheckinMinute)
		if nx.IsZero() || c.Before(nx) {
			nx = c
		}
	}
	return nx
}

// TestNewDefaults 默认双窗口：CheckinHours = [9,21]。
func TestNewDefaults(t *testing.T) {
	s := New(Config{CheckinHour: -1})
	if len(s.cfg.CheckinHours) != 2 || s.cfg.CheckinHours[0] != 9 || s.cfg.CheckinHours[1] != 21 {
		t.Fatalf("default CheckinHours = %v, want [9 21]", s.cfg.CheckinHours)
	}
}

// TestBackCompat 旧配置 checkin_hour=9 → 单窗口 [9]。
func TestBackCompat(t *testing.T) {
	s := New(Config{CheckinHour: 9})
	if len(s.cfg.CheckinHours) != 1 || s.cfg.CheckinHours[0] != 9 {
		t.Fatalf("back-compat CheckinHours = %v, want [9]", s.cfg.CheckinHours)
	}
}

// TestBackCompatDefaultMinute 未显式给 CheckinHours 且单点 -1 → 默认双窗口。
func TestBackCompatDefaultMinute(t *testing.T) {
	s := New(Config{CheckinHours: nil, CheckinHour: -1, CheckinMinute: 5})
	if len(s.cfg.CheckinHours) != 2 {
		t.Fatalf("CheckinHours = %v, want default [9 21]", s.cfg.CheckinHours)
	}
	if s.cfg.CheckinMinute != 5 {
		t.Fatalf("CheckinMinute not preserved")
	}
}

// TestNextCheckinMultiWindow 多个窗口选最近触发点。
func TestNextCheckinMultiWindow(t *testing.T) {
	cfg := Config{CheckinHours: []int{9, 21}, CheckinMinute: 5}
	tc := []struct {
		name     string
		now      time.Time
		wantTime time.Time
	}{
		// 现在 08:00 → 最近触发 09:05
		{"08:00",
			time.Date(2026, 1, 1, 8, 0, 0, 0, time.Local),
			time.Date(2026, 1, 1, 9, 5, 0, 0, time.Local)},
		// 现在 10:00 → 最近触发 21:05
		{"10:00",
			time.Date(2026, 1, 1, 10, 0, 0, 0, time.Local),
			time.Date(2026, 1, 1, 21, 5, 0, 0, time.Local)},
	}
	for _, c := range tc {
		if n := nextCheckinIn(cfg, c.now); !n.Equal(c.wantTime) {
			t.Fatalf("%s next = %v, want %v", c.name, n, c.wantTime)
		}
	}
	// 现在 22:00 → 跨天回到次日 09:05
	now := time.Date(2026, 1, 1, 22, 0, 0, 0, time.Local)
	n := nextCheckinIn(cfg, now)
	want := time.Date(2026, 1, 2, 9, 5, 0, 0, time.Local)
	if !n.Equal(want) {
		t.Fatalf("next from 22:00 = %v, want %v", n, want)
	}
}

// TestHourEquals 命中判断。
func TestHourEquals(t *testing.T) {
	at := time.Date(2026, 1, 1, 21, 5, 0, 0, time.Local)
	if !hourEquals(at, 21, 5) {
		t.Fatal("expected match 21:05")
	}
	if hourEquals(at, 9, 5) {
		t.Fatal("expected no match at 9:05")
	}
}
