# OpenAI App reliability contract

`clash-relay` treats ChatGPT application reachability as stricter than a single successful request to `chatgpt.com`.

The pinned ACL4SSR OpenAI rules remain the upstream classification baseline. A separate, reviewed `cr_openai_app` overlay covers the documented ChatGPT web/desktop/mobile application network surface and targets `__CR_AI_SERVICE_OPENAI`, so documented ChatGPT application traffic cannot silently fall through to generic AI, browsing, media, or final routing.

The reviewed source is OpenAI's public network recommendations:

`https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps`

The executable classification/qualification contract lives in `src/clash_relay/openai_app_contract.py`. It records a contract version, source-review date, deterministic fingerprint, exact route rules, critical qualification endpoints, and supporting diagnostic endpoints. Shared third-party infrastructure such as WorkOS, Cloudflare, Stripe, Sentry, Datadog, Apple, and Imgix is never admitted with an unrestricted suffix merely to make ChatGPT work; shared dependencies use exact hosts unless the reviewed OpenAI network list requires a wildcard family.

## Two-layer App-ready model

P24 server-side qualification remains the admission gate. An OpenAI candidate is admitted only when every critical endpoint passes normal TLS certificate and hostname verification. `skip-cert-verify` is not introduced and certificate errors are classified as `tls_error` and reject the candidate.

P25 adds a second layer after server qualification. The already-qualified OpenAI nodes are copied into deterministic runtime-only inline providers. Those providers are health-checked by the user's own Mihomo/FlClash core against `https://android.chat.openai.com/`, so the health decision observes the user's current Wi-Fi/mobile-network -> FlClash -> proxy-node -> OpenAI path instead of relying only on the GitHub runner's path.

The original AI providers are not modified. Claude, Gemini, generic AI routing, source accounting, and server-side qualification therefore keep their existing provider identities. OpenAI runtime copies receive isolated deterministic runtime names so Mihomo's one-name/one-provider invariant remains intact while source identity at the beginning of the runtime name is preserved.

## Stable-first failover

Each OpenAI service-qualified region becomes a hidden `fallback` backed by its client-local runtime provider. The top-level `__CR_AI_SERVICE_OPENAI` target is also a hidden `fallback` across the declared preferred regions.

The runtime health contract is:

```text
endpoint:        https://android.chat.openai.com/
interval:        120 seconds
timeout:         5000 ms
lazy:            false
expected-status: 200-399/400-499
selection:       stable-first fallback
```

This intentionally prefers continuity over latency racing. A healthy earlier route remains selected; failover occurs only after the client-local health state marks the path unavailable. The group-level failure setting triggers Mihomo's documented forced health-check behavior; it is not described as durable hysteresis or a persistent quarantine.

Static Mihomo configuration does not expose durable per-node error-type history. Therefore v1.6.2 does **not** claim a 12-24 hour client-side TLS quarantine. Implementing durable TLS-specific quarantine would require a trusted local controller/companion with persistent state and is outside this source-only configuration generator.

## App-ready qualification

The canonical `ai_openai` probe remains declared in `policies.yaml`. The OpenAI App contract adds Android/authentication critical endpoints. Supporting CDN/telemetry endpoints are probed only after the candidate passes all critical endpoints; supporting failures remain diagnostic and do not independently reject an otherwise App-ready candidate.

OpenAI, Claude, and Gemini keep independent qualified sets. A missing OpenAI App-ready set causes the hidden OpenAI service target to fail closed to `REJECT`; it never falls back to an unqualified general node.

OpenAI server-side pass-cache freshness is shorter than the generic AI cache window: canonical production uses two hours for OpenAI pass reuse while Claude/Gemini retain the generic six-hour pass TTL. The local runtime health layer is still authoritative for the user's current path after publication.

## Runtime parity

Canonical production remains client-owned DNS (`runtime.dns.mode: client`) and does not restore managed Fake-IP DNS. AI qualification mirrors the production HTTP/TLS/QUIC sniffer configuration while leaving DNS ownership unchanged.

The unified production pipeline is now:

```text
generated
  -> browsing/transport qualification
  -> AI server-side App-ready qualification
  -> OpenAI client-path runtime hardening
  -> post-qualification production/source/routing audits
  -> complete stable Mihomo matrix
  -> versioned Cloudflare KV release transaction
```

The post-qualification production audit requires both the exact P24 OpenAI App route lock and the P25 client-path runtime contract. The final exact candidate must still pass every pinned stable Mihomo core before publication.

## Exact historical rollback

P25 is an availability/reliability layer, not a source-permission bypass. Normal publication must include the P25 client-path runtime contract. Emergency rollback remains able to restore the exact bytes of a previously validated P24 release: the rollback workflow explicitly allows only the recognized P24 server-qualified OpenAI group shape after the candidate has still passed the current source isolation, Routing V2, ACL4SSR fidelity, P24 route lock, and current stable Mihomo matrix. The rollback path never rewrites the historical candidate merely to manufacture P25 state.

This narrow compatibility mode is available only through the explicit rollback audit flag and is not used by normal production publication.

## Cache and observability

OpenAI cache identity includes the App contract fingerprint. A contract/probe change invalidates only old OpenAI decisions; unrelated Claude and Gemini cache records remain reusable according to their existing TTL contract.

Production observability remains aggregate-only. It may record App-ready counts, client-path region/provider/node counts, critical/supporting endpoint counts, and aggregate TLS/DNS/timeout failures. It does not record endpoint URLs in longitudinal metrics, node names, proxy servers, credentials, subscription URLs, or node-level outcomes.
