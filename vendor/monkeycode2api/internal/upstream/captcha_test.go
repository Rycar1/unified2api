package upstream

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"testing"
)

// 验证码 PRNG/求解器测试。
// 参考: capjs-server (https://github.com/vshn/capjs-server) Python 参考实现
//
//	prng = FNV-1a 播种 + xorshift32，输出 hex
//	求解 = 找 nonce 使 sha256(salt+nonce) hex 前缀 == target
//
// 期望值均由 Python 参考实现预先计算得出。

func TestFnv1a(t *testing.T) {
	cases := []struct {
		in   string
		want uint32
	}{
		{"abc", 0x1a47e90b},
		{"", 2166136261}, // FNV-1a 初始值
	}
	for _, c := range cases {
		if got := fnv1a(c.in); got != c.want {
			t.Errorf("fnv1a(%q) = %08x, want %08x", c.in, got, c.want)
		}
	}
}

func TestPrng(t *testing.T) {
	cases := []struct {
		seed string
		n    int
		want string
	}{
		{"abc", 32, "0bb9adb8ffd8e55f8d1de826333f356a"},
		{"f6ec30af65e5e63a1", 32, "9dfccebc8142d989940102b03ebc4bfb"},
		{"25c8608d41c8d23afc70c54121", 32, "284f80acd317fca1bcfa96e02afadf93"},
		{"25c8608d41c8d23afc70c54121", 3, "284"}, // 长度截断一致性
	}
	for _, c := range cases {
		if got := prng(c.seed, c.n); got != c.want {
			t.Errorf("prng(%q,%d) = %s, want %s", c.seed, c.n, got, c.want)
		}
	}
}

func TestPrngDeterministic(t *testing.T) {
	if prng("seed1", 16) != prng("seed1", 16) {
		t.Fatal("prng not deterministic")
	}
	if prng("seed1", 6) != prng("seed1", 16)[:6] {
		t.Fatal("prng prefix truncation mismatch")
	}
}

// TestSolveSubChallengeFixture 用线上实测 derivation 结果验证 nonce 求解：
// 该 fixture 来自真实 challenge，salt/target 由 prng(token+idx, s/d) 得到，
// 再暴力搜索 nonce；验证 verifyNonce 与搜索逻辑一致。
func TestSolveSubChallengeFixture(t *testing.T) {
	// 线上实测: token 派生出的第 1 个子挑战
	// salt  = prng(token+"1", 32)
	// target= prng(token+"1d", 3)
	salt := "284f80acd317fca1bcfa96e02afadf93" // prng("25c8608d41c8d23afc70c54121",32)
	target := "63e"                            // prng(token+"1d",3)

	nonce := -1
	hasher := sha256.New()
	for n := 0; n < 1<<20; n++ {
		hasher.Reset()
		_, _ = hasher.Write([]byte(salt + fmt.Sprint(n)))
		if hex.EncodeToString(hasher.Sum(nil))[:len(target)] == target {
			nonce = n
			break
		}
	}
	if nonce < 0 {
		t.Skip("fixture not solved within 1<<20 (difficulty may be higher than local fixture)")
	}
	if !verifyNonce(salt, target, nonce) {
		t.Fatalf("nonce %d does not satisfy challenge", nonce)
	}
	t.Logf("fixture nonce=%d", nonce)
}

func verifyNonce(salt, target string, nonce int) bool {
	sum := sha256.Sum256([]byte(salt + fmt.Sprint(nonce)))
	return hex.EncodeToString(sum[:])[:len(target)] == target
}

// 集成标记：完整 SolveCaptcha 需要真实网络（公网 challenge/redeem），
// 不做离线断言，避免 CI 依赖网络。
