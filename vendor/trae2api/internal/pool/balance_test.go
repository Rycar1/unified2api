package pool

import (
	"os"
	"path/filepath"
	"testing"

	"trae2api/internal/auth"
)

func TestRemainingDistinguishesUnknownAndZeroAcrossReload(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	p := New(path)
	p.Add(&auth.Auth{UID: "unknown"})
	p.Add(&auth.Auth{UID: "zero"})
	p.Add(&auth.Auth{UID: "positive"})
	p.ReenableIfCredits("zero", 0)
	p.SetCredits("positive", 123)
	for _, current := range []*Pool{p, New(path)} {
		unknown, _ := current.Status("unknown")
		zero, _ := current.Status("zero")
		positive, _ := current.Status("positive")
		if unknown.Remaining != nil {
			t.Fatalf("unqueried account has a balance: %v", *unknown.Remaining)
		}
		if zero.Remaining == nil || *zero.Remaining != 0 {
			t.Fatalf("queried zero was lost: %+v", zero)
		}
		if positive.Remaining == nil || *positive.Remaining != 123 {
			t.Fatalf("queried positive was lost: %+v", positive)
		}
		// Callers cannot mutate pool state through a returned snapshot.
		*positive.Remaining = 999
		fresh, _ := current.Status("positive")
		if fresh.Remaining == nil || *fresh.Remaining != 123 {
			t.Fatal("status exposes mutable pool balance")
		}
	}
}

func TestLegacyBalancesMigrateWithoutInventingZero(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	if err := os.WriteFile(path, []byte(`{"accounts":{"unknown":{"credits":0},"known":{"credits":250}}}`), 0600); err != nil {
		t.Fatal(err)
	}
	p := New(path)
	unknown, _ := p.Status("unknown")
	known, _ := p.Status("known")
	if unknown.Remaining != nil || known.Remaining == nil || *known.Remaining != 250 {
		t.Fatalf("legacy balance migration failed: unknown=%+v known=%+v", unknown, known)
	}
}

func TestBalanceRefreshPreservesManualDisable(t *testing.T) {
	p := New("")
	p.Add(&auth.Auth{UID: "u1"})
	p.SetEnabled("u1", false, "manually disabled")
	p.ReenableIfCredits("u1", 500)
	status, _ := p.Status("u1")
	if status.Enabled || status.Reason != "manually disabled" || p.Pick() != nil {
		t.Fatalf("balance refresh changed manual disable: %+v", status)
	}
	if status.Remaining == nil || *status.Remaining != 500 {
		t.Fatalf("disabled account balance was not updated: %+v", status)
	}
}
