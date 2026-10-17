package main

import (
	"net/http"
	"net/url"
	"testing"
)

func TestProxyUpdatesAndLoopbackBypass(t *testing.T) {
	defer outboundProxy.Store(nil)
	req, _ := http.NewRequest("GET", "https://provider.example/test", nil)
	first, _ := url.Parse("http://proxy.example:7890")
	outboundProxy.Store(first)
	got, err := nativeProxy(req)
	if err != nil || got != first {
		t.Fatal("global proxy was not selected")
	}
	for _, target := range []string{"http://localhost:8788", "http://127.0.0.1:7865", "http://[::1]:7865"} {
		local, _ := http.NewRequest("GET", target, nil)
		if proxy, _ := nativeProxy(local); proxy != nil {
			t.Fatal("internal request used proxy")
		}
	}
	second, _ := url.Parse("http://other.example:7891")
	outboundProxy.Store(second)
	if got, _ := nativeProxy(req); got != second {
		t.Fatal("proxy update did not take effect")
	}
	outboundProxy.Store(nil)
	if got, _ := nativeProxy(req); got != nil {
		t.Fatal("clearing proxy did not restore direct connection")
	}
}
