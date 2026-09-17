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

// adminCheckin signs in every enabled TRAE account and refreshes its credits.
// One bad credential does not prevent the remaining accounts from running.
func (h *Handler) adminCheckin(w http.ResponseWriter, r *http.Request) {
	results := make([]checkinResult, 0)
	for _, status := range h.cfg.Pool.List() {
		if status.Disabled {
			continue
		}
		a := h.cfg.Pool.AuthByUID(status.UID)
		if a == nil || a.RefreshTokenValue() == "" {
			results = append(results, checkinResult{UID: status.UID, Message: "账号缺少有效凭据"})
			continue
		}

		checked, _, enabled, err := h.cfg.Upstream.CheckinStatus(a)
		message := "今日已签到"
		if err != nil {
			results = append(results, checkinResult{UID: status.UID, Message: "签到状态查询失败"})
			continue
		}
		if !checked && !enabled {
			results = append(results, checkinResult{UID: status.UID, OK: true, Message: "当前没有可领取的签到活动"})
			continue
		}
		if !checked {
			if err := h.cfg.Upstream.CheckinClaim(a); err != nil {
				message = "签到失败"
				if ue, ok := err.(*upstream.Error); ok && ue.Kind == upstream.ErrSessionDead {
					message = "登录已失效，请重新授权"
				}
				results = append(results, checkinResult{UID: status.UID, Message: message})
				continue
			}
			message = "签到成功"
		}

		remaining, err := h.cfg.Upstream.UserEntUsage(a)
		if err != nil {
			results = append(results, checkinResult{UID: status.UID, OK: true, Message: message + "，额度暂未刷新"})
			continue
		}
		h.cfg.Pool.ReenableIfCredits(status.UID, remaining)
		results = append(results, checkinResult{UID: status.UID, OK: true, Message: message, Remaining: remaining})
	}
	writeJSON(w, http.StatusOK, map[string]any{"results": results})
}
