package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func fixture(t *testing.T, handler http.HandlerFunc) *gateway {
	t.Helper()
	upstream := httptest.NewServer(handler)
	t.Cleanup(upstream.Close)
	u, _ := url.Parse(upstream.URL)
	return &gateway{key: "public", backends: map[string]backend{"trae": {u, "private"}, "codebuddy": {u, "private"}}, client: &http.Client{Timeout: time.Second, Transport: http.DefaultTransport}}
}
func TestRouteAndPreservePayload(t *testing.T) {
	g := fixture(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer private" || r.Header.Get("X-Api-Key") != "" || r.Header.Get("Cookie") != "" {
			t.Error("credential isolation failed")
		}
		var body map[string]json.RawMessage
		json.NewDecoder(r.Body).Decode(&body)
		if string(body["model"]) != `"glm-5.2"` || string(body["big"]) != "9007199254740993" {
			t.Errorf("payload changed: %v", body)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: hello\n\ndata: [DONE]\n\n")
	})
	for _, provider := range []string{"trae", "codebuddy"} {
		req := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(`{"model":"`+provider+`/glm-5.2","big":9007199254740993,"stream":true}`))
		req.Header.Set("Authorization", "Bearer public")
		req.Header.Set("X-Api-Key", "public")
		req.Header.Set("Cookie", "secret=value")
		w := httptest.NewRecorder()
		g.ServeHTTP(w, req)
		if w.Code != 200 || !strings.Contains(w.Body.String(), "[DONE]") {
			t.Fatal(w.Code, w.Body.String())
		}
	}
}
func TestValidation(t *testing.T) {
	g := fixture(t, func(w http.ResponseWriter, r *http.Request) { t.Error("unexpected upstream request") })
	cases := []struct {
		path, body, key string
		status          int
	}{
		{"/v1/chat/completions", `{"model":"trae/x"}`, "", 401},
		{"/v1/chat/completions", `{"model":"x"}`, "public", 400},
		{"/v1/chat/completions", `{"model":"unknown/x"}`, "public", 400},
		{"/v1/messages", `{"model":"trae/x"}`, "public", 400},
		{"/v1/chat/completions", `null`, "public", 400},
		{"/v1/chat/completions", `{`, "public", 400},
		{"/admin", `{}`, "public", 404},
	}
	for _, c := range cases {
		r := httptest.NewRequest("POST", c.path, strings.NewReader(c.body))
		r.Header.Set("Authorization", "Bearer "+c.key)
		w := httptest.NewRecorder()
		g.ServeHTTP(w, r)
		if w.Code != c.status {
			t.Errorf("%s %s: %d", c.path, c.body, w.Code)
		}
	}
}
func TestModels(t *testing.T) {
	g := fixture(t, func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, `{"data":[{"id":"same","object":"model"}]}`)
	})
	r := httptest.NewRequest("GET", "/v1/models", nil)
	r.Header.Set("Authorization", "Bearer public")
	w := httptest.NewRecorder()
	g.ServeHTTP(w, r)
	if w.Code != 200 || !strings.Contains(w.Body.String(), "trae/same") || !strings.Contains(w.Body.String(), "codebuddy/same") {
		t.Fatal(w.Body.String())
	}
}
func TestStreamingFlush(t *testing.T) {
	release := make(chan struct{})
	defer close(release)
	g := fixture(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		<-release
		io.WriteString(w, "data: [DONE]\n\n")
	})
	s := httptest.NewServer(g)
	defer s.Close()
	req, _ := http.NewRequest("POST", s.URL+"/v1/chat/completions", strings.NewReader(`{"model":"trae/x","stream":true}`))
	req.Header.Set("Authorization", "Bearer public")
	client := &http.Client{Timeout: time.Second}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	first := make([]byte, len("data: first\n\n"))
	if _, err = io.ReadFull(resp.Body, first); err != nil {
		t.Fatal("stream buffered", err)
	}
	// Release before server cleanup.
	release <- struct{}{}
}
