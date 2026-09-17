// Package server OpenAI 兼容 HTTP 路由：/v1/chat/completions（流式+非流式）、
// /v1/models、/status、/healthz。鉴权用 Bearer API Key。
package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"monkeycode2api/internal/pool"
	"monkeycode2api/internal/upstream"
)

// Config handler 依赖。
type Config struct {
	Pool         *pool.Pool
	Upstream     *upstream.Client
	Models       *upstream.ModelCatalog
	APIKey       string
	HardCooldown time.Duration
	SoftCooldown time.Duration
	ErrThreshold int
	ErrCooldown  time.Duration
}

// Handler 路由。
type Handler struct {
	cfg Config
	mux *http.ServeMux
}

// NewHandler 构建。
func NewHandler(cfg Config) *Handler {
	if cfg.HardCooldown <= 0 {
		cfg.HardCooldown = 12 * time.Hour
	}
	if cfg.SoftCooldown <= 0 {
		cfg.SoftCooldown = 60 * time.Second
	}
	if cfg.ErrThreshold <= 0 {
		cfg.ErrThreshold = 5
	}
	if cfg.ErrCooldown <= 0 {
		cfg.ErrCooldown = 10 * time.Minute
	}
	h := &Handler{cfg: cfg, mux: http.NewServeMux()}
	h.mux.HandleFunc("POST /v1/chat/completions", h.withAuth(h.chatCompletions))
	h.mux.HandleFunc("GET /v1/models", h.withAuth(h.models))
	h.mux.HandleFunc("GET /status", h.withAuth(h.status))
	h.mux.HandleFunc("GET /healthz", h.healthz)
	return h
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	h.mux.ServeHTTP(w, r)
}

func (h *Handler) withAuth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if h.cfg.APIKey != "" {
			authz := r.Header.Get("Authorization")
			if !strings.HasPrefix(authz, "Bearer ") || strings.TrimPrefix(authz, "Bearer ") != h.cfg.APIKey {
				writeOpenAIError(w, http.StatusUnauthorized, "invalid_api_key", "missing or invalid API key")
				return
			}
		}
		next(w, r)
	}
}

func (h *Handler) healthz(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok"))
}

func (h *Handler) status(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"accounts": h.cfg.Pool.List()})
}

func (h *Handler) models(w http.ResponseWriter, r *http.Request) {
	var data []map[string]any
	if h.cfg.Models != nil {
		for _, m := range h.cfg.Models.List() {
			data = append(data, map[string]any{
				"id":            m.ID,
				"object":        "model",
				"created":       1753600000,
				"owned_by":      "monkeycode",
				"tier":          firstNonEmpty(m.Tier, m.AccessLevel, "basic"),
				"name":          m.Name,
				"input_price":   m.InputPrice,
				"support_image": m.SupportImage,
				"access_level":  m.AccessLevel,
			})
		}
	}
	if len(data) == 0 {
		// 目录为空（尚未拉到）时，退回到已知基础档别名，保证 /v1/models 可用。
		seen := map[string]bool{}
		for _, n := range upstream.FreeModels {
			if seen[n] {
				continue
			}
			seen[n] = true
			data = append(data, map[string]any{
				"id": n, "object": "model", "created": 1753600000,
				"owned_by": "monkeycode", "tier": "basic",
			})
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"object": "list", "data": data})
}

// chatRequest OpenAI 兼容请求。
type chatRequest struct {
	Model    string           `json:"model"`
	Messages []map[string]any `json:"messages"`
	Stream   bool             `json:"stream"`
}

func (h *Handler) chatCompletions(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 8<<20))
	if err != nil {
		writeOpenAIError(w, http.StatusBadRequest, "invalid_request", "read body: "+err.Error())
		return
	}
	var req chatRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeOpenAIError(w, http.StatusBadRequest, "invalid_request", "parse json: "+err.Error())
		return
	}
	if req.Model == "" {
		writeOpenAIError(w, http.StatusBadRequest, "invalid_request", "model is required")
		return
	}
	if len(req.Messages) == 0 {
		writeOpenAIError(w, http.StatusBadRequest, "invalid_request", "messages is required")
		return
	}
	if h.cfg.Pool.Pick() == nil {
		writeOpenAIError(w, http.StatusServiceUnavailable, "no_healthy_account", "all accounts unavailable (cooling/disabled)")
		return
	}

	tried := map[string]bool{}
	var lastErr error
	for i := 0; i < 3; i++ {
		acct := h.cfg.Pool.PickExcluding(tried)
		if acct == nil {
			break
		}
		tried[acct.UID] = true

		content := upstream.BuildContent(req.Messages)
		cs, err := h.cfg.Upstream.Chat(r.Context(), acct, req.Model, content, upstream.TaskTypeDevelop)
		if err != nil {
			lastErr = err
			if kind, ok := upstream.Classify(err); ok {
				switch kind {
				case upstream.ErrAuth:
					h.cfg.Pool.Disable(acct.UID, "auth invalid (relogin needed)")
				case upstream.ErrQuota:
					h.cfg.Pool.Cooldown(acct.UID, pool.CoolHard, h.cfg.HardCooldown, "额度不足(等隔日刷新)")
				case upstream.ErrRateLimit:
					h.cfg.Pool.Cooldown(acct.UID, pool.CoolSoft, h.cfg.SoftCooldown, "429 rate limit")
				case upstream.ErrBusy:
					// 账号在跑别的任务：短冷却，让其它账号顶上；避免当成长久不可用
					h.cfg.Pool.Cooldown(acct.UID, pool.CoolSoft, 120*time.Second, "busy(running task), retry later")
				default:
					h.cfg.Pool.NoteError(acct.UID, h.cfg.ErrThreshold, h.cfg.ErrCooldown)
				}
			} else {
				h.cfg.Pool.NoteError(acct.UID, h.cfg.ErrThreshold, h.cfg.ErrCooldown)
			}
			continue
		}

		if req.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			w.Header().Set("Cache-Control", "no-cache")
			w.Header().Set("Connection", "keep-alive")
			w.Header().Set("X-Accel-Buffering", "no")
			fl, _ := w.(http.Flusher)
			flush := func() {
				if fl != nil {
					fl.Flush()
				}
			}
			streamAsOpenAI(w, cs, req.Model, flush)
			return
		}
		text, err := upstream.AggregateStream(cs)
		cs.Close()
		if err != nil {
			writeOpenAIError(w, http.StatusBadGateway, "upstream_parse", err.Error())
			return
		}
		resp := map[string]any{
			"id":      "chatcmpl-monkeycode",
			"object":  "chat.completion",
			"model":   req.Model,
			"created": time.Now().Unix(),
			"choices": []any{
				map[string]any{
					"index":         0,
					"message":       map[string]any{"role": "assistant", "content": text},
					"finish_reason": "stop",
				},
			},
		}
		writeJSON(w, http.StatusOK, resp)
		return
	}
	msg := "all accounts unavailable (cooling/disabled)"
	if lastErr != nil {
		msg += ": " + lastErr.Error()
	}
	writeOpenAIError(w, http.StatusServiceUnavailable, "no_healthy_account", msg)
}

// streamAsOpenAI 把上游正文流转成 SSE OpenAI chunk。
func streamAsOpenAI(w http.ResponseWriter, cs *upstream.ChunkStream, model string, flush func()) {
	// 首帧 role chunk
	writeSSE(w, map[string]any{
		"id":      "chatcmpl-monkeycode",
		"object":  "chat.completion.chunk",
		"model":   model,
		"created": time.Now().Unix(),
		"choices": []any{
			map[string]any{"index": 0, "delta": map[string]any{"role": "assistant"}, "finish_reason": nil},
		},
	})
	flush()

	for {
		text, done, err := cs.Next()
		if err != nil {
			writeSSE(w, map[string]any{
				"id": "chatcmpl-monkeycode", "object": "chat.completion.chunk", "model": model,
				"choices": []any{map[string]any{"delta": map[string]any{}, "finish_reason": "stop"}},
			})
			flush()
			_, _ = w.Write([]byte("data: [DONE]\n\n"))
			flush()
			return
		}
		if done {
			writeSSE(w, map[string]any{
				"id": "chatcmpl-monkeycode", "object": "chat.completion.chunk", "model": model,
				"choices": []any{map[string]any{"delta": map[string]any{}, "finish_reason": "stop"}},
			})
			flush()
			_, _ = w.Write([]byte("data: [DONE]\n\n"))
			flush()
			return
		}
		writeSSE(w, map[string]any{
			"id": "chatcmpl-monkeycode", "object": "chat.completion.chunk", "model": model,
			"choices": []any{map[string]any{"index": 0, "delta": map[string]any{"content": text}, "finish_reason": nil}},
		})
		flush()
	}
}

// writeSSE 写一个 SSE 事件。
func writeSSE(w http.ResponseWriter, v any) {
	raw, _ := json.Marshal(v)
	_, _ = fmt.Fprintf(w, "data: %s\n\n", raw)
}

// writeJSON JSON 响应。
func writeJSON(w http.ResponseWriter, status int, v any) {
	raw, _ := json.Marshal(v)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(raw)
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

func writeOpenAIError(w http.ResponseWriter, status int, code, msg string) {
	writeJSON(w, status, map[string]any{
		"error": map[string]any{"message": msg, "type": "api_error", "code": code},
	})
}
