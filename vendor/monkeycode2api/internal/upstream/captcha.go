package upstream

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
	"sync"
)

// Cap.js/长亭自研 PoW 验证码客户端。
//
// 流程（与 SPA 内嵌 cap.js widget 一致，已对线上 /api/v1/public/captcha/ 实测验证）：
//
//	1. POST  /api/v1/public/captcha/challenge          → {challenge:{c,s,d}, token, expires}
//	2. 生成 c 个子挑战：salt_i  = prng(token+i,  s)     （FNV-1a + xorshift32 确定性 PRNG，hex 输出）
//	                     target_i= prng(token+i+"d", d)
//	3. 对每个子挑战求 nonce：sha256(salt_i + nonce) 的 hex 前缀 == target_i
//	4. POST  /api/v1/public/captcha/redeem             → {success, token, expires}
//	   返回的 token 即签到用的 captcha_token。
//
// 难度 d 为 target 的 hex 字符数（线上常见 d=3 → 12bit，预期 ~2k 次/子挑战）。

const (
	EpCaptchaChallenge = "/api/v1/public/captcha/challenge"
	EpCaptchaRedeem    = "/api/v1/public/captcha/redeem"
)

const fnvOffset32 = 2166136261
const fnvMask = 0xFFFFFFFF

// fnv1a FNV-1a 32bit，与 cap.js 实现一致。
func fnv1a(s string) uint32 {
	h := uint32(fnvOffset32)
	for i := 0; i < len(s); i++ {
		h ^= uint32(s[i])
		h = (h + (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24)) & fnvMask
	}
	return h
}

// prng 确定性 PRNG：FNV-1a 播种 + xorshift32，输出 length 长度的 hex 字符串。
// 必须与 Cap.js widget 的 PRNG 完全一致。
func prng(seed string, length int) string {
	state := fnv1a(seed)
	var sb strings.Builder
	for sb.Len() < length {
		state ^= (state << 13) & fnvMask
		state ^= state >> 17
		state ^= (state << 5) & fnvMask
		state &= fnvMask
		sb.WriteString(fmt.Sprintf("%08x", state))
	}
	return sb.String()[:length]
}

// captchaChallenge 上游 challenge 响应。
type captchaChallenge struct {
	Challenge struct {
		C int `json:"c"`
		S int `json:"s"`
		D int `json:"d"`
	} `json:"challenge"`
	Token   string `json:"token"`
	Expires int64  `json:"expires"`
}

// captchaRedeem redeem 响应。
type captchaRedeem struct {
	Success bool   `json:"success"`
	Token   string `json:"token"`
	Expires int64  `json:"expires"`
}

// maxNoncePerSub 单个子挑战的 nonce 搜索上限；线上 d=3 时远用不到。
// 上限设为 2^26（约 6700 万次），d 即使升到 6 也够用。
const maxNoncePerSub = 1 << 26

// solveSubChallenge 求单个子挑战的 nonce。
func solveSubChallenge(token string, idx, sLen, dLen int) (int, error) {
	salt := prng(token+fmt.Sprint(idx), sLen)
	target := prng(token+fmt.Sprint(idx)+"d", dLen)
	hasher := sha256.New()
	for n := 0; n < maxNoncePerSub; n++ {
		hasher.Reset()
		_, _ = hasher.Write([]byte(salt + fmt.Sprint(n)))
		sum := hasher.Sum(nil)
		if hex.EncodeToString(sum)[:dLen] == target {
			return n, nil
		}
	}
	return 0, fmt.Errorf("sub-challenge %d unsolved in %d attempts (d=%d)", idx, maxNoncePerSub, dLen)
}

// SolveCaptcha 完成完整验证码流程，返回可用于签到的 captcha_token。
// ctx 可取消；多个子挑战并行求解。
func (c *Client) SolveCaptcha(ctx context.Context) (string, error) {
	var ch captchaChallenge
	if err := c.doJSON(ctx, nil, "POST", EpCaptchaChallenge, nil, &ch); err != nil {
		return "", fmt.Errorf("captcha challenge: %w", err)
	}
	cfg := ch.Challenge
	if cfg.C <= 0 || cfg.S <= 0 || cfg.D <= 0 {
		return "", fmt.Errorf("captcha challenge invalid config: %+v", cfg)
	}

	solutions := make([]int, cfg.C)
	sem := make(chan struct{}, 16)
	var wg sync.WaitGroup
	var mu sync.Mutex
	var firstErr error
	for i := 1; i <= cfg.C; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			select {
			case sem <- struct{}{}:
				defer func() { <-sem }()
			case <-ctx.Done():
				return
			}
			n, err := solveSubChallenge(ch.Token, idx, cfg.S, cfg.D)
			if err != nil {
				mu.Lock()
				if firstErr == nil {
					firstErr = err
				}
				mu.Unlock()
				return
			}
			mu.Lock()
			solutions[idx-1] = n
			mu.Unlock()
		}(i)
	}
	wg.Wait()
	if firstErr != nil {
		return "", firstErr
	}

	var rd captchaRedeem
	if err := c.doJSON(ctx, nil, "POST", EpCaptchaRedeem, map[string]any{
		"token":     ch.Token,
		"solutions": solutions,
	}, &rd); err != nil {
		return "", fmt.Errorf("captcha redeem: %w", err)
	}
	if !rd.Success {
		return "", fmt.Errorf("captcha redeem failed: success=false")
	}
	if rd.Token == "" {
		return "", fmt.Errorf("captcha redeem returned empty token")
	}
	return rd.Token, nil
}
