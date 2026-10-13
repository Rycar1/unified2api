package pool

import (
	"monkeycode2api/internal/cred"
	"sync"
	"testing"
)

func TestReservedNeverExceedsAccountLimit(t *testing.T) {
	p := New("")
	p.Add(&cred.Account{UID: "synthetic"})
	var successes int
	var mu sync.Mutex
	var wg sync.WaitGroup
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if p.PickReserved(nil, 2) != nil {
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
	if p.PickReserved(nil, 2) == nil {
		t.Fatal("slot not released")
	}
}
