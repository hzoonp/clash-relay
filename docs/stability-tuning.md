# Stability tuning and client validation

The generated profile remains one subscription URL. Client-side Mihomo probes select routes using the device's current network. Service qualification and publication gates still protect the candidate before it reaches clients.

## Subscription fetch concurrency

The builder fetches enabled subscriptions with at most **four concurrent workers**. The limit is internal, with no new public configuration field. Per-source timeout, payload-size limits, destination validation and client profiles are unchanged.

Fetch completion order never determines node ownership: parsing, classification, source reports and deduplication consume results in `(ingest_order, id)` order. Required-source and optional-source error policies remain in force. On an aborted build, queued work is cancelled and already-running fetches are joined before returning; those requests still obey their configured timeouts. Concurrent fetching may start later sources before an earlier source is found to have failed.

## Inventory before changing health checks

Inspect a locally stored final candidate with the installed project environment:

```powershell
python scripts/measure_health_checks.py --candidate .private/candidate.yaml
```

The tool reads YAML locally, sends no probes and emits only aggregate JSON. It reports provider/group check counts, expanded target slots, unique checked provider nodes, repeated provider node occurrences, estimated periodic requests per hour and incomplete estimates. It emits no provider/group/node names, endpoints, URLs, credentials or fingerprints. Load failures return a fixed diagnostic without private parser excerpts.

This is a **static inventory**, not measured network traffic or battery cost. The rate estimate assumes lazy checks are active, ignores filters and expands nested groups to all leaves; real checks may use only the selected member. Unknown external payloads and missing intervals are excluded from the rate and counted as incomplete. Startup bursts, retries and manually requested tests are outside the estimate. Repeated node occurrences identify possible overlap, not proof that their check purposes are interchangeable.

Before reducing checks or adding a Top-N pool, measure an actual FlClash device with a fixed profile and observation window. Record aggregate automatic-check request counts, transferred bytes, idle/active behavior and battery use where the platform exposes them; distinguish user-triggered tests from scheduled checks. Compare representative browsing and network-switching behavior. Keep raw logs and endpoint details private. This change does not reduce checks or introduce Top-N limits.

## FlClash device checklist

These are manual acceptance steps; repository tests do not establish that a real device has passed them. Record FlClash version, embedded Mihomo version, OS and network type for each run. Follow the DNS/TUN settings in [configuration.md](configuration.md) before checking behavior.

1. **Import:** with the proxy initially off, import the final HTTPS subscription URL and enable the profile. Confirm successful load, domestic direct access, overseas browsing and an eligible AI service.
2. **Update:** refresh the same link, confirm successful profile replacement and continued browsing. Refresh again with unchanged content and verify stable group choices. Check that a temporary download failure leaves the last valid local profile usable.
3. **Restart:** restart the app and the core, then confirm profile loading and routing work without another import.
4. **Wi-Fi/mobile switch:** switch in both directions while using automatic browsing. Allow the configured test interval and failure threshold, then confirm new connections recover on the new network. Existing connections need not migrate between exits.
5. **Sleep/resume:** suspend or lock the device long enough to stop foreground activity, resume it, and check that browsing and automatic probes recover.
6. **Manual region persistence:** choose HK/SG/JP or another explicit region, then repeat update, restart and network switching. Confirm the manual group choice remains selected; choosing automatic mode again should restore local automatic selection.
7. **Carrier coverage:** repeat available cases on real Telecom, Unicom and Mobile access networks, recording unavailable combinations as untested. A successful run on one connection does not establish all-carrier coverage.

Report pass/fail, recovery time and aggregate counts. Redact subscription URLs, tokens, node identities and raw application logs before sharing results.
