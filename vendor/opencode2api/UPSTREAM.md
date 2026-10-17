# OpenCode2API

- Source: https://github.com/TiaraBasori/OpenCode2API
- Revision: `2be4813d10790360ed6623f92bedd2159b2bd133`
- Package version: 2.5.0
- License: MIT; see LICENSE.md.
- Local changes: Responses created/completed objects include the standard
  `created_at` and `status` fields; the dependency lock updates `proxy-addr` to
  2.0.8 to resolve GHSA-jqcg-44mw-7w3h. Other runtime sources are unchanged.

Unified2API manages this optional process through `unified/opencode_runtime.py`.
The gateway binds to loopback, uses its own random credentials, an isolated home
and workspace, and a dedicated unprivileged container user. The upstream tool-lock
plugin denies built-in tools; client tool calls are returned for client execution.
The OpenCode CLI is pinned to 1.19.0, matching the upstream SDK 1.x requirement.
