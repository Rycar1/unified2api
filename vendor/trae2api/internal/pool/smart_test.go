package pool

import (
	"sync"
	"testing"
	"trae2api/internal/auth"
)

func TestReservedNeverExceedsAccountLimit(t *testing.T) {
	p := New("")
	p.Add(&auth.Auth{UID: "synthetic"})
	var successes int
	var mu sync.Mutex
	var wg sync.WaitGroup
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if p.PickReserved(nil, 2, false) != nil {
				mu.Lock()
				successes++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if successes != 2 {
		t.Fatalf("admitted %d requests, expected 2", successes)
	}
	p.Release("synthetic")
	if p.PickReserved(nil, 2, false) == nil {
		t.Fatal("slot not released")
	}
	p.SetCredits("synthetic", 0)
	p.Release("synthetic")
	p.Release("synthetic")
	if p.PickReserved(nil, 2, false) != nil {
		t.Fatal("known exhausted account admitted")
	}
}

func TestPinnedReservationNeverFallsBack(t *testing.T) {
	p := New("")
	p.Add(&auth.Auth{UID: "a"})
	p.Add(&auth.Auth{UID: "b"})
	for _, work := range []bool{false, true} {
		got := p.PickReserved(nil, 1, work, "b")
		if got == nil || got.UID != "b" {
			t.Fatal("did not select pinned account")
		}
		if p.PickReserved(nil, 1, work, "b") != nil {
			t.Fatal("busy pin fell back to another account")
		}
		p.Release("b")
		if p.PickReserved(map[string]bool{"b": true}, 1, work, "b") != nil {
			t.Fatal("failed pin fell back")
		}
	}
	if p.PickReserved(nil, 1, false, "missing") != nil {
		t.Fatal("unknown pin fell back")
	}
}
