package adapter

import (
	"io"
	"monkeycode2api/internal/cred"
	"monkeycode2api/internal/pool"
	"monkeycode2api/internal/upstream"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

type transport func(*http.Request) (*http.Response, error)

func (f transport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func TestAccountLifecycle(t *testing.T) {
	dir := t.TempDir()
	s := &service{dir: dir, p: pool.New(filepath.Join(dir, "state.json")), up: upstream.New()}
	s.up.Models = upstream.NewModelCatalog()
	s.up.HTTP.Transport = transport(func(r *http.Request) (*http.Response, error) {
		if r.Header.Get("Cookie") != "session=secret" {
			t.Error("cookie not forwarded")
		}
		body := `{"code":0,"data":{"user":{"id":"user-1","name":"User"},"teams":[]}}`
		if r.URL.Path != "/api/v1/users/status" && r.URL.Path != upstream.EpWallet && r.URL.Path != upstream.EpModelsAvail {
			t.Fatal("unexpected endpoint")
		}
		switch r.URL.Path {
		case upstream.EpWallet:
			body = `{"code":0,"data":{"balance":12000,"daily_token_balance":500}}`
		case upstream.EpModelsAvail:
			body = `{"code":0,"data":[{"id":"uuid","name":"test-model"}]}`
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}, nil
	})
	call := func(method, path, body string) *httptest.ResponseRecorder {
		t.Helper()
		w := httptest.NewRecorder()
		r := httptest.NewRequest(method, path, strings.NewReader(body))
		if path == "/admin/accounts" {
			s.accounts(w, r)
		} else {
			s.account(w, r)
		}
		if w.Code != 200 {
			t.Fatalf("%s: %d %s", path, w.Code, w.Body.String())
		}
		return w
	}
	call("POST", "/admin/accounts", `{"cookie":"session=secret","name":"first"}`)
	if len(s.p.Accounts()) != 1 || s.p.List()[0].DailyTokenBal != 500 {
		t.Fatal("account not loaded")
	}
	if strings.Contains(call("GET", "/admin/accounts", "").Body.String(), "secret") {
		t.Fatal("credential leaked")
	}
	call("PATCH", "/admin/accounts/user-1", `{"enabled":false,"name":"renamed"}`)
	if s.p.Pick() != nil {
		t.Fatal("disabled account selected")
	}
	saved, err := cred.LoadDir(dir)
	if err != nil || saved[0].Nickname != "renamed" {
		t.Fatal("persistence failed")
	}
	call("POST", "/admin/accounts", `{"cookie":"session=secret","name":"updated"}`)
	if len(s.p.Accounts()) != 1 || s.p.Pick() == nil || s.p.Pick().Nickname != "updated" {
		t.Fatal("reimport failed")
	}
	call("DELETE", "/admin/accounts/user-1", "")
	saved, err = cred.LoadDir(dir)
	if err != nil || len(saved) != 0 || s.p.Pick() != nil {
		t.Fatal("delete failed")
	}
}
