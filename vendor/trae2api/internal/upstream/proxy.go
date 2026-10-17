package upstream

import (
	"net/http"
	"net/url"
)

// OutboundProxy is supplied by the embedding host before clients are created.
// Its callback reads an atomic URL, so existing clients follow live settings.
var OutboundProxy func(*http.Request) (*url.URL, error)
