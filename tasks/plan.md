# Upstream features and console refresh

Scope approved by the user: implement all eleven gaps from the source audit, retain existing provider separation and APIs, and redesign the React console using ARTEX and blog.rycarl.cn references. Local development and verification precede publication.

## Implementation order

1. Complete truthful model metadata and route capability aggregation. Verify that unknown limits stay unknown and configured channel limits are respected.
2. Add task/benefit and quota detail endpoints, an international completed-web-session activity flow, and a task center. Verify eligibility, idempotence and correct earned-credit totals.
3. Add WorkBuddy Messages/count_tokens and local search/fetch execution. Verify tool cycles, interruptions, guarded URL retrieval and explicitly estimated token counts.
4. Add account-level guardrails and enriched non-content metrics. Verify concurrent admission, attempt attribution, caching, cost estimates and daily boundaries.
5. Add bulk import preview/overwrite, full encrypted migration, and a downloadable local client configurator with restore. Verify that all array entries are imported and WorkBuddy volumes participate in backups without exporting Docker control state.
6. Refactor the console into composed React pages with reference design tokens, accessible dialogs and interruptible filter/layout motion. Verify live APIs at desktop/tablet/mobile sizes and reduced-motion settings.

Each feature is tested independently before final backend, frontend and isolated Docker checks. Real account operations use explicit UI actions; automated verification uses synthetic fixtures. Never read real conversations.

## API contract

- Existing URLs and model prefixes remain stable.
- New management endpoints are under `/admin/api/unified/`; admin authentication, CSRF and redaction remain mandatory.
- Metadata records retain source/provenance; route metadata is derived from compatible targets.
- Task and credit operations return per-account `ok`, `state`, `earned_credit`, `remaining` and safe error messages.
- Bulk import supports preview and explicit overwrite policy; exports containing credentials use encrypted `.ubak` only.
- Client setup scripts run on the client machine, preserve original configuration and support restore; the remote console supplies configuration rather than modifying local files.
- Stats store counters and timing only; cost is marked as estimated and no conversation bodies are persisted.
