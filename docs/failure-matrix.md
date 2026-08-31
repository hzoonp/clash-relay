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
| Mihomo v1.19.30 or v1.19.29 rejects the exact candidate | Abort; do not snapshot over the recovery slot and do not publish. |
| Cloudflare production PUT fails | The workflow fails; auxiliary AI/history state is not advanced because those steps occur only after successful production publication. |
| Scheduler history is missing/corrupt/unavailable | Use current live browsing qualification without historical narrowing. Do not weaken the 3/3, 2/3, <2/3 boundary. |
| AI qualification cache is missing/corrupt/unavailable | Perform live per-service AI qualification instead of trusting cache. |
| Previous-good recovery slot is absent/unreadable | Manual rollback refuses before activation. Production remains unchanged. |
| Rollback candidate fails either Mihomo core | Rollback aborts; production remains unchanged. |

## Publication ordering invariant

The production write ordering is:

```text
private generation
  -> source reachability audit
  -> live browsing qualification
  -> per-service AI qualification
  -> post-qualification reachability audit
  -> Mihomo v1.19.30 + v1.19.29
  -> preserve previous-good when applicable
  -> publish exact production-config bytes
  -> persist auxiliary AI cache / scheduler history
  -> aggregate production proof
```

Any failure before the production write leaves the previous production value active. Auxiliary state is optimization/observability data and must never become a prerequisite that relaxes a live safety gate.

## Source isolation during degradation

Degradation is not permission escalation. In particular, a source admitted only for `browsing` and `ai` cannot be used to rescue `general`, media, messaging, games, downloads, Microsoft/cloud application routes, or final `MATCH`. Empty pools fail according to their policy rather than borrowing an unauthorized source.
