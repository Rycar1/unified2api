package pool

import (
	"monkeycode2api/internal/cred"
	"time"
)

// Replace swaps immutable credential snapshots while preserving runtime cooldowns.
func (p *Pool) Replace(a *cred.Account, enabled *bool) {
	p.mu.Lock()
	if e, ok := p.byUID[a.UID]; ok {
		e.c = a
		if enabled != nil {
			e.disabled = !*enabled
			if *enabled {
				e.reason = ""
				e.until = time.Time{}
			}
		}
	} else {
		e := &entry{c: a}
		p.byUID[a.UID] = e
		p.order = append(p.order, e)
	}
	p.mu.Unlock()
	p.SaveState()
}

func (p *Pool) Remove(uid string) {
	p.mu.Lock()
	delete(p.byUID, uid)
	for i, e := range p.order {
		if e.c.UID == uid {
			p.order = append(p.order[:i], p.order[i+1:]...)
			break
		}
	}
	p.mu.Unlock()
	p.SaveState()
}
