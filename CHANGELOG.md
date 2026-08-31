# Changelog

All notable user-visible changes are documented here. This project follows Semantic Versioning after the v0.1.0 production baseline.

## [Unreleased]

### Changed

- Future changes after the v0.1.0 production baseline.

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

[Unreleased]: https://github.com/hzoonp/clash-relay/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/hzoonp/clash-relay/releases/tag/v0.1.0
