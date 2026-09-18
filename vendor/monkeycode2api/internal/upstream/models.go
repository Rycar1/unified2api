package upstream

import (
	"context"
	"sort"
	"strings"
	"sync"

	"monkeycode2api/internal/cred"
)

// ModelCatalog 动态模型目录：从 /api/v1/users/models/available 拉取。
//
// 平台可用模型与订阅/额度相关、会随时间变化，运行时拉取比硬编码更可靠：
//   - /v1/models 返回目录里所有可见（非隐藏）模型；
//   - 创建任务时用目录把"模型名 → 平台 UUID"解析出来（model_id 要 UUID）。
//
// 用任意一个账号即可拉取（它们是该账号的可用模型）。
type ModelCatalog struct {
	mu       sync.RWMutex
	models   []*Model       // 当前全部（含隐藏）
	by       map[string]int // 可见模型 name → 在 models 中的下标
	accounts map[string][]*Model
}

func NewModelCatalog() *ModelCatalog {
	return &ModelCatalog{by: make(map[string]int), accounts: make(map[string][]*Model)}
}

// Refresh 用账号拉取一次模型目录并重建索引；失败返回错误但不破坏旧目录。
// 同名覆盖策略：优取可见、basic 档。
func (c *ModelCatalog) Refresh(ctx context.Context, cli *Client, acct *cred.Account) error {
	models, err := cli.GetModels(ctx, acct)
	if err != nil {
		return err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.accounts[acct.UID] = models
	c.rebuild()
	return nil
}

func (c *ModelCatalog) rebuild() {
	var models []*Model
	for _, accountModels := range c.accounts {
		models = append(models, accountModels...)
	}
	by := map[string]int{}
	sort.SliceStable(models, func(i, j int) bool {
		return pickBest(models[i], models[j])
	})
	for i, m := range models {
		if m == nil || m.IsHidden || m.ID == "" || strings.TrimSpace(m.Name) == "" {
			continue
		}
		name := strings.TrimSpace(m.Name)
		if _, ok := by[name]; !ok {
			by[name] = i
		}
	}
	c.models = models
	c.by = by
}

func (c *ModelCatalog) RemoveAccount(uid string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.accounts, uid)
	c.rebuild()
}

func (c *ModelCatalog) HasAccount(uid string) bool {
	c.mu.RLock()
	defer c.mu.RUnlock()
	_, ok := c.accounts[uid]
	return ok
}

// Each account can have different custom models/permissions. Resolve against
// its own catalog, so another account's refresh cannot replace its model UUID.
func (c *ModelCatalog) ResolveForAccount(uid, name string) (string, bool) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	var best *Model
	for _, m := range c.accounts[uid] {
		if m == nil || m.IsHidden || m.ID == "" {
			continue
		}
		if m.ID == name {
			return m.ID, true
		} // Existing clients may use UUIDs.
		if strings.TrimSpace(m.Name) == strings.TrimSpace(name) && (best == nil || pickBest(m, best)) {
			best = m
		}
	}
	if best != nil {
		return best.ID, true
	}
	return name, false
}

// Resolve 把用户请求的模型名解析成平台模型 UUID；未命中返回原样。
func (c *ModelCatalog) Resolve(name string) (string, bool) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	if i, ok := c.by[name]; ok {
		return c.models[i].ID, true
	}
	return name, false
}

// List 返回目录中所有可见模型（按名称排序）。
func (c *ModelCatalog) List() []*Model {
	c.mu.RLock()
	defer c.mu.RUnlock()
	var out []*Model
	for _, i := range c.by {
		m := c.models[i]
		cp := *m
		cp.Name = strings.TrimSpace(cp.Name)
		out = append(out, &cp)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// Len 返回可见模型数。
func (c *ModelCatalog) Len() int {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return len(c.by)
}

// pickBest 稳定排序比较器：可见优先，其次 basic 档。
func pickBest(a, b *Model) bool {
	if a == nil || b == nil {
		return a != nil
	}
	if a.IsHidden != b.IsHidden {
		return !a.IsHidden
	}
	if a.AccessLevel != b.AccessLevel {
		if a.AccessLevel == "basic" && b.AccessLevel != "basic" {
			return true
		}
		if b.AccessLevel == "basic" && a.AccessLevel != "basic" {
			return false
		}
	}
	return a.ID < b.ID
}
