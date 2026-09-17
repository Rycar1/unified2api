package upstream

import (
	"context"
	"sort"
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
	mu     sync.RWMutex
	models []*Model       // 当前全部（含隐藏）
	by     map[string]int // 可见模型 name → 在 models 中的下标
}

func NewModelCatalog() *ModelCatalog {
	return &ModelCatalog{by: make(map[string]int)}
}

// Refresh 用账号拉取一次模型目录并重建索引；失败返回错误但不破坏旧目录。
// 同名覆盖策略：优取可见、basic 档。
func (c *ModelCatalog) Refresh(ctx context.Context, cli *Client, acct *cred.Account) error {
	models, err := cli.GetModels(ctx, acct)
	if err != nil {
		return err
	}
	by := map[string]int{}
	sort.SliceStable(models, func(i, j int) bool {
		return pickBest(models[i], models[j])
	})
	for i, m := range models {
		if m.IsHidden {
			continue
		}
		if _, ok := by[m.Name]; !ok {
			by[m.Name] = i
		}
	}
	c.mu.Lock()
	c.models = models
	c.by = by
	c.mu.Unlock()
	return nil
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
	for _, m := range c.models {
		if m.IsHidden {
			continue
		}
		cp := *m
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
