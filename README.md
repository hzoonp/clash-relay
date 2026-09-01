# clash-relay

[简体中文](README.zh-CN.md)

`clash-relay` is a deterministic Mihomo / FlClash configuration builder for merging private subscriptions into one standalone `config.yaml` while preserving hard source-to-scenario permissions.

The generated production configuration contains proxy credentials and is treated as highest-sensitivity data. Production publishes validated bytes only to private Cloudflare Workers KV. Credential-bearing configuration is never uploaded to GitHub Artifacts, Releases, Gists, Pages, or commits.

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
  -> validated rollback when required
```

`clash-relay doctor` validates public declarations, private subscription-secret readiness, the pinned Mihomo manifest, and optionally Cloudflare read connectivity without publishing configuration bytes.

## Public scenarios

FlClash exposes only six primary decisions:

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
  ├─ EMBY-labelled      -> rejected
  ├─ browsing           -> allowed
  ├─ ai                 -> allowed
  └─ general/media/...  -> denied

SUBSCRIPTION_2+
  ├─ general
  ├─ browsing
  └─ ai
```

Production invariants:

1. `subscription_1` can enter only browsing and AI inventories.
2. EMBY-labelled subscription-1 nodes are rejected case-insensitively before inventory generation.
3. Explicit multipliers strictly above `2x` are rejected before classification and deduplication. Exactly `2x` and unmarked nodes remain eligible.
4. Media, messaging, download, ACL compatibility selectors, and final `MATCH` cannot reach `subscription_1`.
5. Source reachability is audited before and after qualification.

## ACL4SSR fidelity

`rules/acl4ssr.yaml` pins the ACL4SSR Online reference. ACL4SSR Online owns classification semantics; clash-relay owns source-safe inventories, qualification, and scheduling.

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

Manual region selection never silently crosses to another country. Automatic mode crosses regions only when the preferred region is unavailable. Scheduler history is private and anonymous; it can demote unstable live-qualified nodes but never expand source admission.

AI qualification is independent for OpenAI, Claude, and Gemini. Hong Kong is excluded before AI qualification and each service fails closed independently.

## Production release model

Production uses one unified private qualification pipeline:

```text
generated.yaml
  -> browsing + transport qualification
  -> AI qualification
  -> post-qualification policy audit
  -> every stable core in tools/mihomo-versions.json
  -> versioned Cloudflare KV release transaction
  -> fixed client-facing production key
```

`tools/mihomo-versions.json` is the only stable/prerelease Mihomo version source of truth. Documentation and workflows must not encode a second fixed stable-version matrix.

Each production candidate is staged as immutable release objects keyed by the exact SHA-256 of its bytes:

```text
<production>.release-v1.<sha256>.config
<production>.release-v1.<sha256>.manifest
<production>.current-release-v1
<production>.previous-release-v1
```

Cloudflare KV is not a cross-key transactional database. Publication therefore uses a compensating transaction: immutable bytes are staged and read-back verified first, the fixed production key is activated, release pointers are committed, and a failed pointer commit attempts to restore the previous exact production bytes.

Rollback resolves the previous release and validates it against the current repository policy plus every currently pinned stable Mihomo core before activation.

## Observability and privacy

Production proof and private longitudinal metrics contain aggregate operational metadata only: candidate SHA/size, qualified counts, regional cohort counts, AI service counts, release status, validation counts, and bounded stage timings. They intentionally exclude proxy names, servers, credentials, subscription URLs, and child-process diagnostics.

See [Production maturity](docs/production-maturity.md) for the P18.1-P23 operating contract.

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

Never write real subscription URLs into tracked YAML, README files, workflow arguments, or logs.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock -e .
clash-relay doctor --public-only
ruff check .
ruff format --check .
pytest -m "not integration"
python scripts/audit_documentation_contract.py
python scripts/audit_acl4ssr_fidelity.py
python scripts/repository_audit.py
```

## Documentation

- [Fork quickstart](docs/quickstart.md)
- [配置快速上手](docs/quickstart.zh-CN.md)
- [Production maturity](docs/production-maturity.md)
- [Configuration model](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [ACL4SSR routing model](docs/rules.md)
- [Security model](docs/security.md)
- [Publishing](docs/publishing.md)
- [Versioning and compatibility](docs/versioning.md)
- [Release checklist](docs/release-checklist.md)
