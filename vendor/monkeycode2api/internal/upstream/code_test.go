package upstream

import "testing"

func TestResolveModelIDFallback(t *testing.T) {
	cli := &Client{} // Models=nil → 走静态 FreeModelIDs 兜底
	cases := []struct {
		in, want string
	}{
		{"kimi-k2.5", "03dc8f96-8d9a-4094-a2b3-c4128b3a78a4"},
		{"minimax-m2.5", "8e22c508-97ad-490b-b38e-113faeeca275"},
		{"qwen3.5-plus", "d937e77c-6941-48cf-913c-ec51cce138bb"},
		{"unknown-model-name", "unknown-model-name"}, // 未识别原样透传
	}
	for _, tc := range cases {
		if got := cli.resolveModelID(tc.in); got != tc.want {
			t.Errorf("resolveModelID(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestResolveModelIDCatalog(t *testing.T) {
	// 目录优先：静态 FreeModelIDs 里没有的名字，若目录有，应解析成目录里的 UUID。
	cat := NewModelCatalog()
	cat.models = []*Model{
		{ID: "X-111", Name: "deepseek-v4-flash", IsHidden: true},
		{ID: "X-222", Name: "deepseek-v4-flash", IsHidden: false, AccessLevel: "basic"},
	}
	cat.by = map[string]int{"deepseek-v4-flash": 1} // 可见的那个
	cli := &Client{Models: cat}
	if got, _ := cli.Models.Resolve("deepseek-v4-flash"); got != "X-222" {
		t.Fatalf("catalog resolve = %q, want X-222", got)
	}
	if got := cli.resolveModelID("deepseek-v4-flash"); got != "X-222" {
		t.Fatalf("client resolve = %q, want X-222", got)
	}
	// 目录中没有的仍走静态兜底
	if got := cli.resolveModelID("kimi-k2.5"); got != "03dc8f96-8d9a-4094-a2b3-c4128b3a78a4" {
		t.Fatalf("fallback resolve = %q", got)
	}
}

func TestBusyVsQuotaCode(t *testing.T) {
	// 10811 = 账号忙（有任务在跑），不是额度不足
	if !busyCode(10811) {
		t.Error("10811 should be busy")
	}
	if quotaCode(10811) {
		t.Error("10811 must NOT be quota")
	}
	// 4002 = 额度/升级
	if !quotaCode(4002) {
		t.Error("4002 should be quota")
	}
	if busyCode(4002) {
		t.Error("4002 must NOT be busy")
	}
}
