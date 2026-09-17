package upstream

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"

	"monkeycode2api/internal/cred"
)

// CliName 平台可用的 agent CLI 名。任务用 opencode 作为运行时 agent。
const CliNameOpencode = "opencode"

// 任务创建必填的镜像 / 主机。
// 实测：POST /api/v1/users/tasks 缺 image_id / host_id 会返回 code=400 参数错误。
// image：平台公开的 devbox 基础镜像（ghcr.1ms.run/chaitin/monkeycode-runner/devbox:bookworm）
//
//	—— 对应 GET /api/v1/users/images 里 remark=="devbox" 的公共镜像。
//
// host ：公共托管主机使用占位符 "public_host"，网关会解析到可用公共 host。
const (
	// PublicDevboxImageID 公共 devbox 镜像 UUID（平台公开镜像，可直接用）。
	PublicDevboxImageID = "2e214f06-79ba-4535-9ac1-89adc2d9c6cc"
	// PublicHost 公共托管主机占位符。
	PublicHost = "public_host"
)

// TaskType 任务模式。
type TaskType string

const (
	TaskTypeDevelop TaskType = "develop"
	TaskTypeDesign  TaskType = "design"
	TaskTypeChat    TaskType = "chat" // 独立对话（轻量，不建沙箱）
)

// CreateTaskRequest 创建任务的载荷（与 SPA v1UsersTasksCreate 一致）。
type CreateTaskRequest struct {
	Content  string         `json:"content"`
	CliName  string         `json:"cli_name"`
	ModelID  string         `json:"model_id"`
	ImageID  string         `json:"image_id"`
	HostID   string         `json:"host_id"`
	Repo     map[string]any `json:"repo,omitempty"`
	Resource map[string]any `json:"resource"`
	Extra    map[string]any `json:"extra,omitempty"`
	TaskType TaskType       `json:"task_type"`
}

// resolveModelID 把用户/前端传的模型名解析成平台模型 UUID。
// 优先级：动态目录 Models（运行时拉取）> 静态 FreeModelIDs 兜底 > 原样透传给上游。
func (c *Client) resolveModelID(name string) string {
	if c.Models != nil {
		if id, ok := c.Models.Resolve(name); ok {
			return id
		}
	}
	if id, ok := FreeModelIDs[name]; ok {
		return id
	}
	return name
}

// CreateTask 在账号上创建一个对话/开发任务，返回 task id。
// 上游会把 content 当作 agent 的需求。返回后即可通过 StreamTask
// 挂到 /api/v1/users/tasks/stream 拉取流式正文。
func (c *Client) CreateTask(ctx context.Context, a *cred.Account, content, modelID string, typ TaskType) (string, error) {
	req := &CreateTaskRequest{
		Content:  content,
		CliName:  CliNameOpencode,
		ModelID:  c.resolveModelID(modelID),
		ImageID:  PublicDevboxImageID,
		HostID:   PublicHost,
		TaskType: typ,
		Resource: map[string]any{
			"core":   2,
			"memory": 8 * 1024 * 1024 * 1024,
			"life":   7200,
		},
	}
	var out struct {
		ID string `json:"id"`
	}
	err := c.doJSON(ctx, a, "POST", EpTasks, req, &out)
	if err != nil {
		if ae, ok := err.(*apiError); ok {
			if ae.Kind == ErrAuth {
				return "", cred.ErrAuthInvalid
			}
			// 上游业务码 10811（及配额类）表示"先升级/额度不足"，归类为配额错误，
			// 额度不足：让账号池对它做长冷却，而不是计入通用错误计数。
			if quotaCode(ae.Code) {
				return "", &apiError{Kind: ErrQuota, Code: ae.Code, msg: err.Error()}
			}
			// 账号忙（已有任务在跑，如 10811）：瞬态，映射为 ErrBusy，
			// 让 pool 做短冷却/切号重试，而不是当成长期不可用。
			if busyCode(ae.Code) {
				return "", &apiError{Kind: ErrBusy, Code: ae.Code, msg: err.Error()}
			}
		}
		return "", err
	}
	if out.ID == "" {
		return "", &apiError{Kind: ErrUpstream, msg: "task create returned empty id"}
	}
	return out.ID, nil
}

// Cluster round 事件数据（用于转成 OpenAI chunk 的正文/工具调用）。
type streamChunk struct {
	Text     string
	ToolCall string
	Done     bool
}

// StreamTask 通过 WebSocket 拉取任务流，把 assistant 文本/工具事件推给 emit。
// 由于 WS 事件语法无法在无实机下 100% 敲定，这里做容错解析：
//   - 把每一帧都记进日志（便于排障）
//   - 从 round/message/assistant/delta/text 事件尽量抽取文本正文
//   - done/finish/error 触发 isDone 回调后返回
//
// 对话模式任务在无沙箱下更接近普通 LLM 对话，正文抽取最简单可靠。
func (c *Client) StreamTask(ctx context.Context, a *cred.Account, taskID string, emit func(text string, done bool) error) error {
	host := c.Base
	u, _ := url.Parse(host + EpTasksStream)
	q := u.Query()
	q.Set("id", taskID)
	q.Set("mode", "develop") // 网页里 mode=develop
	u.RawQuery = q.Encode()
	wsURL := u.String()

	ws, err := c.dialWS(ctx, a, wsURL)
	if err != nil {
		return err
	}
	defer ws.Close()

	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		msgType, raw, err := ws.ReadMessage()
		if err != nil {
			return fmt.Errorf("ws read: %w", err)
		}
		if msgType != wsTextMessage {
			continue
		}
		var ev map[string]any
		if err := json.Unmarshal(raw, &ev); err != nil {
			continue
		}
		typ, _ := ev["type"].(string)
		switch typ {
		case "done", "finish", "completed", "error", "abort":
			return emit("", true)
		case "round", "message", "assistant", "delta", "text", "content", "delta_text":
			if s := extractText(raw); s != "" {
				if err := emit(s, false); err != nil {
					return err
				}
			}
		}
	}
}

// extractText 从各种可能的正文事件负载中抽取字符串正文。
func extractText(raw json.RawMessage) string {
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return ""
	}
	for _, k := range []string{"content", "text", "delta", "message"} {
		if v, ok := m[k]; ok {
			switch t := v.(type) {
			case string:
				return t
			case map[string]any:
				for _, k2 := range []string{"content", "text", "delta"} {
					if s, ok := t[k2].(string); ok {
						return s
					}
				}
			}
		}
	}
	return ""
}
