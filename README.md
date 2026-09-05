# clash-relay

[简体中文](README.zh-CN.md)

`clash-relay` 2.x is a deterministic, fail-closed Mihomo / FlClash configuration builder for merging private subscriptions into one standalone `config.yaml` while preserving hard source-to-scenario permissions.

Generated production configuration contains proxy credentials and is highest-sensitivity data. Production publishes validated bytes only to private Cloudflare Workers KV. Credential-bearing configuration is never uploaded to GitHub Artifacts, Releases, Gists, Pages, or commits.

## Start with a fork

For a fresh fork, use the [Fork quickstart](docs/quickstart.md):

```text
Fork
  -> configure CLASH_RELAY_SUBSCRIPTIONS
  -> configure Cloudflare KV
  -> clash-relay doctor
  -> manual dry-run (publish=false)
  -> inspect aggregate production proof
  -> publish=true
  -> automatic six-hour production refresh
  -> validated rollback when required
```

`clash-relay doctor` validates public declarations, subscription-secret readiness, the pinned Mihomo manifest, and optional Cloudflare read connectivity without publishing configuration bytes.

Scheduled and push production runs use the same release-authoritative gate as manual runs. Manual dispatch remains a dry run unless `publish=true` is explicitly selected. Unchanged validated bytes are idempotent and do not rotate the previous-release pointer.

## Public Config v2

The supported tracked declaration surface is intentionally small:

```text
config.yaml          version: 2
subscriptions.yaml   version: 2
policies.yaml        version: 2 manifest
policies/*           owned Policy Model v2 fragments
```

Removed v1 public fields do not have runtime compatibility aliases. Policy Model v1 is not a runtime input; `scripts/migrate_policy_v2.py` is an offline conversion helper only.

FlClash exposes six primary decisions:

```text
代理选择
网页浏览
人工智能
流媒体
消息通讯
下载流量
```

ACL4SSR compatibility groups, regional helpers, automatic schedulers, and qualification runtime groups remain hidden.

## Source policy

Merging subscriptions never means every source can enter every scenario:

```text
SUBSCRIPTION_1_URL
  ├─ explicit >2x       -> rejected
  ├─ exactly 2x         -> retained
  ├─ EMBY-labelled      -> rejected
  ├─ browsing           -> allowed
  ├─ ai                 -> allowed
  └─ general/media/...  -> denied

SUBSCRIPTION_2+
  ├─ general
  ├─ browsing
  └─ ai
```

`ingest_order` controls deterministic source ingestion/deduplication order only; it is not a routing or node-quality priority.

Production invariants:

1. `subscription_1` can enter only browsing and AI inventories.
2. EMBY-labelled subscription-1 nodes are rejected case-insensitively before inventory generation.
3. Explicit multipliers strictly above `2x` are rejected before classification and deduplication. Exactly `2x` and unmarked nodes remain eligible.
4. Media, messaging, download, ACL compatibility selectors, and final `MATCH` cannot reach `subscription_1`.
5. Source reachability is audited before and after qualification.

## Compiler and runtime graph

The v2 production data path is:

```text
Declarations
  -> Subscription I/O
  -> NodeInventory
  -> PolicyCompiler
  -> RuntimeGraph
  -> qualification
  -> Qualified Graph
  -> MihomoSerializer
  -> config.yaml
  -> audit / real Mihomo / promotion
```

The builder does not mutate topology after compilation. Python application stages call typed in-process APIs directly; only true external programs such as Mihomo remain subprocess boundaries.

## ACL4SSR fidelity

`rules/acl4ssr.yaml` pins the ACL4SSR Online reference. ACL4SSR owns the baseline classification semantics; clash-relay owns source-safe inventories, declared extensions, qualification, and scheduling.

Intentional deviations are explicit and audited:

- `BanProgramAD / 应用净化` remains disabled because it caused confirmed mobile image/CDN breakage.
- AI/OpenAI runs before broad `ProxyMedia` classification.
- `Download.list` runs before `ProxyLite` and targets `下载流量`.
- ACL4SSR's single-subscription raw-node wildcard is adapted to source-aware scenario selectors.

## Qualification and scheduling

Browsing qualification is region-aware. Automatic preference is:

```text
US -> SG -> JP -> TW -> KR -> HK -> OTHER
```

Manual region selection never silently crosses countries. Automatic mode crosses regions only when the preferred region is unavailable. Private anonymous scheduler history may demote unstable live-qualified nodes but never expands source admission.

AI services qualify through the generic `ServiceQualification` registry. OpenAI, Claude, and Gemini are registered implementations; the main qualification pipeline contains no provider-specific branch. Provider-specific critical/supporting probes, cache TTLs, route post-processing, and optional client-path hardening remain inside the implementation.

OpenAI keeps the reviewed ChatGPT App contract and route lock. Its client-path hardening is policy-declared and runs only after server-side admission. Normal certificate and hostname verification remain mandatory.

## Production release model

The private production path is:

```text
generated graph
  -> browsing + transport qualification
  -> ServiceQualification registry
  -> declared service client-path hardening
  -> post-qualification policy audit
  -> Promotion Guard
  -> every stable core in tools/mihomo-versions.json
  -> versioned Cloudflare KV release transaction
  -> fixed client-facing production key
```

`tools/mihomo-versions.json` is the only stable/prerelease Mihomo version source of truth. Documentation and workflows do not encode a second fixed stable-version matrix.

Every source or production release is bound to an exact validated commit SHA. The quality gate covers Python 3.11/3.12/3.13, hash-verified dependencies, Ruff, static typing for application boundaries, tests and coverage, architecture/supply-chain/privacy audits, deterministic generation, Routing V2 drift, and real Mihomo startup/provider integration.

Private production candidates are staged as immutable SHA-256 release objects:

```text
<production>.release-v1.<sha256>.config
<production>.release-v1.<sha256>.manifest
<production>.current-release-v1
<production>.previous-release-v1
```

The `v1` suffix here is the stable private storage-schema version, not the clash-relay product major version. v2 removes the legacy `previous-v1` rollback slot/fallback. Rollback requires the versioned previous pointer, exact bytes, a matching immutable manifest, the current policy audit, and the complete stable Mihomo matrix before activation.

Cloudflare KV is not a cross-key transactional database. The versioned Cloudflare KV release transaction therefore uses compensating semantics: immutable bytes are staged and read-back verified first, the fixed production key is activated, pointers are committed, and a failed commit attempts to restore the previous exact production bytes.

## Operational SLO and privacy

Production proof, production metrics, and operational SLO history contain aggregate operational metadata only. The SLO ring can measure qualification rejection rate, retry recovery rate, Promotion Guard block rate, lifecycle duration, and candidate churn without node identity or subscription data. SLO persistence is best-effort and never weakens a publication gate.

Public or persisted aggregate data excludes proxy names, servers, ports, credentials, subscription URLs, generated config bytes, and child-process diagnostics.

## GitHub Secrets and variables

Preferred subscription secret:

```text
CLASH_RELAY_SUBSCRIPTIONS
```

Example shape:

```json
{
  "SUBSCRIPTION_1_URL": "https://example.invalid/subscription-1",
  "SUBSCRIPTION_2_URL": "https://example.invalid/subscription-2",
  "SUBSCRIPTION_3_URL": "https://example.invalid/subscription-3",
  "SUBSCRIPTION_4_URL": "https://example.invalid/subscription-4"
}
```

Cloudflare publication requires:

```text
Secret:   CLOUDFLARE_API_TOKEN
Variable: CLOUDFLARE_ACCOUNT_ID
Variable: CLOUDFLARE_KV_NAMESPACE_TITLE
```

Never write real subscription URLs into tracked YAML, documentation, workflow arguments, or logs.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-build-isolation --no-deps -e .
clash-relay doctor --public-only
ruff check .
ruff format --check .
pytest -m "not integration"
python scripts/audit_documentation_contract.py
python scripts/audit_architecture_contract.py
python scripts/audit_operational_slo_contract.py
python scripts/audit_service_qualification_contract.py
python scripts/audit_supply_chain.py
python scripts/audit_acl4ssr_fidelity.py
python scripts/repository_audit.py
```

## Documentation

- [Fork quickstart](docs/quickstart.md)
- [配置快速上手](docs/quickstart.zh-CN.md)
- [Architecture](docs/architecture.md)
- [Configuration model](docs/configuration.md)
- [Service Qualification API](docs/service-qualification.md)
- [Operational SLO](docs/operational-slo.md)
- [Production maturity](docs/production-maturity.md)
- [OpenAI App reliability](docs/openai-app-reliability.md)
- [ACL4SSR routing model](docs/rules.md)
- [Security model](docs/security.md)
- [Publishing](docs/publishing.md)
- [Versioning and compatibility](docs/versioning.md)
- [v2 release checklist](docs/release-checklist.md)
- [2.0.0 release notes](docs/releases/2.0.0.md)
