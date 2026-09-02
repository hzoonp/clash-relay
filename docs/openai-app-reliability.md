# OpenAI App reliability contract

`clash-relay` treats ChatGPT application reachability as stricter than a single successful request to `chatgpt.com`.

The pinned ACL4SSR OpenAI rules remain the upstream classification baseline. A separate, reviewed `cr_openai_app` overlay covers the documented ChatGPT web/desktop/mobile application network surface and is inserted only after private AI qualification. The overlay targets `__CR_AI_SERVICE_OPENAI`, so documented ChatGPT application traffic cannot silently fall through to generic AI, browsing, media, or final routing.

The reviewed source is OpenAI's public network recommendations:

`https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps`

The executable contract lives in `src/clash_relay/openai_app_contract.py`. It records a contract version, source-review date, deterministic fingerprint, exact route rules, critical qualification endpoints, and supporting diagnostic endpoints. Shared third-party infrastructure such as WorkOS, Cloudflare, Stripe, Sentry, Datadog, Apple, and Imgix is never admitted with an unrestricted suffix merely to make ChatGPT work; shared dependencies use exact hosts unless the reviewed OpenAI network list requires a wildcard family.

## App-ready qualification

An OpenAI candidate is App-ready only when every critical endpoint passes normal TLS certificate and hostname verification. `skip-cert-verify` is not introduced and certificate errors are classified as `tls_error` and reject the candidate.

The canonical `ai_openai` probe remains declared in `policies.yaml`. The OpenAI App contract adds Android/authentication critical endpoints. Supporting CDN/telemetry endpoints are probed only after the candidate passes all critical endpoints; supporting failures remain diagnostic and do not independently reject an otherwise App-ready candidate.

OpenAI, Claude, and Gemini keep independent qualified sets. A missing OpenAI App-ready set causes the hidden OpenAI service target to fail closed to `REJECT`; it never falls back to an unqualified general node.

## Runtime parity

Canonical production remains client-owned DNS (`runtime.dns.mode: client`) and does not restore managed Fake-IP DNS. AI qualification mirrors the production HTTP/TLS/QUIC sniffer configuration while leaving DNS ownership unchanged, so the qualification runtime is closer to the FlClash/Android path without inventing a second resolver policy.

The post-qualification production audit requires the exact OpenAI App overlay and verifies that it precedes ACL4SSR OpenAI and generic AI classification. The final candidate must still pass the complete stable Mihomo matrix before the versioned Cloudflare KV release transaction can activate it.

## Cache and observability

OpenAI cache identity includes the App contract fingerprint. A contract/probe change invalidates only old OpenAI decisions; unrelated Claude and Gemini cache records remain reusable according to their existing TTL contract.

Production observability remains aggregate-only. It may record App-ready counts, critical/supporting endpoint counts, and aggregate TLS/DNS/timeout failures. It does not record endpoint URLs, node names, proxy servers, credentials, subscription URLs, or node-level outcomes.
