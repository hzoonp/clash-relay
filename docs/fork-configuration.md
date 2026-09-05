# Fork configuration surface

A normal fork does **not** require understanding the whole internal architecture. Start with the smallest supported surface and move into advanced policy files only when a concrete requirement needs them.

## The three things a normal fork owns

1. **Subscription credentials** — keep real URLs only in the `CLASH_RELAY_SUBSCRIPTIONS` repository Secret.
2. **Subscription policy** — edit `subscriptions.yaml` only when enabling a source or changing its declared uses, country boundary, name admission rules, or multiplier ceiling.
3. **Production publication** — configure the Cloudflare KV Secret/Variables, dry-run with `publish=false`, then intentionally publish.

Everything else has a safe repository default. You do not need to edit qualification internals, scheduler history, release transactions, RuntimeGraph code, or Mihomo version pins for a routine fork.

## What to edit for a specific goal

| Goal | Primary surface | Usually leave alone |
| --- | --- | --- |
| Add or remove a subscription | `subscriptions.yaml` + `CLASH_RELAY_SUBSCRIPTIONS` | routing, scheduler, release code |
| Restrict which scenarios can use a source | `subscriptions.yaml` → `allowed_uses` | proxy groups generated at runtime |
| Reject source-specific node names | `subscriptions.yaml` admission fields | capability classification rules |
| Limit explicit node multipliers | `subscriptions.yaml` → `max_node_multiplier` | selector ordering |
| Change public routing behavior | `policies/routing.yaml` | subscription credentials |
| Change pool topology/regions | `policies/topology.yaml` | production release transaction |
| Change qualification behavior | qualification policy files only when intentionally redesigning health semantics | Cloudflare publication code |
| Change scheduled refresh cadence | `.github/workflows/publish.yml` | candidate generation semantics |
| Recover the previous production release | manual rollback workflow | KV keys by hand |

## Default source policy

The canonical repository deliberately treats `subscription_1` differently:

```text
subscription_1
  allowed uses: browsing, ai
  explicit multiplier > 2x: rejected
  exact 2x: retained
  EMBY-labelled nodes: rejected by admission policy

subscription_2+
  allowed uses: general, browsing, ai
```

`ingest_order` exists only to make ingestion and deterministic output stable. It is not a quality score and must not be used as a routing preference.

## Advanced surfaces

Only edit these when the requirement explicitly calls for them:

- `policies/routing.yaml` — scenario bindings and public routing behavior;
- `policies/topology.yaml` — pools, regions, probes, and source-use selectors;
- `policies/scheduling.yaml` — scheduler policy;
- classification fragments — country/capability semantics;
- `promotion-guard.yaml` — release availability/degradation thresholds;
- `tools/mihomo-versions.json` — validated stable/prerelease core matrix.

Changes to advanced surfaces still pass the same fail-closed validation and production lifecycle. A fork should not bypass those gates to make a policy change easier.

## Never edit or publish these as configuration

- generated production `config.yaml` bytes containing proxy credentials;
- subscription payloads or real subscription URLs in tracked files;
- private qualified candidates;
- node-level qualification results;
- generated KV release objects or pointers by hand.

Use `clash-relay doctor --public-only` after public declaration changes, then run a private doctor check and a `publish=false` production dry run before the first intentional publication.
