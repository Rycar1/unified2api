# Zen direct transport reference

- Source: https://github.com/12errh/zen-proxy
- Revision: `0b009a106df2353e3f105db2420642594cf1032f`
- License: MIT; see LICENSE.
- `agent-tools.json` is the unchanged upstream tool manifest.

`unified/zen_direct.py` implements the session identity and tool framing in Python.
No upstream executable JavaScript is run. Requests always stream upstream; the
existing bounded Python relay converts Chat, Responses and Messages downstream.
Automatic model aliases/fallback, synthetic-success health checks, prompt
injection and silently discarded tool calls are intentionally not incorporated.
Injected tool definitions are never executed on the server. Unexpected tool calls
return an explicit error. Model identity and a protocol finish marker are checked.
