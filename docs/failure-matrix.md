# Production failure and degradation matrix

Production is intentionally fail-closed at the publication boundary. A failed run may degrade an individual optional source or AI service where explicitly documented, but it must never replace the last validated `production-config` unless the exact candidate passes all mandatory gates.

| Failure | Required behavior |
| --- | --- |
| One optional subscription fetch/parse fails with `on_error: skip` | Record the source as failed and continue only if `minimum_successful_subscriptions`, `minimum_usable_nodes`, and every required policy pool still satisfy generation validation. |
| An explicitly required / `on_error: fail` subscription fails | Abort generation; do not publish. |
| Successful subscriptions fall below `minimum_successful_subscriptions` | Abort generation; do not publish. |
| General inventory/pool has no eligible nodes | Generator/selector fails the required pool; do not publish. Never borrow browsing/AI-only sources. |
| Browsing live qualification leaves no eligible provider payload | Browsing qualification fails; do not publish. Historical state cannot revive a live-failed node. |
| OpenAI has zero qualified nodes | Fail closed only for OpenAI by rewriting its service target to `REJECT`; Claude/Gemini remain independently qualified. |
| Claude has zero qualified nodes | Fail closed only for Claude; OpenAI/Gemini remain independent. |
| Gemini has zero qualified nodes | Fail closed only for Gemini; OpenAI/Claude remain independent. |
| ACL4SSR/rule acquisition or parsing fails | Candidate generation fails before publication; existing production stays untouched. |
| Source-to-scenario reachability audit fails before or after qualification | Abort; do not publish. |
| Qualified candidate collapses below the configured Promotion Guard inventory/source-diversity ratio relative to the current production value | Abort before core-matrix validation and before release activation. Keep the exact current production bytes active. A source-use threshold applies only when that use exists in the current production topology. |
| Current production baseline is absent because this is the first publication | Promotion Guard records `first_release` and allows the normal remaining fail-closed gates to decide publication. |
| Any pinned stable Mihomo core declared in `tools/mihomo-versions.json` rejects the exact candidate | Abort before production activation. Workflow YAML does not maintain a second core-version list. |
| Cloudflare versioned release transaction fails before activation | Abort; existing production remains active. |
| Cloudflare versioned release transaction fails after the client-facing key changes but before pointer commit | Attempt compensating restoration of the previous exact production bytes and release pointers; report failure if compensation cannot be completed. |
| Scheduler history is missing/corrupt/unavailable | Use current live browsing qualification without historical narrowing. Do not weaken the 3/3, 2/3, <2/3 boundary. |
| AI qualification cache is missing/corrupt/unavailable | Perform live per-service AI qualification instead of trusting cache. |
| Post-commit AI cache, scheduler history, or production metrics persistence fails | Keep the already committed validated production release active, warn, and rebuild/advance derived state on a later successful run. |
| Previous release metadata is absent/unreadable | Manual rollback refuses before activation. Production remains unchanged. |
| Rollback candidate fails the current production/Routing V2 policy audit or any pinned stable Mihomo core | Rollback aborts; production remains unchanged. |

## Publication ordering invariant

The production write ordering is:

```text
private generation
  -> ProductionPipeline pre-qualification composite audit
  -> unified qualification
       -> live browsing / transport qualification
       -> per-service AI qualification
       -> OpenAI client-path hardening
  -> ProductionPipeline post-qualification composite audit
  -> fetch exact client-visible current production baseline
  -> Promotion Guard relative degradation check
  -> every stable Mihomo core in tools/mihomo-versions.json
  -> stage and read-back verify immutable SHA-256 release objects
  -> activate exact production-config bytes and commit release pointers
  -> persist best-effort AI cache / scheduler history / aggregate production metrics
  -> aggregate production proof
```

Any failure before production activation leaves the previous production value active. The Promotion Guard is deliberately positioned before the stable-core matrix and release transaction because its purpose is to reject a technically valid but sharply degraded refresh before any production mutation occurs. A failure during the multi-key Cloudflare KV commit invokes the compensating P17 restoration path when previous production bytes exist. Auxiliary state is derived optimization/observability data and must never become a prerequisite that relaxes a live safety gate or falsely turn an already committed validated release into a failed deployment.

## Rollback ordering invariant

Manual rollback requires explicit confirmation and follows the current repository safety contract:

```text
resolve previous-release-v1 (legacy previous-v1 fallback)
  -> audit historical candidate against current production/source policy
  -> audit current Routing V2 contract
  -> validate every stable Mihomo core in tools/mihomo-versions.json
  -> activate through the same versioned release transaction
```

A historical config that remains valid Mihomo syntax but violates the current source-permission or Routing V2 contract is not eligible for rollback.

## Source isolation during degradation

Degradation is not permission escalation. In particular, a source admitted only for `browsing` and `ai` cannot be used to rescue `general`, media, messaging, games, downloads, Microsoft/cloud application routes, or final `MATCH`. Empty pools fail according to their policy rather than borrowing an unauthorized source.
