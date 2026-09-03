# Changelog

All notable user-visible changes are documented here. This project follows Semantic Versioning after the v0.1.0 production baseline.

## [Unreleased]

## [1.8.0] - 2026-09-04

### Added

- P33 adds one canonical production application entrypoint, `scripts/run_production_release.py`, which owns generation, private derived-state loading, qualification/audits, Promotion Guard, the pinned stable Mihomo matrix, the existing versioned release transaction, post-commit derived-state persistence, and production proof. GitHub Actions is now a thin environment adapter instead of a second orchestration engine.
- P34 migrates canonical production to physical Policy Model v2 with separate routing, scheduling, classification, and topology fragments, plus `scripts/migrate_policy_v2.py` for deterministic v1-to-v2 migration with normalized-equivalence verification.
- P37 adds a privacy-safe aggregate release manifest containing exact config/release identity, Policy Model/project version, runtime inventory counts, source-use aggregates, qualification/Promotion Guard state, validated Mihomo cores, timestamp, and commit SHA when available.

### Changed

- P35 removes the legacy Python `PolicyContract` and Routing V2 default documents. Routing consumers now require an explicit `routing` declaration and `routing.contract` and fail closed instead of silently inheriting group names, bindings, region aliases, or exclusions.
- P36 freezes canonical `services.yaml` as an empty compatibility-only extension point. Production routing and service qualification semantics remain owned by Policy Model v2 rather than a second generic Service domain.
- P38 establishes v1.8.0 as the stabilization boundary for the P27-P38 architecture, preserving Python 3.11/3.12/3.13 coverage, deterministic generation, repository safety checks, and the pinned real-Mihomo stable matrix.

### Compatibility

- The fixed client-facing Cloudflare KV production key, six FlClash public scenario names, source permissions, ACL4SSR ordering, scheduled refresh cadence, and `subscription_1` browsing/AI-only plus >2x filtering contract are unchanged.
- Physical v1 policy documents remain readable by `PolicyDocument` for compatible projects, but consumers that use Routing V2 must explicitly declare routing semantics; implicit Routing V2/PolicyContract defaults are no longer supported.
- The P17 immutable `.release-v1.<sha>.config` / `.manifest` format and current/previous release pointers are unchanged, so historical rollback verification remains byte-compatible.

### Security

- The production ordering remains fail closed: qualification/audits, Promotion Guard, and every pinned stable Mihomo core must pass before the existing Cloudflare release activation transaction can change the client-visible value.
- P37 release-manifest output is aggregate-only and excludes node names, servers, ports, credentials, subscription URLs, probe endpoints, and child-process diagnostics. Private candidate files are removed from the runner in a `finally` cleanup path.

## [1.7.0] - 2026-09-03

### Added

- P26 adds automatic production subscription refresh on a six-hour GitHub Actions schedule, so upstream subscription changes are periodically regenerated, qualified, validated, and published without requiring a repository commit or manual dispatch.
- Push, schedule, and manual dispatch now resolve one explicit `publish_requested` workflow contract: pushes and scheduled runs publish after validation, while manual dispatch remains a dry run unless `publish=true` is explicitly selected.
- Regression coverage freezes the scheduled trigger and verifies that republishing identical validated bytes remains idempotent without rotating the previous-release pointer.

### Changed

- Scheduled refreshes use the same unified private generation, source-isolation audit, browsing/transport qualification, AI qualification, OpenAI client-path hardening, post-qualification audit, pinned stable Mihomo matrix, and versioned Cloudflare KV release transaction as existing production pushes.
- Repeated scheduled runs with unchanged final bytes keep the existing production release active with `status: unchanged` and `production_changed: false`; no synthetic release rotation is introduced.

### Security

- Automatic refresh does not create a lower-trust publication path: failed subscription fetches, qualification failures, policy audits, Mihomo validation failures, or Cloudflare publication failures still leave the previous production value intact.
- Subscription URLs and generated configuration bytes remain private runner/KV data, and the six public FlClash scenarios, `subscription_1` browsing/AI-only isolation, >2x filtering, ACL4SSR fidelity, client-owned DNS, and normal TLS verification remain unchanged.

## [1.6.3] - 2026-09-03

### Fixed

- P25.1 keeps the unified qualification stage protocol status fixed at `passed` after successful OpenAI client-path hardening, while preserving the hardener's operation result separately as `runtime_status`.
- Regression coverage now prevents runtime metadata from overriding the top-level pipeline success contract used by production publication.

### Security

- This hotfix does not change OpenAI route locking, normal TLS certificate and hostname verification, source isolation, client-owned DNS, stable-first client fallback, or any of the six public routing scenarios.


## [1.6.2] - 2026-09-02

### Added

- P25 adds a post-qualification OpenAI client-path hardening stage that keeps P24 server-side App-ready qualification as admission control while generating dedicated client-local OpenAI runtime providers for FlClash/Mihomo.
- Each qualified OpenAI region receives an isolated inline runtime provider whose local health check targets `https://android.chat.openai.com/` with normal TLS/hostname verification, a 120-second interval, a 5-second timeout, and non-lazy probing.
- The final production audit now requires the OpenAI client-path runtime contract in addition to the P24 route lock, Routing V2, source reachability, ACL4SSR fidelity, and stable Mihomo matrix.

### Changed

- OpenAI runtime selection is stable-first fallback instead of latency racing: it prefers the declared region order, keeps using the first locally healthy route, and fails over when the user's own Mihomo core marks that path unavailable.
- Service-qualified OpenAI nodes are cloned into deterministic runtime-only providers with isolated runtime names; the original AI providers remain unchanged for Claude, Gemini, generic AI routing, server qualification, and source-policy accounting.
- OpenAI server-side qualification cache pass freshness is reduced from the generic six-hour window to two hours; Claude/Gemini keep the existing generic AI cache TTLs.
- The unified private pipeline is now `generated -> browsing/transport -> AI server qualification -> OpenAI client-path hardening -> final candidate` before post-qualification audit and the stable Mihomo matrix.

### Security

- Client-path hardening does not disable certificate verification, restore managed/Fake-IP DNS, or broaden the OpenAI domain contract. It adds a second health layer on the actual Android/FlClash route without weakening P24 TLS gates.
- Static Mihomo configuration does not expose durable error-type state, so this release does not claim a persistent 12-24 hour client-side TLS quarantine. Runtime failure handling uses documented provider health-check and fallback semantics only.
- The six public scenarios, `subscription_1` browsing/AI-only isolation and >2x filtering, ACL4SSR Online fidelity, client-owned DNS, versioned Cloudflare KV release transaction, and source-only GitHub Release policy remain unchanged.

## [1.6.1] - 2026-09-02

### Added

- P24 adds a reviewed OpenAI/ChatGPT App network contract and a dedicated `cr_openai_app` rule overlay that locks the documented application surface to service-qualified OpenAI egress without changing the pinned ACL4SSR baseline.
- OpenAI qualification now requires the primary ChatGPT probe plus Android and authentication critical TLS endpoints; supporting WorkOS/Cloudflare/CDN/telemetry probes remain diagnostic-only.
- Production metrics and production proof expose only aggregate OpenAI App-ready/TLS/DNS/timeout counts.

### Changed

- The AI qualification runtime now preserves the canonical HTTP/TLS/QUIC sniffer while retaining client-owned DNS; it does not restore managed DNS or Fake-IP.
- OpenAI cache identity includes the reviewed App contract fingerprint, so this release invalidates stale OpenAI-only decisions while Claude/Gemini cache records remain independently reusable.
- Post-qualification production audit requires the exact OpenAI App route lock ahead of ACL4SSR OpenAI and generic AI rules before the complete stable Mihomo matrix can validate publication.

### Security

- TLS certificate and hostname verification remain mandatory. `skip-cert-verify` is not introduced, and certificate failures are hard `tls_error` qualification failures.
- Shared third-party infrastructure is routed with exact hosts unless the reviewed OpenAI network contract explicitly requires a wildcard family; broad WorkOS, Cloudflare, Stripe, Sentry, Datadog, Apple, and Imgix suffix capture is forbidden by tests.
- The six public scenarios, `subscription_1` browsing/AI-only isolation and >2x filtering, client-owned DNS, ACL4SSR Online fidelity, versioned Cloudflare KV release transaction, and source-only GitHub Release policy remain unchanged.

## [1.6.0] - 2026-09-02

### Added

- P19 extends the private bounded production-metrics ring with sanitized release state, stable-Mihomo validation counts, regional browsing aggregates, unified qualification stage counts, and bounded phase timings.
- P21 adds explicit failure-injection coverage for ambiguous Cloudflare writes, first-release activation failure, pointer-commit failure, incomplete compensation, and corrupt or missing immutable release manifests.
- P23 adds `clash-relay doctor` for public declaration validation, subscription Secret readiness, optional bounded subscription reachability checks, and optional Cloudflare KV read-only readiness checks.

### Changed

- P18.1 makes the authoritative onboarding documentation describe the manifest-driven Mihomo matrix, versioned release transaction, current-policy rollback, and doctor-first fork flow, with an automated documentation drift guard.
- P20 upgrades anonymous browsing scheduler history to v3 with consecutive-failure debounce and asymmetric hysteresis: a transient failure does not immediately demote a preferred Stable node, while a historically demoted node must clear a stronger recovery EMA before returning to preferred Stable.
- P22 records aggregate browsing/transport, AI, and total qualification durations and reuses the already downloaded primary pinned stable Mihomo binary for the first stable-matrix validation, while every pinned stable core still validates the exact final candidate.
- Production proof now includes safe unified-qualification timing metadata and versioned release transaction status; private metrics sanitize unknown fields before re-persistence.

### Compatibility

- Existing scheduler state v1/v2 is read and migrated to v3 without changing anonymous node fingerprints. Production writes the new `.scheduler-state-v3` key and continues reading v2/v1 as migration fallbacks.
- The client-facing Cloudflare production key, six public FlClash scenarios, source permissions, and release-object naming remain compatible with v1.5.0.

### Security

- Doctor never publishes bytes and reduces private connectivity failures to safe public identifiers/status messages; subscription URLs, subscription payloads, Cloudflare credentials, and production config bytes are excluded from its report.
- Versioned previous-release reads now require both exact release-id byte integrity and an exact immutable manifest match before rollback can proceed.
- Production observability remains aggregate-only and strips unsupported historical fields instead of perpetuating arbitrary private state.

## [1.5.0] - 2026-09-01

### Added

- P14 introduces the shared side-effect-free `RuntimeGraph` and immutable `CandidateArtifact` stage model for one canonical view of generated Mihomo groups, providers, proxies, and controlled dialer edges.
- P15 adds a declarative `routing.contract` in `policies.yaml` for the six public groups, automatic scheduler names, compatibility selectors, AI service/region names, required exclusions, and ACL4SSR binding/priority contracts.
- P16 adds one staged production qualification entrypoint: generated -> browsing/transport -> AI -> explicit final candidate.
- P17 adds immutable SHA-256 production release objects, current/previous release pointers, release-aware rollback, and compensating restoration when a pointer commit fails after activation.
- P18 adds manifest-driven stable Mihomo matrices for CI, production, rollback, and production proof, plus broader Routing V2 drift triggers and coverage reporting.

### Changed

- Routing V2 audit consumes the concrete `RuntimeGraph` and declarative policy contract instead of maintaining production selector names and AI exclusions as parallel Python constants.
- Production no longer mutates the generator output through separate workflow qualification steps; legacy browsing/transport and AI executors remain compatible internal stages behind the unified pipeline.
- Rollback now applies the **current** production/source-isolation and Routing V2 policy audit before the full stable Mihomo matrix and activation.
- AI cache and scheduler history persist only after the production release commits and are explicitly best-effort derived state; their failure warns without falsely marking an already committed validated release as failed.
- `tools/mihomo-versions.json` is the workflow source of truth for stable/prerelease core versions; production and rollback workflow YAML no longer hard-code version tags.

### Security

- The canonical six-scenario routing behavior, `subscription_1` browsing/AI-only isolation and >2x filtering, ACL4SSR Online fidelity, client-owned DNS/sniffing behavior, browsing/transport/AI qualification, and fail-closed production gates remain unchanged.
- Versioned release activation keeps the existing client-facing Cloudflare KV key and does not claim impossible cross-key atomicity; immutable releases are verified before activation and previous exact bytes are restored on compensatable commit failures.
- Historical rollback candidates that remain valid Mihomo syntax but violate the current source-permission or Routing V2 contract are rejected before activation.

## [1.4.1] - 2026-09-01

### Changed

- Generated Mihomo/FlClash runtime node names shorten canonical numbered source labels from `subscription_N/` to `sub_N/` while keeping the canonical source id unchanged inside policy and isolation logic.
- Runtime label collisions fail closed instead of weakening source isolation.

### Compatibility

- Browsing scheduler history and AI qualification cache use runtime identity, so this display-name migration causes a one-time private state refresh on the first v1.4.1 production run; live qualification remains authoritative throughout the refresh.

## [1.4.0] - 2026-09-01

### Added

- P13 adds private pre-publication transport qualification for the general automatic inventory: live HTTPS admission for `自动选择`, plus live SOCKS5 UDP-associate qualification for `媒体自动` and `通讯自动`.
- UDP qualification probes a QUIC-speaking UDP/443 endpoint first and falls back to a lightweight UDP DNS round-trip; the report distinguishes observed QUIC-path responses from generic UDP reachability.

### Changed

- Automatic general/media/messaging selection is now transport-aware while explicit/manual node choices remain available and unchanged.

### Security

- P13 preserves client-owned DNS, no Fake-IP, P12 Sniffer settings, pinned ACL4SSR order, the six public selectors, browsing/AI qualification, and all `subscription_1` isolation boundaries.
- If transport qualification leaves no safe automatic UDP inventory, publication stops before Cloudflare KV is replaced and previous-good remains intact.

## [1.3.0] - 2026-09-01

### Added

- Canonical production now enables DNS-independent HTTP, TLS, and QUIC traffic sniffing so Mihomo can recover domain identity from Host/SNI/QUIC metadata before existing routing rules classify traffic.
- `runtime.sniffer` is an optional, strictly validated declaration with pure-IP parsing, protocol-specific ports, and HTTP destination override support.
- Real Mihomo integration covers the production combination of client-owned DNS plus HTTP/TLS/QUIC sniffing on both pinned stable cores.

### Changed

- Traffic sniffing is rendered independently from `runtime.dns.mode`; legacy configurations that omit `runtime.sniffer` preserve their previous output.
- Canonical production enables `parse-pure-ip`, keeps `force-dns-mapping` disabled, and does not alter ACL4SSR ordering, proxy groups, qualification, or source isolation.

### Security

- P12 does not restore Fake-IP, managed DNS, `store-fake-ip`, or overseas resolver injection. Canonical production remains `runtime.dns.mode: client`.

## [1.2.1] - 2026-09-01

### Fixed

- Canonical production now uses client-owned DNS, so clash-relay no longer forces Fake-IP or overseas public resolvers onto FlClash/Mihomo mobile clients.
- Managed DNS remains available as an explicit compatibility mode, and legacy configurations without `runtime.dns.mode` continue to behave as managed DNS.

## [1.2.0] - 2026-09-01

### Added

- The canonical FlClash surface now exposes `流媒体`, `消息通讯`, and `下载流量` alongside `代理选择`, `网页浏览`, and `人工智能`.
- A pinned `ACL4SSR_Online.ini` reference compiler and parity gate now verify baseline ruleset order, compatibility-selector defaults, explicit extensions, and intentional deviations before release or production publication.
- Hidden `通讯自动` provides the default general-only scheduler for messaging while existing media/download schedulers remain provider-backed and hidden.

### Changed

- ACL4SSR Online is now the classification source of truth instead of being reinterpreted into a separate application-routing graph.
- Generic foreign-web routing uses the pinned ACL4SSR `ProxyLite.list` baseline and maps it to `网页浏览`; the former canonical `ProxyGFWlist` replacement is removed.
- `ProxyMedia.list` now owns generic foreign-media classification and maps it to `流媒体`; Telegram maps directly to `消息通讯`.
- Microsoft, Apple, Google FCM, global-direct, block, and final compatibility selectors preserve the pinned ACL4SSR default member order while remaining hidden from the six-group FlClash surface.
- AI/OpenAI are explicit classification extensions before `ProxyMedia`; `Download.list` is the only download extension and runs before `ProxyLite`.
- Standalone YouTube/Netflix/game/Bilibili/ChinaMedia classification sources that altered ACL4SSR Online precedence are removed from the canonical rule graph. Media capability scheduling can remain internal without redefining baseline classification.

### Security

- `BanProgramAD.list` / `应用净化` remains intentionally disabled because it caused confirmed mobile image/CDN breakage; basic `BanAD.list` remains enabled.
- ACL4SSR raw-node wildcards are never copied into canonical scenario selectors. `流媒体`, `消息通讯`, and `下载流量` remain backed only by the `general` inventory, so `subscription_1` stays unreachable from all three.
- `subscription_1` remains browsing/AI-only, EMBY-labelled nodes remain excluded, and explicit multipliers greater than 2x remain rejected before classification.
- Pinned ACL4SSR parity, Routing V2 drift, source reachability, browsing/AI qualification, previous-good preservation, and dual-Mihomo validation remain fail-closed production gates.

## [1.1.0] - 2026-08-31

### Added

- Browsing Regional Scheduling partitions the canonical browsing inventory into `US`, `SG`, `JP`, `TW`, `KR`, `HK`, and `OTHER` before automatic selection.
- Every available browsing region owns independent hidden Stable and Reserve schedulers, and FlClash exposes regional browsing choices without exposing raw runtime nodes or proxy providers.
- `routing.browsing.preferred_regions` declares a browsing-only regional preference order independently from AI service routing.
- Real Mihomo integration now verifies both same-region Stable-to-Reserve recovery and cross-region fallback only after the preferred region is unavailable.

### Changed

- `网页自动` now prefers regions in the canonical `US -> SG -> JP -> TW -> KR -> HK -> OTHER` order instead of racing all qualified browsing nodes globally.
- Automatic browsing failover is ordered `preferred-region Stable -> same-region Reserve -> next-region Stable`; a healthy preferred region is not abandoned merely because another region has a lower instantaneous delay.
- `网页浏览` now exposes `网页自动`, each currently available `网页 · <地区>` choice, and `DIRECT`. Regional choices remain provider-free and do not expand raw `[BROWSING:*]` runtime nodes.
- A manual regional browsing choice is region-pinned: it can fail over from Stable to Reserve inside that region but never silently crosses into another region.
- Browsing history demotion is applied independently inside each region. A historically demoted but currently qualified node remains eligible through that same region's Reserve tier.
- Completely unavailable browsing regions are removed from the published runtime graph after live qualification rather than making the whole publication fail, provided at least one browsing region remains qualified.
- Regional re-evaluation uses the declared `scheduler.browsing.region_switch_interval` while node health checks retain the canonical browsing probe interval.

### Security

- Regional browsing groups can reference only their matching `cr_browsing_<region>` provider; general, media, download, final, and AI providers are not valid regional browsing inputs.
- `subscription_1` remains browsing/AI-only and explicit multipliers strictly greater than 2x are still rejected before classification.
- Source-to-scenario reachability audits, post-qualification audits, AI fail-closed qualification, previous-good preservation, and dual-Mihomo production validation remain mandatory.

## [1.0.1] - 2026-08-31

### Added

- Routing Model V2 now treats scenario, service, source permission, region, capability, and scheduler policy as independent routing dimensions, with complex concurrent route intents covered by unit and dual-Mihomo integration tests.
- Hidden `媒体自动` and `下载自动` schedulers keep media/download routing independent from persisted user selections while remaining strictly in the `general` source-use domain.
- A no-Secrets Routing V2 Drift Guard continuously verifies the finalized configuration graph without collecting domains, node identities, endpoints, credentials, subscription URLs, or user traffic.
- Browsing runtime hardening adds separate hidden Stable and Reserve automatic tiers under `网页自动`, with a real-Mihomo failover contract test.

### Changed

- The canonical FlClash policy surface exposes only the three user decisions `代理选择`, `网页浏览`, and `人工智能`; ACL4SSR application targets, regional helpers, manual/provider helpers, media/download schedulers, and final routing groups remain functional but hidden.
- `网页浏览` is now a policy-only selector containing exactly `网页自动` and `DIRECT`; it no longer attaches a proxy provider and therefore cannot expand raw `[BROWSING:*]` runtime nodes in FlClash.
- `网页自动` is now a hidden fallback from the Stable browsing scheduler to the Reserve browsing scheduler. A current qualified reserve node can automatically take over when the Stable tier becomes unavailable.
- Browsing pre-publication qualification, provider health checks, Stable/Reserve runtime schedulers, and the browsing fallback now share the canonical HTTPS probe, timeout, lazy mode, and expected-status semantics.
- Historical scheduler demotion now moves a currently qualified node from the preferred Stable tier into Reserve instead of removing it from automatic failover eligibility.
- AI countries are internal scheduling dimensions rather than top-level policy groups. Hong Kong is hard-excluded before OpenAI, Claude, or Gemini qualification, while each service independently follows the declared `US -> SG -> JP -> TW -> KR -> OTHER` preference over its own qualified set.
- YouTube and generic foreign media route through the hidden general-only media scheduler. Netflix prefers its filtered capability pool and then falls back to that media scheduler.
- `Download.list` is a first-class internal scenario and now routes through the hidden general-only download scheduler after known domestic classification but before generic `ProxyGFWlist` browsing classification.
- Shadow-era `current -> cutover` reporting has been removed now that Routing V2 is the canonical production graph; drift is reported as healthy or drifted instead.

### Security

- Canonical public scenario groups are contract-validated to reject direct proxy-provider exposure; the browsing public surface is additionally frozen to `网页自动` plus `DIRECT`.
- `subscription_1` remains reachable only from browsing and AI; media, download, general, and final routes remain unreachable from that source, and explicit multipliers strictly greater than 2x are rejected before classification.
- `ProxyGFWlist -> 网页浏览`, final `MATCH -> 漏网之鱼`, AI HK exclusion, independent per-service AI fail-closed qualification, source-reachability audits, and dual-Mihomo production validation remain non-negotiable boundaries.

## [1.0.0] - 2026-08-31

### Added

- Browsing scheduler history v2 with backward migration, freshness limits, anonymous success EMA, and aggregate latency EMA.
- Private incremental OpenAI, Claude, and Gemini qualification cache with full-proxy HMAC fingerprints, shorter failure TTLs, and live fallback for changed, expired, missing, or corrupt records.
- Same-run reuse of already digest-verified Mihomo binaries without cross-run or GitHub artifact caching.
- A tested production failure/degradation matrix covering subscription, browsing, AI, reachability, Mihomo, Cloudflare, auxiliary-state, and rollback failures.
- A private 30-run aggregate production metrics ring for configuration size/SHA, browsing health trends, scheduler demotions, and AI qualification/cache counts.
- Optional declarative scheduler controls in `policies.yaml` for browsing sampling, history maturity/freshness thresholds, and AI cache TTLs; omitted blocks keep the v0.1.0-compatible defaults.
- DNS-level subscription destination validation that rejects localhost and any hostname resolution containing private or special-use IPv4/IPv6 addresses.
- Expanded public security guidance for Cloudflare token scope, private operational state, parser/input limits, workflow trust, source permissions, and recovery.

### Changed

- The canonical production policy now explicitly declares the existing 3-attempt / 2-success browsing boundary, historical thresholds, and AI cache TTLs in YAML without changing routing behavior.
- Production path filtering now treats `scripts/download_mihomo.py` as a production-critical change and runs the real production workflow when it changes.
- Package maturity is declared Production/Stable for the 1.0 compatibility contract.

### Security

- Source-use admission and end-to-end route reachability remain mandatory before and after live qualification.
- Historical browsing state cannot promote reserve or live-failed nodes.
- AI cache reuse cannot survive a provider/protocol/endpoint/credential payload change because the full private proxy payload participates in the HMAC fingerprint.
- Subscription DNS resolution fails closed on private/special-use destinations and is repeated across redirects/final URLs.
- Generated production bytes, previous-good recovery data, scheduler history, AI cache, and metrics remain private Cloudflare KV/runtime data and are not GitHub Release assets.

## [0.1.0] - 2026-08-31

### Added

- Deterministic aggregation of multiple private Mihomo subscriptions.
- Declarative per-source `allowed_uses`, country permissions, and multiplier limits.
- Canonical Subscription 1 isolation to browsing and AI only, with explicit multipliers above 2x rejected before classification and deduplication.
- End-to-end source-to-route reachability auditing before and after live qualification.
- Browsing Scheduler V2.1 with three live HTTPS samples: 3/3 stable automatic candidates, 2/3 manual reserve candidates, and fewer than 2/3 rejected.
- Privacy-preserving browsing scheduler history stored as HMAC-SHA256 fingerprints and aggregate stability metadata.
- Independent OpenAI, Claude, and Gemini live qualification with service-specific fail-closed behavior.
- Validation of every production candidate with the pinned stable Mihomo matrix.
- Private Cloudflare Workers KV publication, previous-good snapshotting, and validated manual rollback.
- Aggregate production proof without node names, servers, credentials, or subscription URLs.
- English and Simplified Chinese Fork quickstarts.

### Security

- Generated private configurations are never published as GitHub Actions artifacts, Releases, Gists, Pages, or repository files.
- Production publication is fail-closed: a failed generation, audit, qualification, core validation, or publication gate does not replace the last known-good production value.
- Source-use isolation remains an admission and graph-reachability invariant rather than a post-generation best-effort filter.

[Unreleased]: https://github.com/hzoonp/clash-relay/compare/v1.8.0...HEAD
[1.8.0]: https://github.com/hzoonp/clash-relay/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/hzoonp/clash-relay/compare/v1.6.3...v1.7.0
[1.6.3]: https://github.com/hzoonp/clash-relay/compare/v1.6.2...v1.6.3
[1.6.2]: https://github.com/hzoonp/clash-relay/compare/v1.6.1...v1.6.2
[1.6.1]: https://github.com/hzoonp/clash-relay/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/hzoonp/clash-relay/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/hzoonp/clash-relay/compare/v1.4.1...v1.5.0
[1.4.1]: https://github.com/hzoonp/clash-relay/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/hzoonp/clash-relay/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/hzoonp/clash-relay/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/hzoonp/clash-relay/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/hzoonp/clash-relay/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/hzoonp/clash-relay/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/hzoonp/clash-relay/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/hzoonp/clash-relay/releases/tag/v1.0.0
[0.1.0]: https://github.com/hzoonp/clash-relay/releases/tag/v0.1.0
