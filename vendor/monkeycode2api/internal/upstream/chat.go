package upstream

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"

	"monkeycode2api/internal/cred"
)

// ChatError 供 pool 判定的上游对话错误。
type ChatError struct {
	Kind string // ErrAuth | ErrQuota | ErrRateLimit | ErrNotFound | ErrBusy | ErrUpstream
	Msg  string
}

func (e *ChatError) Error() string { return e.Msg }

// RouteError 判定上游错误类别，便于 pool 做冷却/禁用。
func Classify(err error) (string, bool) {
	var ce *ChatError
	if errors.As(err, &ce) {
		return ce.Kind, true
	}
	var ae *apiError
	if errors.As(err, &ae) {
		return ae.Kind, true
	}
	return "", false
}

// ChatRequest 是 2api 交给上游的"对话意图"。保留供上层使用。
type ChatRequest struct {
	Model    string // 上游模型 id（已解析）
	Messages []map[string]any
}

// BuildContent 把 messages 列表转成 agent 需求正文。
func BuildContent(messages []map[string]any) string {
	var sb strings.Builder
	for i, m := range messages {
		role, _ := m["role"].(string)
		var parts []string
		// 支持 string 或数组(content 含 image/text)
		switch c := m["content"].(type) {
		case string:
			parts = append(parts, c)
		case []any:
			for _, item := range c {
				if mm, ok := item.(map[string]any); ok {
					if t, ok := mm["text"].(string); ok {
						parts = append(parts, t)
					}
				}
			}
		}
		if role == "" {
			continue
		}
		prefix := "user"
		if i == 0 {
			prefix = "system"
		} else if role == "user" {
			prefix = "user"
		} else {
			prefix = "assistant"
		}
		sb.WriteString(fmt.Sprintf("## %s\n%s\n\n", prefix, strings.Join(parts, "\n")))
	}
	return sb.String()
}

// chunkStream 由 Go 消费者逐 chunk 读取。
type ChunkStream struct {
	ch   chan string
	errc chan error
	done chan struct{}
	once sync.Once
	buf  strings.Builder
}

func newChunkStream() *ChunkStream {
	return &ChunkStream{
		ch:   make(chan string),
		errc: make(chan error, 1),
		done: make(chan struct{}),
	}
}

// pushErr 只写入一次错误
func (s *ChunkStream) pushErr(err error) {
	select {
	case s.errc <- err:
	default:
	}
}

func (s *ChunkStream) Close() { s.once.Do(func() { close(s.ch) }) }

// Next 返回下一个正文 chunk；done==true 表示流结束。
func (s *ChunkStream) Next() (text string, done bool, err error) {
	select {
	case t, ok := <-s.ch:
		if !ok { // 已关闭 → 结束或错误
			if s.closedErr() != nil {
				return "", true, s.closedErr()
			}
			return "", true, nil
		}
		return t, false, nil
	case e := <-s.errc:
		return "", true, e
	}
}

// 跳转发
func (s *ChunkStream) closedErr() error {
	select {
	case e := <-s.errc:
		return e
	default:
		return nil
	}
}

// Chat 在给定账号上跑一次对话：创建任务 → 拉流 → 返回正文流。
// done() 用法同参考：Close 后流结束返回。
func (c *Client) Chat(ctx context.Context, acct *cred.Account, modelID, content string, typ TaskType) (*ChunkStream, error) {
	taskID, err := c.CreateTask(ctx, acct, content, modelID, typ)
	if err != nil {
		return nil, wrapChatErr(err)
	}

	cs := newChunkStream()
	go func() {
		defer cs.Close()
		err := c.StreamTask(ctx, acct, taskID, func(text string, done bool) error {
			if done {
				return nil
			}
			if text != "" {
				cs.buf.WriteString(text)
				select {
				case cs.ch <- text:
				case <-ctx.Done():
					return ctx.Err()
				}
			}
			return nil
		})
		if err != nil && ctx.Err() == nil {
			cs.pushErr(err)
		}
	}()
	return cs, nil
}

// AggregateStream 消费整个正文流，返回拼接文本。
func AggregateStream(cs *ChunkStream) (string, error) {
	var sb strings.Builder
	for {
		t, done, err := cs.Next()
		if err != nil {
			return sb.String(), err
		}
		if done {
			return sb.String(), nil
		}
		sb.WriteString(t)
	}
}

// wrapChatErr 把上游错误转成 ChatError，便于 Pool 判断。
func wrapChatErr(err error) error {
	if err == nil {
		return nil
	}
	if ce, ok := err.(*ChatError); ok {
		return ce
	}
	if ae, ok := err.(*apiError); ok {
		return &ChatError{Kind: ae.Kind, Msg: ae.Error()}
	}
	if errors.Is(err, cred.ErrAuthInvalid) {
		return &ChatError{Kind: ErrAuth, Msg: err.Error()}
	}
	return &ChatError{Kind: ErrUpstream, Msg: err.Error()}
}
