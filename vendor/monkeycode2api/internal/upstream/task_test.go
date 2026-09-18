package upstream

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"monkeycode2api/internal/cred"
)

func taskEvent(typ, kind string, data any) []byte {
	b, _ := json.Marshal(data)
	raw, _ := json.Marshal(map[string]any{"type": typ, "kind": kind, "data": base64.StdEncoding.EncodeToString(b)})
	return raw
}

func TestTaskEvents(t *testing.T) {
	for _, tc := range []struct {
		name string
		raw  []byte
		text string
		done bool
		err  string
	}{
		{"answer", taskEvent("task-running", "acp_event", map[string]any{"update": map[string]any{"sessionUpdate": "agent_message_chunk", "content": map[string]string{"type": "text", "text": "你好"}}}), "你好", false, ""},
		{"thought excluded", taskEvent("task-running", "acp_event", map[string]any{"update": map[string]any{"sessionUpdate": "agent_thought_chunk", "content": map[string]string{"type": "text", "text": "private reasoning"}}}), "", false, ""},
		{"user excluded", taskEvent("user-input", "", map[string]string{"content": "user prompt"}), "", false, ""},
		{"finished", taskEvent("task-ended", "turn_end", map[string]any{"exit_code": 0, "message": ""}), "", true, ""},
		{"upstream failure", taskEvent("task-error", "", map[string]string{"message": "model endpoint unavailable"}), "", false, "model endpoint unavailable"},
		{"exit failure", taskEvent("task-ended", "turn_end", map[string]any{"exit_code": 2, "message": "runner failed"}), "", false, "runner failed"},
		{"interactive", taskEvent("task-running", "acp_ask_user_question", map[string]any{}), "", false, "interactive reply"},
		{"bad payload", []byte(`{"type":"task-running","kind":"acp_event","data":"!!!"}`), "", false, "encoding"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			text, done, err := parseTaskEvent(tc.raw)
			if text != tc.text || done != tc.done || (tc.err == "" && err != nil) || (tc.err != "" && (err == nil || !strings.Contains(err.Error(), tc.err))) {
				t.Fatalf("got (%q, %v, %v)", text, done, err)
			}
		})
	}
}

func writeServerFrame(w io.Writer, opcode byte, final bool, data []byte) {
	first := opcode
	if final {
		first |= 0x80
	}
	header := []byte{first, byte(len(data))}
	if len(data) > 125 {
		header = []byte{first, 126, byte(len(data) >> 8), byte(len(data))}
	}
	_, _ = w.Write(append(header, data...))
}

func TestChatTaskStreamAndCleanup(t *testing.T) {
	for _, outcome := range []string{"success", "task-error", "cancel"} {
		t.Run(outcome, func(t *testing.T) {
			var stopped atomic.Bool
			connected := make(chan struct{})
			stopReceived := make(chan struct{})
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case EpTasks:
					var body CreateTaskRequest
					_ = json.NewDecoder(r.Body).Decode(&body)
					if body.ModelID != "model-uuid" {
						t.Errorf("wrong model ID %q", body.ModelID)
					}
					fmt.Fprint(w, `{"code":0,"data":{"id":"owned-task"}}`)
				case EpTasksStream:
					if r.URL.Query().Get("mode") != "attach" || r.URL.Query().Get("id") != "owned-task" {
						t.Errorf("wrong stream query")
					}
					if r.Header.Get("Origin") == "" || r.Header.Get("Cookie") != "session=test" {
						t.Errorf("missing websocket headers")
					}
					conn, brw, err := w.(http.Hijacker).Hijack()
					if err != nil {
						t.Error(err)
						return
					}
					defer conn.Close()
					fmt.Fprintf(brw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n", wsAccept(r.Header.Get("Sec-WebSocket-Key")))
					_ = brw.Flush()
					close(connected)
					if outcome == "cancel" {
						_, _ = io.Copy(io.Discard, conn)
						return
					}
					if outcome == "task-error" {
						writeServerFrame(conn, wsTextMessage, true, taskEvent("task-error", "", map[string]string{"message": "model refused request"}))
						return
					}
					// A fragmented text message with interleaved ping is legal RFC6455.
					answer := taskEvent("task-running", "acp_event", map[string]any{"update": map[string]any{"sessionUpdate": "agent_message_chunk", "content": map[string]string{"type": "text", "text": "OK"}}})
					writeServerFrame(conn, wsTextMessage, false, answer[:25])
					writeServerFrame(conn, wsPingMessage, true, []byte("ping"))
					_ = conn.SetReadDeadline(time.Now().Add(time.Second))
					two := make([]byte, 2)
					if _, err := io.ReadFull(brw, two); err != nil {
						t.Error(err)
						return
					}
					if two[0] != 0x8a || two[1]&0x80 == 0 {
						t.Error("client pong must be masked")
					}
					maskPayload := make([]byte, 4+int(two[1]&0x7f))
					_, _ = io.ReadFull(brw, maskPayload)
					writeServerFrame(conn, 0, true, answer[25:])
					writeServerFrame(conn, wsTextMessage, true, taskEvent("task-ended", "turn_end", map[string]any{"exit_code": 0}))
				case EpTasksStop:
					var body map[string]string
					_ = json.NewDecoder(r.Body).Decode(&body)
					if r.Method != "PUT" || body["id"] != "owned-task" {
						t.Error("cleanup must stop only this API task")
					}
					stopped.Store(true)
					close(stopReceived)
					fmt.Fprint(w, `{"code":0}`)
				default:
					t.Errorf("unexpected endpoint %s", r.URL.Path)
				}
			}))
			defer srv.Close()
			cli := New()
			cli.Base = srv.URL
			acct := &cred.Account{UID: "test", Session: cred.Session{Cookie: "session=test"}}
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			cs, err := cli.Chat(ctx, acct, "model-uuid", "reply OK", TaskTypeDevelop)
			if err != nil {
				t.Fatal(err)
			}
			defer cs.Close()
			if outcome == "cancel" {
				<-connected
				cancel()
			}
			answer, err := AggregateStream(cs)
			if outcome == "success" && (answer != "OK" || err != nil) {
				t.Fatalf("got %q / %v", answer, err)
			}
			if outcome == "task-error" && (err == nil || !strings.Contains(err.Error(), "model refused request")) {
				t.Fatalf("lost upstream error: %v", err)
			}
			if outcome == "cancel" && (err == nil || !strings.Contains(err.Error(), "context canceled")) {
				t.Fatalf("lost cancellation: %v", err)
			}
			select {
			case <-stopReceived:
			case <-time.After(time.Second):
				t.Fatal("task was not cleaned up")
			}
			if !stopped.Load() {
				t.Fatal("task not stopped")
			}
		})
	}
}

func TestClosedChunkStreamPreservesError(t *testing.T) {
	cs := newChunkStream()
	want := errors.New("upstream failed")
	cs.pushErr(want)
	close(cs.ch)
	_, done, err := cs.Next()
	if !done || !errors.Is(err, want) {
		t.Fatalf("lost closed stream error: %v", err)
	}
}

func TestWSRejectsHTTPSInsteadOfDialingPlainHTTP(t *testing.T) {
	_, err := New().dialWS(context.Background(), &cred.Account{}, "https://invalid.example/stream")
	if err == nil || !strings.Contains(err.Error(), "scheme") {
		t.Fatal(err)
	}
}

func TestCloseFrameReportsCode(t *testing.T) {
	frame := []byte{0x88, 2, 0, 0}
	binary.BigEndian.PutUint16(frame[2:], 1008)
	w := &wsConn{r: bufio.NewReader(strings.NewReader(string(frame)))}
	typ, data, err := w.ReadMessage()
	if err != nil || typ != wsCloseMessage || binary.BigEndian.Uint16(data) != 1008 {
		t.Fatal("close frame lost")
	}
}
