// Package adapter embeds MonkeyCode in Unified without an additional server.
package adapter

import (
	"context"
	"encoding/json"
	"monkeycode2api/internal/cred"
	"monkeycode2api/internal/pool"
	"monkeycode2api/internal/server"
	"monkeycode2api/internal/upstream"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

var safeID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

type service struct {
	mu  sync.Mutex
	dir string
	p   *pool.Pool
	up  *upstream.Client
}

func reply(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}
func fail(w http.ResponseWriter, code int, message string) {
	reply(w, code, map[string]string{"detail": message})
}

func New(ctx context.Context, dir string) (http.Handler, error) {
	accounts, err := cred.LoadDir(dir)
	if err != nil {
		return nil, err
	}
	s := &service{dir: dir, p: pool.New(filepath.Join(dir, "state.json")), up: upstream.New()}
	s.up.HTTP.Timeout = 30 * time.Second
	s.up.Models = upstream.NewModelCatalog()
	for _, a := range accounts {
		s.p.Add(a)
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/admin/accounts", s.accounts)
	mux.HandleFunc("/admin/accounts/", s.account)
	mux.Handle("/", server.NewHandler(server.Config{Pool: s.p, Upstream: s.up, Models: s.up.Models}))
	// Refresh metadata only; account sign-in remains an explicit user action upstream.
	go func() {
		s.refreshAll(ctx)
		ticker := time.NewTicker(30 * time.Minute)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				s.refreshAll(ctx)
			}
		}
	}()
	return mux, nil
}
func (s *service) refreshAll(ctx context.Context) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, a := range s.p.Accounts() {
		if ctx.Err() != nil {
			return
		}
		s.refresh(ctx, a)
	}
}
func (s *service) refresh(ctx context.Context, a *cred.Account) bool {
	wallet, err := s.up.GetWallet(ctx, a)
	if err != nil {
		return false
	}
	next := &cred.Account{UID: a.UID, Nickname: a.Nickname, Email: a.Email, Plan: a.Plan, Session: a.Session,
		Wallet: cred.Wallet{Balance: wallet.Balance, DailyTokenBalance: wallet.DailyTokenBalance, DailyTokenLimit: wallet.DailyTokenLimit}}
	if next.SaveFile(s.dir) != nil {
		return false
	}
	s.p.Replace(next, nil)
	s.up.Models.Refresh(ctx, s.up, next)
	return true
}
func (s *service) accounts(w http.ResponseWriter, r *http.Request) {
	if r.Method == "GET" {
		reply(w, 200, map[string]any{"accounts": s.p.List()})
		return
	}
	if r.Method != "POST" {
		fail(w, 405, "method not allowed")
		return
	}
	var body struct {
		Cookie     string        `json:"cookie"`
		Name       string        `json:"name"`
		Credential *cred.Account `json:"credential"`
	}
	if json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&body) != nil {
		fail(w, 400, "请输入 Cookie 或 JSON 凭据")
		return
	}
	a := body.Credential
	if a == nil {
		a = &cred.Account{}
	}
	if body.Cookie != "" {
		a.Session.Cookie = body.Cookie
	}
	// Credentials may only be sent to the official service, never a host from an imported file.
	a.Session.Host = ""
	a.Session.UserAgent = ""
	a.Session.CSRF = ""
	if a.Session.Cookie == "" || strings.ContainsAny(a.Session.Cookie, "\r\n") {
		fail(w, 400, "Cookie 不能为空且不能包含换行")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()
	user, err := s.up.GetUser(ctx, a)
	if err != nil || !safeID.MatchString(user.ID) {
		fail(w, 400, "Cookie 校验失败，请重新登录 MonkeyCode 后复制 Cookie")
		return
	}
	a.UID = user.ID
	a.Nickname = user.Name
	a.Plan = user.Plan
	a.Email = user.Email
	if strings.TrimSpace(body.Name) != "" {
		a.Nickname = strings.TrimSpace(body.Name)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if a.SaveFile(s.dir) != nil {
		fail(w, 500, "账号保存失败")
		return
	}
	enabled := true
	s.p.Replace(a, &enabled)
	refreshed := s.refresh(ctx, a)
	reply(w, 200, map[string]any{"id": a.UID, "ok": true, "refreshed": refreshed})
}
func (s *service) account(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/admin/accounts/"), "/")
	if !safeID.MatchString(parts[0]) {
		fail(w, 404, "账号不存在")
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	var a *cred.Account
	for _, v := range s.p.Accounts() {
		if v.UID == parts[0] {
			a = v
			break
		}
	}
	if a == nil {
		fail(w, 404, "账号不存在")
		return
	}
	if len(parts) == 2 && parts[1] == "refresh" && r.Method == "POST" {
		ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
		defer cancel()
		if !s.refresh(ctx, a) {
			fail(w, 502, "额度刷新失败；Cookie 过期时请重新导入")
			return
		}
		reply(w, 200, map[string]bool{"ok": true})
		return
	}
	if len(parts) == 2 && parts[1] == "checkin" && r.Method == "POST" {
		ctx, cancel := context.WithTimeout(r.Context(), 90*time.Second)
		defer cancel()
		status, err := s.up.GetCheckinStatus(ctx, a)
		if err != nil {
			fail(w, 502, "签到状态查询失败；Cookie 过期时请重新登录")
			return
		}
		message := "今日已签到"
		if !status.CheckedIn {
			if err := s.up.DoCheckin(ctx, a); err != nil {
				fail(w, 502, "签到失败，请稍后重试")
				return
			}
			message = "签到成功"
		}
		refreshed := s.refresh(ctx, a)
		reply(w, 200, map[string]any{"ok": true, "id": a.UID, "message": message, "refreshed": refreshed})
		return
	}
	if len(parts) != 1 {
		fail(w, 404, "接口不存在")
		return
	}
	if r.Method == "DELETE" {
		if err := os.Remove(a.Path(s.dir)); err != nil && !os.IsNotExist(err) {
			fail(w, 500, "删除失败")
			return
		}
		s.p.Remove(a.UID)
	} else if r.Method == "PATCH" {
		var body struct {
			Name    *string `json:"name"`
			Enabled *bool   `json:"enabled"`
		}
		if json.NewDecoder(r.Body).Decode(&body) != nil {
			fail(w, 400, "参数无效")
			return
		}
		next := &cred.Account{UID: a.UID, Nickname: a.Nickname, Email: a.Email, Plan: a.Plan, Session: a.Session, Wallet: a.Wallet}
		if body.Name != nil {
			if strings.TrimSpace(*body.Name) == "" || len(*body.Name) > 240 {
				fail(w, 400, "备注无效")
				return
			}
			next.Nickname = strings.TrimSpace(*body.Name)
		}
		if next.SaveFile(s.dir) != nil {
			fail(w, 500, "保存失败")
			return
		}
		s.p.Replace(next, body.Enabled)
	} else {
		fail(w, 405, "method not allowed")
		return
	}
	reply(w, 200, map[string]bool{"ok": true})
}
