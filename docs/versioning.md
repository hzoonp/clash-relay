# Versioning and compatibility

`clash-relay` follows Semantic Versioning. `2.0.0` is the clean-slate public contract: runtime declarations are v2-only, the production application path is compiler/graph based, and removed v1 public fields do not have runtime compatibility aliases.

## Current v2 public contract

The tracked public declarations are:

- `config.yaml` with `version: 2`;
- `subscriptions.yaml` with `version: 2`;
- `policies.yaml` with `version: 2`, composing owned routing, scheduling, classification, and topology fragments.

A future incompatible change to a documented declaration field meaning, source permission, publication contract, or security boundary requires a new major version. Additive declarations and internal hardening may ship in a minor version when they preserve this contract. Bug fixes and dependency/core pin updates may ship in a patch release when behavior remains compatible.

There is no runtime Policy Model v1 fallback and no runtime alias for removed Public Config v1 fields. `scripts/migrate_policy_v2.py` is an offline conversion helper only; its output must pass the same current v2 schemas and audits as a fresh project.

## Canonical v2 invariants

- A source declaring `allowed_uses: [browsing, ai]` cannot become reachable from a `general`, media, messaging, download, or final route, including through nested groups, fallback groups, provider indirection, or `dialer-proxy`.
- The canonical first subscription rejects EMBY-labelled nodes and only explicit multipliers strictly greater than `2.0`; exactly `2.0` and unmarked nodes remain eligible.
- `ingest_order` controls deterministic source ingestion/deduplication order only. It is not a routing or node-quality priority.
- The pinned `ACL4SSR_Online.ini` profile is the canonical classification baseline. `ProxyLite.list` is the generic foreign-web classifier; `ProxyGFWlist` is not the canonical browser classifier.
- AI/OpenAI before `ProxyMedia` and `Download.list` before `ProxyLite` are explicit declared extensions. Undeclared classification drift fails closed.
- Final `MATCH` remains `漏网之鱼` on the general graph.
- The canonical top-level user-facing groups are exactly `代理选择`, `网页浏览`, `人工智能`, `流媒体`, `消息通讯`, and `下载流量`. Public scenario groups do not attach proxy providers directly.
- Production selector names, compatibility members, AI display names/exclusions, ACL4SSR binding targets, and ordering edges are declared under `routing.contract`; the Routing V2 audit consumes that declaration rather than a second hard-coded policy.
- `流媒体`, `消息通讯`, and `下载流量` are general-only selectors. Browsing/AI-only sources cannot become reachable through them.
- Browsing owns an independent regional order `US -> SG -> JP -> TW -> KR -> HK -> OTHER`; changing browsing preference does not change AI service preference order.
- Browsing qualification is live and fail closed. Canonical policy is three attempts: 3/3 is Stable, 2/3 is Reserve, and fewer than 2/3 is rejected for publication.
- Automatic browsing routing is region-first; lower instantaneous delay elsewhere is not sufficient to switch regions. Manual regional browsing choices never cross regions.
- Scheduler history may demote a currently qualified node inside its region but never promotes a current Reserve or live-failed node into Stable.
- OpenAI, Claude, and Gemini qualify independently behind the generic `ServiceQualification` API. The main qualification pipeline is provider-agnostic. Provider-specific probe, cache, diagnostic, route-postprocessing, and optional client-path hardening behavior belongs to the registered implementation.
- The production data path is `Declarations -> Subscription I/O -> NodeInventory -> PolicyCompiler -> RuntimeGraph -> qualification -> Qualified Graph -> MihomoSerializer -> config.yaml`.
- Python application stages communicate through typed in-process APIs. Only true external programs such as Mihomo are subprocess boundaries.
- The exact final production candidate is validated with every stable Mihomo core declared in `tools/mihomo-versions.json` before publication. Workflow YAML and documentation do not maintain a second version list.
- CI and publication are bound to an exact validated commit SHA. Third-party GitHub Actions are immutable-SHA pinned and Python dependencies are installed from hash-verified locks.
- A failed ACL fidelity, source reachability, qualification, current-policy audit, Promotion Guard, Mihomo validation, or production-publication gate does not intentionally leave an unvalidated candidate active.

## Private release storage contract

The project version and the private KV storage-format version are independent.

The current immutable release storage schema remains v1 because the format is stable and already supplies the required integrity guarantees:

- immutable config objects: `<production>.release-v1.<sha256>.config`;
- immutable manifests: `<production>.release-v1.<sha256>.manifest`;
- current pointer: `<production>.current-release-v1`;
- previous pointer: `<production>.previous-release-v1`.

The `v1` suffix above identifies **storage schema version 1**, not product version 1. Renaming stable private keys solely for the `2.0.0` product release would create unnecessary state migration risk.

v2 removes the old `<production>.previous-v1` compatibility slot and rollback fallback. A rollback candidate must resolve through the versioned previous pointer, match its SHA-256 release id, have an exact immutable manifest, pass the current source/Routing V2 policy audit, and pass the full stable Mihomo matrix before activation.

Release activation stages immutable objects first, verifies exact bytes, updates the fixed client-facing Cloudflare KV key, then commits versioned pointers. A pointer failure after activation invokes compensating restoration when the previous state is available. Workers KV is not represented as a cross-key atomic store.

## Derived state and observability

Scheduler history, AI qualification cache, production metrics, and operational SLO history are private derived state. They may safely degrade to fresh live behavior without widening routing or qualification permissions.

Operational SLO state is aggregate-only and bounded. Qualification rejection rate, retry recovery rate, Promotion Guard block rate, lifecycle duration, and candidate churn are observational signals; SLO persistence is best-effort and never weakens a production gate.

Production configuration, immutable release bytes, release manifests, pointers, scheduler state, AI cache, production metrics, operational SLO state, subscription responses, and node-level qualification results are not attached to GitHub Releases.

## Release process

The reusable validation workflow is release-authoritative. A release commit must pass Python 3.11/3.12/3.13 quality, deterministic generation, Routing V2 drift validation, every pinned stable Mihomo integration job, and the final Validated SHA binding.

The source-release workflow reads the package version from `pyproject.toml` and requires matching versioned release notes at `docs/releases/<version>.md`. It checks out the exact validated SHA and creates a source-only GitHub Release only if that tag does not already exist.
