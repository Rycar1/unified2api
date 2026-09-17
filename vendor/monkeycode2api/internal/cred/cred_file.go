package cred

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"
)

// authFilePrefix auth 文件命名前缀。
const authFilePrefix = "monkeycode-"

// LoadDir 读取 auths 目录下所有 monkeycode-*.json，返回账号列表。
func LoadDir(dir string) ([]*Account, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var out []*Account
	for _, e := range entries {
		if e.IsDir() || !stringsHasPrefix(e.Name(), authFilePrefix) {
			continue
		}
		full := filepath.Join(dir, e.Name())
		a, err := LoadFile(full)
		if err != nil {
			return nil, fmt.Errorf("load %s: %w", full, err)
		}
		if a.UID == "" {
			// 从文件名兜底取 uid
			a.UID = stringsTrimSuffix(e.Name()[len(authFilePrefix):], filepath.Ext(e.Name()))
		}
		out = append(out, a)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].UID < out[j].UID })
	return out, nil
}

// LoadFile 读取单个 auth 文件。
func LoadFile(path string) (*Account, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var a Account
	if err := json.Unmarshal(raw, &a); err != nil {
		return nil, err
	}
	return &a, nil
}

// Path 返回该账号对应的 auth 文件路径。
func (a *Account) Path(dir string) string {
	return filepath.Join(dir, fmt.Sprintf("%s%s.json", authFilePrefix, a.UID))
}

// SaveFile 原子写回 auth 文件。
func (a *Account) SaveFile(dir string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	a.Updated = time.Now()
	raw, err := json.MarshalIndent(a, "", "  ")
	if err != nil {
		return err
	}
	tmp := filepath.Join(dir, ".tmp-"+a.UID+"-"+fmt.Sprint(time.Now().UnixNano()))
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, a.Path(dir))
}

// NeedsInstrumented 简单占位：是否有效判断由上层 wallet 校验决定。
func (a *Account) HasCookie() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.Session.Cookie != ""
}

// CookieHeader 返回可直接付给上游请求的 Cookie 原文（含必要的 Host 处理）。
func (a *Account) CookieHeader() string {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.Session.Cookie
}

// UpdateWallet 更新钱包快照，并在已变更时返回 true。
func (a *Account) UpdateWallet(w Wallet) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	if w == a.Wallet {
		return false
	}
	a.Wallet = w
	return true
}

func stringsHasPrefix(s, p string) bool { return len(s) >= len(p) && s[:len(p)] == p }
func stringsTrimSuffix(s, suf string) string {
	if stringsHasSuffix(s, suf) {
		return s[:len(s)-len(suf)]
	}
	return s
}
func stringsHasSuffix(s, suf string) bool { return len(s) >= len(suf) && s[len(s)-len(suf):] == suf }
