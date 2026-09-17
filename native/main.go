// The TRAE adapter runs inside the Python host. No HTTP server or subprocess.
package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"context"
	"encoding/json"
	"io"
	"monkeycode2api/adapter"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unsafe"

	"trae2api/internal/auth"
	"trae2api/internal/pool"
	"trae2api/internal/scheduler"
	"trae2api/internal/server"
	"trae2api/internal/upstream"
)

var handler http.Handler
var stop context.CancelFunc
var active sync.Map
var next atomic.Uint64

type event struct {
	Status  int         `json:"status,omitempty"`
	Headers http.Header `json:"headers,omitempty"`
	Data    []byte      `json:"data,omitempty"`
	Done    bool        `json:"done,omitempty"`
}
type call struct {
	ctx    context.Context
	cancel context.CancelFunc
	events chan event
}
type writer struct {
	c       *call
	headers http.Header
	sent    bool
}

func (w *writer) Header() http.Header { return w.headers }
func (w *writer) emit(e event) bool {
	select {
	case w.c.events <- e:
		return true
	case <-w.c.ctx.Done():
		return false
	}
}
func (w *writer) WriteHeader(code int) {
	if !w.sent {
		w.sent = true
		w.emit(event{Status: code, Headers: w.headers.Clone()})
	}
}
func (w *writer) Write(p []byte) (int, error) {
	w.WriteHeader(200)
	for start := 0; start < len(p); start += 32768 {
		end := start + 32768
		if end > len(p) {
			end = len(p)
		}
		if !w.emit(event{Data: append([]byte(nil), p[start:end]...)}) {
			return start, io.ErrClosedPipe
		}
	}
	return len(p), nil
}
func (w *writer) Flush() { w.WriteHeader(200) }

//export TraeInit
func TraeInit() *C.char {
	cfg, err := Load("")
	if err != nil {
		return C.CString(err.Error())
	}
	auths, err := auth.LoadDir(cfg.AuthDir)
	if err != nil {
		return C.CString(err.Error())
	}
	p := pool.New(cfg.StateFile)
	p.SyncToDir(auths)
	up := upstream.New()
	up.HTTP.Timeout = time.Duration(cfg.Upstream.TimeoutSeconds) * time.Second
	if tr, ok := up.StreamHTTP.Transport.(*http.Transport); ok {
		tr.ResponseHeaderTimeout = up.HTTP.Timeout
	}
	wc := upstream.DefaultWorkClientConfig()
	wc.Mode = upstream.WorkMode(cfg.WorkMode)
	if cfg.WorkHost != "" {
		wc.Host = cfg.WorkHost
	}
	wc.BridgeURL = cfg.WorkBridgeURL
	wc.BridgeToken = cfg.WorkBridgeToken
	work := upstream.NewWorkClient(wc)
	handler = server.NewHandler(server.Config{Pool: p, Upstream: up, WorkClient: work, WorkMode: wc.Mode, APIKey: cfg.APIKey, AuthDir: cfg.AuthDir, PlanCooldown: cfg.PlanCreditDur, SoftCooldown: cfg.SoftRateDur, ErrThreshold: cfg.Cooldown.ErrThresh, ErrCooldown: cfg.ErrCooldownDur, DefaultModel: cfg.DefaultModel, WorkBridgeURL: cfg.WorkBridgeURL, WorkBridgeToken: cfg.WorkBridgeToken})
	var ctx context.Context
	ctx, stop = context.WithCancel(context.Background())
	dataDir := os.Getenv("MANAGEMENT_DATA_DIR")
	if dataDir == "" {
		dataDir = "/data/management"
	}
	monkey, err := adapter.New(ctx, filepath.Join(dataDir, "monkeycode"))
	if err != nil {
		stop()
		return C.CString("MonkeyCode account storage could not be loaded")
	}
	traeHandler := handler
	handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/monkey/") {
			http.StripPrefix("/monkey", monkey).ServeHTTP(w, r)
			return
		}
		traeHandler.ServeHTTP(w, r)
	})
	sch := scheduler.New(scheduler.Config{Pool: p, Upstream: up, CheckinHour: cfg.Schedule.CheckinHour, RefreshHours: cfg.Schedule.RefreshHours, RefreshSkew: 24 * time.Hour})
	go sch.Run(ctx)
	return C.CString("")
}

//export TraeStart
func TraeStart(method, path, body, key *C.char) C.ulonglong {
	ctx, cancel := context.WithCancel(context.Background())
	c := &call{ctx: ctx, cancel: cancel, events: make(chan event, 8)}
	id := next.Add(1)
	active.Store(id, c)
	req, err := http.NewRequestWithContext(ctx, C.GoString(method), C.GoString(path), strings.NewReader(C.GoString(body)))
	if err != nil {
		c.events <- event{Status: 400}
		c.events <- event{Done: true}
		return C.ulonglong(id)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+C.GoString(key))
	go func() {
		w := &writer{c: c, headers: make(http.Header)}
		defer func() {
			if recover() != nil {
				w.WriteHeader(500)
			}
			w.WriteHeader(200)
			w.emit(event{Done: true})
		}()
		handler.ServeHTTP(w, req)
	}()
	return C.ulonglong(id)
}

//export TraeRead
func TraeRead(id C.ulonglong) *C.char {
	value, ok := active.Load(uint64(id))
	if !ok {
		return C.CString(`{"done":true}`)
	}
	c := value.(*call)
	var e event
	select {
	case e = <-c.events:
	case <-c.ctx.Done():
		e = event{Done: true}
	case <-time.After(time.Second):
		return C.CString(`{}`)
	}
	b, _ := json.Marshal(e)
	return C.CString(string(b))
}

//export TraeCancel
func TraeCancel(id C.ulonglong) {
	if value, ok := active.LoadAndDelete(uint64(id)); ok {
		value.(*call).cancel()
	}
}

//export TraeStop
func TraeStop() {
	if stop != nil {
		stop()
	}
	active.Range(func(k, v any) bool { v.(*call).cancel(); active.Delete(k); return true })
}

//export TraeFree
func TraeFree(p unsafe.Pointer) { C.free(p) }
func main()                     {}
