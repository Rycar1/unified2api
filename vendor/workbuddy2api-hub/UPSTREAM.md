# WorkBuddy upstream modules

Original integrated runtime: https://github.com/ardeyouxipianyi/workbuddy2api-hub (MIT).

Python modules are synchronized to revision `dd0ed4523e552a91f60829f9788dc682b9d0e2b3` (2026-10-13). Unified keeps its adapter hooks in `unified/workbuddy_hooks.py`, private lifecycle in `unified/workbuddy_runtime.py`, and management authorization in its own API.

Unified creates its own short activity session, waits for the actual ACP turn to complete, and saves status/counters only. It does not query existing user conversations or persist activity text. The standalone entry point, desktop credential scan and client configurator are not run during gateway startup. Client configuration is supplied as a downloadable local helper.
