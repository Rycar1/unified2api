package server

import (
	"net/http"

	"trae2api/internal/upstream"
)

type checkinResult struct {
	UID       string `json:"uid"`
	OK        bool   `json:"ok"`
	Message   string `json:"message"`
	Remaining int64  `json:"remaining,omitempty"`
}

func (h *Handler) checkinAccount(uid string) checkinResult {
	result := checkinResult{UID: uid}
	for _, status := range h.cfg.Pool.List() {
		if status.UID == uid && status.Disabled {
			result.Message = "账号已停用"
			return result
		}
	}

	a := h.cfg.Pool.AuthByUID(uid)
	if a == nil || a.RefreshTokenValue() == "" {
		result.Message = "账号缺少有效凭据"
		return result
	}

	checked, _, enabled, err := h.cfg.Upstream.CheckinStatus(a)
	message := "今日已签到"
	if err != nil {
		result.Message = "签到状态查询失败"
		return result
	}
	if !checked && !enabled {
		result.OK = true
		result.Message = "当前没有可领取的签到活动"
		return result
	}
	if !checked {
		if err := h.cfg.Upstream.CheckinClaim(a); err != nil {
			message = "签到失败"
			if ue, ok := err.(*upstream.Error); ok && ue.Kind == upstream.ErrSessionDead {
				message = "登录已失效，请重新授权"
			}
			result.Message = message
			return result
		}
		message = "签到成功"
	}

	remaining, err := h.cfg.Upstream.UserEntUsage(a)
	if err != nil {
		result.OK = true
		result.Message = message + "，额度暂未刷新"
		return result
	}
	h.cfg.Pool.ReenableIfCredits(uid, remaining)
	result.OK = true
	result.Message = message
	result.Remaining = remaining
	return result
}

// adminCheckin signs in every enabled TRAE account and refreshes its credits.
// One bad credential does not prevent the remaining accounts from running.
func (h *Handler) adminCheckin(w http.ResponseWriter, r *http.Request) {
	results := make([]checkinResult, 0)
	for _, status := range h.cfg.Pool.List() {
		if !status.Disabled {
			results = append(results, h.checkinAccount(status.UID))
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"results": results})
}

func (h *Handler) adminCheckinAccount(w http.ResponseWriter, r *http.Request) {
	result := h.checkinAccount(r.PathValue("uid"))
	status := http.StatusOK
	if !result.OK {
		status = http.StatusBadGateway
	}
	writeJSON(w, status, result)
}

func (h *Handler) adminAccountBalance(w http.ResponseWriter, r *http.Request) {
	uid := r.PathValue("uid")
	a := h.cfg.Pool.AuthByUID(uid)
	if a == nil {
		writeOpenAIError(w, http.StatusNotFound, "account_not_found", "账号不存在")
		return
	}
	remaining, limit, used, packs, err := h.cfg.Upstream.EntUsage(a)
	if err != nil {
		writeOpenAIError(w, http.StatusBadGateway, "balance_failed", "余额查询失败，请检查登录状态")
		return
	}
	h.cfg.Pool.ReenableIfCredits(uid, remaining)
	writeJSON(w, http.StatusOK, map[string]any{
		"uid": uid, "ok": true, "message": "余额已更新",
		"remaining": remaining, "limit": limit, "used": used, "packs": packs,
	})
}
