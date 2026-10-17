package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"net/http"
	"net/url"
	"strings"
	"sync/atomic"
)

var outboundProxy atomic.Pointer[url.URL]

func nativeProxy(req *http.Request) (*url.URL, error) {
	host := req.URL.Hostname()
	if host == "localhost" || host == "127.0.0.1" || host == "::1" {
		return nil, nil
	}
	return outboundProxy.Load(), nil
}

//export TraeSetProxy
func TraeSetProxy(value *C.char) *C.char {
	raw := C.GoString(value)
	if raw == "" {
		outboundProxy.Store(nil)
		return C.CString("")
	}
	parsed, err := url.Parse(raw)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" || strings.ContainsAny(raw, "\r\n\t ") {
		return C.CString("Invalid outbound proxy")
	}
	outboundProxy.Store(parsed)
	return C.CString("")
}
