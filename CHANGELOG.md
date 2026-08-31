# Changelog

All notable user-visible changes are documented here. This project follows Semantic Versioning after the v0.1.0 production baseline.

## [Unreleased]

### Added

- Routing Model V2 now treats scenario, service, source permission, region, capability, and scheduler policy as independent routing dimensions, with complex concurrent route intents covered by unit and dual-Mihomo integration tests.
- Hidden `媒体自动` and `下载自动` schedulers keep media/download routing independent from persisted user selections while remaining strictly in the `general` source-use domain.
- A no-Secrets Routing V2 Drift Guard continuously verifies the finalized configuration graph without collecting domains, node identities, endpoints, credentials, subscription URLs, or user traffic.

### Changed

- The canonical FlClash policy surface exposes only the three user decisions `代理选择`, `网页浏览`, and `人工智能`; ACL4SSR application targets, regional helpers, manual/provider helpers, media/download schedulers, and final routing groups remain functional but hidden.
- AI countries are internal scheduling dimensions rather than top-level policy groups. Hong Kong is hard-excluded before OpenAI, Claude, or Gemini qualification, while each service independently follows the declared `US -> SG -> JP -> TW -> KR -> OTHER` preference over its own qualified set.
- YouTube and generic foreign media route through the hidden general-only media scheduler. Netflix prefers its filtered capability pool and then falls back to that media scheduler.
- `Download.list` is a first-class internal scenario and now routes through the hidden general-only download scheduler after known domestic classification but before generic `ProxyGFWlist` browsing classification.
- Shadow-era `current -> cutover` reporting has been removed now that Routing V2 is the canonical production graph; drift is reported as healthy or drifted instead.

### Security

- `subscription_1` remains reachable only from browsing and AI; media, download, general, and final routes remain unreachable from that source.
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

[Unreleased]: https://github.com/hzoonp/clash-relay/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/hzoonp/clash-relay/releases/tag/v1.0.0
[0.1.0]: https://github.com/hzoonp/clash-relay/releases/tag/v0.1.0
