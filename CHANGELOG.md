# Changelog

All notable user-visible changes are documented here. This project follows Semantic Versioning after the v0.1.0 production baseline.

## [Unreleased]

## [1.4.0] - 2026-09-01

### Added

- P13 adds private pre-publication transport qualification for the general automatic inventory: live HTTPS admission for `自动选择`, plus live SOCKS5 UDP-associate qualification for `媒体自动` and `通讯自动`.
- UDP qualification probes a QUIC-speaking UDP/443 endpoint first and falls back to a lightweight UDP DNS round-trip; the report distinguishes observed QUIC-path responses from generic UDP reachability.
- P15 adds a fail-closed source-health guard that compares the fully qualified candidate with the currently published private configuration before the previous-good snapshot or production KV write.
- Source-health evaluation tracks only private structural fingerprints and aggregate source/region counts; it rejects large active-source collapses and protected browsing-region disappearance without emitting node names, endpoints, credentials, subscription URLs, or traffic records.

### Changed

- Automatic general/media/messaging selection is now transport-aware while explicit/manual node choices remain available and unchanged.
- Explicit source removal from `subscriptions.yaml` is treated as a deliberate declaration change, so P15 does not mistake planned source retirement for an outage.

### Security

- P13/P15 preserve client-owned DNS, no Fake-IP, P12 Sniffer settings, pinned ACL4SSR order, the six public selectors, browsing/AI qualification, and all `subscription_1` isolation boundaries.
- If transport qualification leaves no safe automatic UDP inventory, or if source-health drift crosses the production thresholds, publication stops before Cloudflare KV is replaced and previous-good remains intact.

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
- `网页浏览` now exposes `网页自动`, each currently available `网页 · <地区>` choice, and `DIRECT`. Regional choices remain provider-free and do not expand raw `[BROWSING:*]` nodes.
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
- Validation of every production candidate with Mihomo v1.19.30 and v1.19.29.
- Private Cloudflare Workers KV publication, previous-good snapshotting, and validated manual rollback.
- Aggregate production proof without node names, servers, credentials, or subscription URLs.
- English and Simplified Chinese Fork quickstarts.

### Security

- Generated private configurations are never published as GitHub Actions artifacts, Releases, Gists, Pages, or repository files.
- Production publication is fail-closed: a failed generation, audit, qualification, core validation, or publication gate does not replace the last known-good production value.
- Source-use isolation remains an admission and graph-reachability invariant rather than a post-generation best-effort filter.

[Unreleased]: https://github.com/hzoonp/clash-relay/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/hzoonp/clash-relay/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/hzoonp/clash-relay/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/hzoonp/clash-relay/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/hzoonp/clash-relay/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/hzoonp/clash-relay/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/hzoonp/clash-relay/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/hzoonp/clash-relay/releases/tag/v1.0.0
[0.1.0]: https://github.com/hzoonp/clash-relay/releases/tag/v0.1.0
