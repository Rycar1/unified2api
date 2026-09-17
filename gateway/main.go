package main

import (
	"bytes"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
	"time"
)

type backend struct {
	URL *url.URL
	Key string
}
type gateway struct {
	key      string
	backends map[string]backend
	client   *http.Client
}

func fail(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"message": message, "type": "gateway_error"}})
}

func (g *gateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/healthz" && r.Method == "GET" {
		w.Write([]byte("ok"))
		return
	}
	token := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	if token == "" {
		token = r.Header.Get("x-api-key")
	}
	if g.key == "" || subtle.ConstantTimeCompare([]byte(token), []byte(g.key)) != 1 {
		fail(w, 401, "Invalid API key")
		return
	}
	if r.URL.Path == "/v1/models" && r.Method == "GET" {
		g.models(w, r)
		return
	}
	if r.Method != "POST" || (r.URL.Path != "/v1/chat/completions" && r.URL.Path != "/v1/responses" && r.URL.Path != "/v1/messages") {
		fail(w, 404, "Unknown endpoint")
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 8<<20))
	if err != nil {
		fail(w, 413, "Request body exceeds limit or cannot be read")
		return
	}
	var payload map[string]json.RawMessage
	if json.Unmarshal(body, &payload) != nil || payload == nil {
		fail(w, 400, "Expected a JSON object")
		return
	}
	var model string
	if json.Unmarshal(payload["model"], &model) != nil {
		fail(w, 400, "model is required")
		return
	}
	provider, actual, found := strings.Cut(model, "/")
	b, ok := g.backends[provider]
	if !found || !ok || strings.TrimSpace(actual) == "" {
		fail(w, 400, "Use trae/<model> or codebuddy/<model>")
		return
	}
	if provider == "trae" && r.URL.Path != "/v1/chat/completions" {
		fail(w, 400, "TRAE supports /v1/chat/completions only")
		return
	}
	payload["model"], _ = json.Marshal(actual)
	body, _ = json.Marshal(payload)
	r.Body = io.NopCloser(bytes.NewReader(body))
	r.ContentLength = int64(len(body))
	proxy := &httputil.ReverseProxy{
		Rewrite: func(p *httputil.ProxyRequest) {
			p.SetURL(b.URL)
			p.Out.Header.Del("Authorization")
			p.Out.Header.Del("X-Api-Key")
			p.Out.Header.Del("Cookie")
			p.Out.Header.Set("Authorization", "Bearer "+b.Key)
			p.Out.Header.Set("Content-Type", "application/json")
			p.Out.Header.Del("Accept-Encoding")
		},
		Transport:     g.client.Transport,
		FlushInterval: -1,
		ErrorHandler:  func(w http.ResponseWriter, r *http.Request, err error) { fail(w, 502, "Provider unavailable") },
	}
	// Never retry a generation: upstream may already have consumed it.
	proxy.ServeHTTP(w, r)
}

func (g *gateway) models(w http.ResponseWriter, r *http.Request) {
	all := []map[string]any{}
	unavailable := []string{}
	for _, name := range []string{"trae", "codebuddy"} {
		b := g.backends[name]
		req, _ := http.NewRequestWithContext(r.Context(), "GET", b.URL.String()+"/v1/models", nil)
		req.Header.Set("Authorization", "Bearer "+b.Key)
		resp, err := g.client.Do(req)
		if err != nil {
			unavailable = append(unavailable, name)
			continue
		}
		var data struct {
			Data []map[string]any `json:"data"`
		}
		err = json.NewDecoder(io.LimitReader(resp.Body, 2<<20)).Decode(&data)
		resp.Body.Close()
		if err != nil || resp.StatusCode != 200 {
			unavailable = append(unavailable, name)
			continue
		}
		for _, m := range data.Data {
			if id, ok := m["id"].(string); ok {
				m["id"] = name + "/" + id
				m["owned_by"] = name
				all = append(all, m)
			}
		}
	}
	if len(unavailable) == 2 {
		fail(w, 502, "Both providers unavailable")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{"object": "list", "data": all, "unavailable_providers": unavailable})
}

func env(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}
func main() {
	key := os.Getenv("UNIFIED_API_KEY")
	if key == "" {
		log.Fatal("UNIFIED_API_KEY is required")
	}
	g := &gateway{key: key, backends: map[string]backend{}, client: &http.Client{Timeout: 15 * time.Second, Transport: &http.Transport{Proxy: http.ProxyFromEnvironment, ResponseHeaderTimeout: 120 * time.Second, IdleConnTimeout: 90 * time.Second, MaxIdleConns: 100}}}
	for _, name := range []string{"trae", "codebuddy"} {
		prefix := strings.ToUpper(name)
		u, err := url.Parse(os.Getenv(prefix + "_URL"))
		if err != nil || u == nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
			log.Fatal(fmt.Sprintf("%s_URL must be an HTTP URL", prefix))
		}
		g.backends[name] = backend{u, env(prefix+"_KEY", key)}
	}
	srv := &http.Server{Addr: env("LISTEN", ":8080"), Handler: g, ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second, IdleTimeout: 90 * time.Second}
	log.Printf("Unified API listening on %s", srv.Addr)
	log.Fatal(srv.ListenAndServe())
}
