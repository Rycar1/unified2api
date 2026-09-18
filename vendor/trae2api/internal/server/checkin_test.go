package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"trae2api/internal/auth"
	"trae2api/internal/pool"
	"trae2api/internal/upstream"
)

func TestBalanceQueryUpdatesAccountList(t *testing.T) {
	remaining := int64(0)
	failed := false
	up := newFakeUpstream(t, func(string) (int, string, bool) {
		if failed {
			return http.StatusBadGateway, `{"error":"temporarily unavailable"}`, false
		}
		return http.StatusOK, fmt.Sprintf(`{"user_entitlement_pack_list":[{"entitlement_base_info":{"quota":{"credits_limit":2000}},"usage":{"credits_amount":%d}}]}`, 2000-remaining), false
	})
	p := pool.New("")
	p.Add(&auth.Auth{UID: "u1", AccessToken: "test-token", ExpiresAt: 9999999999})
	h := NewHandler(Config{Pool: p, Upstream: up, WorkMode: upstream.WorkModeDisabled})
	assertList := func(want *int64) {
		t.Helper()
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest("GET", "/admin/api/accounts", nil))
		var body struct {
			Accounts []map[string]json.RawMessage `json:"accounts"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil || len(body.Accounts) != 1 {
			t.Fatalf("invalid account list: %s (%v)", rec.Body, err)
		}
		got, present := body.Accounts[0]["remaining"]
		expect := "null"
		if want != nil {
			expect = fmt.Sprint(*want)
		}
		if !present || string(got) != expect {
			t.Fatalf("remaining=%s (present=%v), want %s", got, present, expect)
		}
	}
	assertList(nil)
	for _, value := range []int64{0, 2000} {
		remaining = value
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest("POST", "/admin/api/accounts/u1/balance", nil))
		var body map[string]json.RawMessage
		if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil || rec.Code != http.StatusOK {
			t.Fatalf("balance query failed: %d %s (%v)", rec.Code, rec.Body, err)
		}
		if string(body["remaining"]) != fmt.Sprint(value) {
			t.Fatalf("unexpected balance response: %s", rec.Body)
		}
		assertList(&value)
	}
	failed = true
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("POST", "/admin/api/accounts/u1/balance", nil))
	if rec.Code != http.StatusBadGateway {
		t.Fatalf("query failure status=%d", rec.Code)
	}
	assertList(&remaining)
}

func TestCheckinIncludesZeroRemaining(t *testing.T) {
	up := newFakeUpstream(t, func(string) (int, string, bool) {
		return http.StatusOK, `{"checked_in":true,"enable":true,"user_entitlement_pack_list":[]}`, false
	})
	p := pool.New("")
	p.Add(&auth.Auth{UID: "u1", AccessToken: "test-token", RefreshToken: "test-refresh"})
	h := NewHandler(Config{Pool: p, Upstream: up, WorkMode: upstream.WorkModeDisabled})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("POST", "/admin/api/accounts/u1/checkin", nil))
	var body map[string]json.RawMessage
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil || rec.Code != http.StatusOK {
		t.Fatalf("checkin failed: %d %s (%v)", rec.Code, rec.Body, err)
	}
	if string(body["remaining"]) != "0" {
		t.Fatalf("known zero omitted from checkin: %s", rec.Body)
	}
	status, _ := p.Status("u1")
	if status.Remaining == nil || *status.Remaining != 0 {
		t.Fatalf("checkin did not update balance: %+v", status)
	}
}

func TestBatchCheckinSkipsDisabledAccounts(t *testing.T) {
	up := newFakeUpstream(t, func(string) (int, string, bool) {
		t.Fatal("disabled account reached the upstream")
		return http.StatusInternalServerError, `{}`, false
	})
	p := pool.New("")
	p.Add(&auth.Auth{UID: "u1", AccessToken: "test-token", RefreshToken: "test-refresh"})
	p.SetEnabled("u1", false, "test")
	h := NewHandler(Config{Pool: p, Upstream: up, WorkMode: upstream.WorkModeDisabled})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("POST", "/admin/api/checkin", nil))
	var body struct {
		Results []checkinResult `json:"results"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil || rec.Code != http.StatusOK || len(body.Results) != 0 {
		t.Fatalf("disabled account was not skipped: %d %s", rec.Code, rec.Body)
	}
}
