# Qoder2API-Hub

Source: https://github.com/shuishuipingan/qoder2api-hub

Pinned revision: `439bb1f32eac52335143e5690734c91aabb9f0ad` (MIT; see LICENSE).

Unified uses the account types, regional OAuth configuration, COSY signer, model catalog,
request builder, tool translation and stream cleanup as libraries. Its standalone HTTP
server, dashboard, usage storage, local credential scanner and scheduler are not started.
The existing Unified authentication, React console and automation schedule remain in use.

Local adaptations: account filenames and replacement lookups include the region; a stable
Unified account ID survives migration; credential files have mode 0600. The request template
retains the upstream schema with neutral system text, no demo messages or built-in tools,
and no default reasoning effort. Async HTTP calls have cancellation, SSE frame size guards and
explicit errors when streams end without a completion marker.

The upstream UMID extractor is included for Docker builds. It downloads only the pinned official @qoder-ai/qodercli 1.1.65 tarball, requires its reviewed SHA-512 integrity, and validates the native architecture. Native binaries and machine identities are not committed. International daily check-in uses the official runtime-info bridge and only daily credits campaigns.
