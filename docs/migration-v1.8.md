# Migrating to v1.8.0

v1.8.0 removes implicit Routing V2/PolicyContract defaults and makes the canonical policy layout a v2 manifest. Client-facing Cloudflare and FlClash contracts do not change.

## 1. Declare routing explicitly

Before upgrading, ensure the normalized policy document contains:

```yaml
routing:
  version: 2
  scenarios: ...
  browsing: ...
  ai: ...
  download: ...
  contract: ...
```

Projects that relied on Python's old implicit routing/default group names must copy their intended semantics into `routing.contract`. v1.8.0 fails closed instead of guessing them.

## 2. Migrate a monolithic v1 policy file

For a project with a monolithic `version: 1` `policies.yaml` and an explicit routing contract:

```bash
python scripts/migrate_policy_v2.py \
  --input policies.yaml \
  --output policies.yaml \
  --fragment-dir policies \
  --force
```

The migration writes four fragments (`routing`, `scheduling`, `classification`, and `topology`), replaces the root with a `version: 2` manifest, reloads it through `PolicyDocument`, and fails if normalized semantics changed.

The library still understands physical v1 policy documents for compatibility, but canonical production uses v2.

## 3. Custom production workflows

The supported production entrypoint is now:

```bash
python scripts/run_production_release.py --dry-run
python scripts/run_production_release.py --publish
```

GitHub Actions push/schedule/manual semantics are resolved in the application layer. Forks that copied business stages into their Workflow should remove that duplicate orchestration or wrap the single production entrypoint instead.

## 4. `services.yaml`

Canonical production keeps:

```yaml
version: 1
services: []
```

This is a compatibility-only extension point. Do not move Routing V2, AI qualification, or scheduler truth into generic Service objects.

## 5. Release and rollback compatibility

v1.8.0 does not change:

- the fixed client-facing Cloudflare KV key;
- the immutable `.release-v1.<sha>.config` / `.manifest` object format;
- `current-release-v1` and `previous-release-v1` pointers;
- compensating rollback behavior;
- the six canonical FlClash scenario names;
- `subscription_1` browsing/AI-only admission and >2x filtering.

The new P37 release manifest is an aggregate observability document and is separate from the immutable rollback manifest.
