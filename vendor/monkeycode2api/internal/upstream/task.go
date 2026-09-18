package upstream

import (
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"

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
	if c.Models != nil {
		if id, ok := c.Models.ResolveForAccount(a.UID, modelID); ok {
			modelID = id
		} else if c.Models.HasAccount(a.UID) {
			return "", &apiError{Kind: ErrNotFound, msg: "model is not available for this MonkeyCode account: " + modelID}
		}
	}
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

// StopTask releases only the sandbox created by the current API request.
func (c *Client) StopTask(ctx context.Context, a *cred.Account, taskID string) error {
	return c.doJSON(ctx, a, http.MethodPut, EpTasksStop, map[string]string{"id": taskID}, nil)
}

// StreamTask 通过 WebSocket 拉取任务流，把 assistant 文本/工具事件推给 emit。
// 由于 WS 事件语法无法在无实机下 100% 敲定，这里做容错解析：
//   - 把每一帧都记进日志（便于排障）
//   - 从 round/message/assistant/delta/text 事件尽量抽取文本正文
//   - done/finish/error 触发 isDone 回调后返回
//
// 对话模式任务在无沙箱下更接近普通 LLM 对话，正文抽取最简单可靠。
func (c *Client) StreamTask(ctx context.Context, a *cred.Account, taskID string, emit func(text string, done bool) error) error {
	u, err := url.Parse(strings.TrimRight(c.Base, "/") + EpTasksStream)
	if err != nil {
		return err
	}
	if u.Scheme == "https" {
		u.Scheme = "wss"
	} else if u.Scheme == "http" {
		u.Scheme = "ws"
	}
	q := u.Query()
	q.Set("id", taskID)
	q.Set("mode", "attach") // Attach to the conversation started by CreateTask.
	u.RawQuery = q.Encode()
	wsURL := u.String()

	ws, err := c.dialWS(ctx, a, wsURL)
	if err != nil {
		return err
	}
	defer ws.Close()
	stopCancel := context.AfterFunc(ctx, func() { ws.conn.Close() })
	defer stopCancel()

	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		msgType, raw, err := ws.ReadMessage()
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return fmt.Errorf("ws read: %w", err)
		}
		if msgType == wsCloseMessage {
			code := uint16(0)
			if len(raw) >= 2 {
				code = binary.BigEndian.Uint16(raw[:2])
			}
			return fmt.Errorf("upstream websocket closed before task-ended (code=%d)", code)
		}
		if msgType != wsTextMessage {
			continue
		}
		text, done, err := parseTaskEvent(raw)
		if err != nil {
			return err
		}
		if text != "" || done {
			if err := emit(text, done); err != nil {
				return err
			}
		}
		if done {
			return nil
		}
	}
}

// Current MonkeyCode task events contain base64-encoded ACP JSON in data.
// Only agent_message_chunk is answer text; thoughts, user input and tools are
// separate events and must never be echoed as the model's answer.
func parseTaskEvent(raw []byte) (string, bool, error) {
	var ev struct {
		Type string          `json:"type"`
		Kind string          `json:"kind"`
		Data json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(raw, &ev); err != nil {
		return "", false, fmt.Errorf("invalid task event: %w", err)
	}
	switch ev.Type {
	case "task-running":
		if ev.Kind == "acp_ask_user_question" {
			return "", false, fmt.Errorf("upstream task requires an interactive reply")
		}
		if ev.Kind != "acp_event" {
			return "", false, nil
		}
		payload, err := decodeTaskData(ev.Data)
		if err != nil {
			return "", false, err
		}
		var event struct {
			Update struct {
				SessionUpdate string `json:"sessionUpdate"`
				Content       struct {
					Type string `json:"type"`
					Text string `json:"text"`
				} `json:"content"`
			} `json:"update"`
		}
		if err := json.Unmarshal(payload, &event); err != nil {
			return "", false, fmt.Errorf("invalid ACP event: %w", err)
		}
		if event.Update.SessionUpdate == "agent_message_chunk" && event.Update.Content.Type == "text" {
			return event.Update.Content.Text, false, nil
		}
	case "task-error", "error", "abort":
		payload, err := decodeTaskData(ev.Data)
		if err != nil {
			return "", false, err
		}
		message := taskEventError(payload)
		if message == "" {
			message = extractText(raw)
		}
		if message == "" {
			message = ev.Type
		}
		return "", false, &apiError{Kind: ErrUpstream, msg: "upstream task error: " + message}
	case "task-ended":
		payload, err := decodeTaskData(ev.Data)
		if err != nil {
			return "", false, err
		}
		var ended struct {
			ExitCode int    `json:"exit_code"`
			Message  string `json:"message"`
		}
		if len(payload) > 0 {
			if err := json.Unmarshal(payload, &ended); err != nil {
				return "", false, fmt.Errorf("invalid task-ended event: %w", err)
			}
		}
		if ended.ExitCode != 0 {
			return "", false, fmt.Errorf("upstream task exited with code=%d: %s", ended.ExitCode, ended.Message)
		}
		return "", true, nil
	case "done", "finish", "completed":
		return "", true, nil
	case "round", "message", "assistant", "delta", "text", "content", "delta_text":
		return extractText(raw), false, nil
	}
	return "", false, nil
}

func decodeTaskData(data json.RawMessage) ([]byte, error) {
	if len(data) == 0 || string(data) == "null" {
		return nil, nil
	}
	if data[0] != '"' {
		return data, nil
	}
	var encoded string
	if err := json.Unmarshal(data, &encoded); err != nil {
		return nil, err
	}
	if encoded == "" {
		return nil, nil
	}
	if json.Valid([]byte(encoded)) {
		return []byte(encoded), nil
	}
	decoded, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return nil, fmt.Errorf("invalid task event data encoding: %w", err)
	}
	return decoded, nil
}

func taskEventError(payload []byte) string {
	if len(payload) == 0 {
		return ""
	}
	var obj map[string]any
	if json.Unmarshal(payload, &obj) == nil {
		for _, key := range []string{"message", "error", "detail"} {
			if value, ok := obj[key].(string); ok && value != "" {
				return value
			}
			if value, ok := obj[key].(map[string]any); ok {
				if message, ok := value["message"].(string); ok {
					return message
				}
			}
		}
	}
	return truncate(string(payload), 2048)
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
