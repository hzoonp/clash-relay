# Production failure and degradation matrix

Production is intentionally fail-closed at the publication boundary. A failed run may degrade an individual optional source or AI service where explicitly documented, but it must never replace the last validated `production-config` unless the exact candidate passes all mandatory gates.

## Failure matrix

| Failure | Required behavior |
| --- | --- |
| One optional subscription fetch/parse fails with `on_error: skip` | Record the source as failed and continue only if `minimum_successful_subscriptions`, `minimum_usable_nodes`, and every required policy pool still satisfy generation validation. |
| Subscription request times out, returns HTTP/network failure, or cannot resolve | Treat it as a source fetch failure. Do not echo the subscription URL/credentials in public diagnostics. Required sources fail the run; optional sources may degrade only within normal minimum thresholds. |
| Subscription endpoint returns HTML/error text instead of Clash YAML/URI/base64 | Reject the untrusted payload as an invalid subscription. Never reinterpret error HTML as nodes. |
| Subscription is empty or contains zero usable proxies | It contributes zero inventory. It cannot satisfy minimum node/source thresholds and cannot synthesize fallback nodes. |
| An explicitly required / `on_error: fail` subscription fails | Abort generation; do not publish. |
| Successful subscriptions fall below `minimum_successful_subscriptions` | Abort generation; do not publish. |
| General inventory/pool has no eligible nodes | Generator/selector fails the required pool; do not publish. Never borrow browsing/AI-only sources. |
| Browsing live qualification has a whole-probe infrastructure failure with zero successful samples and only timeout/probe/HTTP 429/5xx evidence | Classify as `transient` and retry the complete browsing/transport stage once from the immutable generated candidate. |
| Browsing live qualification partially succeeds but leaves a provider empty | Treat as a policy/inventory rejection, not transient. Do not retry it into production. |
| Browsing or transport qualification produces an unstructured child failure | Classify as `protocol_error`; fail closed without retry. |
| Mihomo rejects, exits, or never becomes ready during qualification | Classify as `core_rejection`; fail closed without retry. |
| Browsing live qualification leaves no eligible provider payload | Browsing qualification fails; do not publish. Historical state cannot revive a live-failed node. |
| General transport qualification leaves no TCP-qualified or UDP-qualified automatic inventory | Fail closed. Never publish unsafe automatic general/media/messaging selectors. |
| OpenAI has zero qualified nodes | Fail closed only for OpenAI by rewriting its service target to `REJECT`; Claude/Gemini remain independently qualified. |
| Claude has zero qualified nodes | Fail closed only for Claude; OpenAI/Gemini remain independent. |
| Gemini has zero qualified nodes | Fail closed only for Gemini; OpenAI/Claude remain independent. |
| ACL4SSR/rule acquisition or parsing fails | Candidate generation fails before publication; existing production stays untouched. |
| Source-to-scenario reachability audit fails before or after qualification | Abort; do not publish. |
| Qualified candidate collapses below the configured Promotion Guard inventory/source-diversity ratio relative to the current production value | Abort before core-matrix validation and before release activation. Keep the exact current production bytes active. A source-use threshold applies only when that use exists in the current production topology. |
| Current production baseline is absent because this is the first publication | Promotion Guard records `first_release` and allows the normal remaining fail-closed gates to decide publication. |
| Any pinned stable Mihomo core declared in `tools/mihomo-versions.json` rejects the exact candidate | Abort before production activation. Workflow YAML does not maintain a second core-version list. |
| Cloudflare request fails with timeout, HTTP 429, or HTTP 5xx before client-visible activation | Abort. Exact current production bytes and release pointers remain unchanged. |
| Cloudflare versioned release PUT succeeds but its response is lost | Exact read-back determines whether the intended bytes committed; continue only when read-back matches exactly. |
| Cloudflare versioned release transaction fails after the client-facing key changes but before pointer commit | Attempt compensating restoration of the previous exact production bytes and release pointers; report failure if compensation cannot be completed. |
| Current-pointer PUT succeeds but its response is lost | Exact pointer read-back may recover the transaction only if the intended release ID is present. |
| Scheduler history is missing/corrupt/unavailable | Use current live browsing qualification without historical narrowing. Do not weaken the declared live success boundary. |
| AI qualification cache is missing/corrupt/unavailable | Perform live per-service AI qualification instead of trusting cache. |
| Aggregate production metrics are missing/corrupt/unavailable | Start from a sanitized empty bounded ring. Metrics never participate in admission or routing policy. |
| Post-commit AI cache, scheduler history, or production metrics persistence fails | Keep the already committed validated production release active, warn, and rebuild/advance derived state on a later successful run. |
| Production proof or release-manifest rendering fails after the release transaction already committed | Keep the release active, report post-release observability as unavailable, and leave release phase at `published` rather than falsely claiming the client-visible publication never happened. |
| Production proof or release-manifest rendering fails during a dry run | Fail the dry run because no client-visible commit needs to be preserved. |
| Previous release metadata is absent/unreadable/corrupt | Manual rollback refuses before activation. Production remains unchanged. |
| Rollback candidate fails the current production/Routing V2 policy audit or any pinned stable Mihomo core | Rollback aborts; production remains unchanged. |
| Rollback rehearsal activates the exact previous immutable release | The same release transaction must make that release current and make the formerly current release the new previous pointer, preserving exact immutable manifests for both. |
| Two publish-triggering Actions overlap | The workflow concurrency group serializes them with `cancel-in-progress: false`; production transactions do not run concurrently and an older in-flight release is not cancelled mid-commit. |

## Publication ordering invariant

The production write ordering is:

```text
private generation
  -> load private derived state
  -> primary pinned Mihomo acquisition
  -> ProductionPipeline pre-qualification composite audit
  -> unified qualification
       -> live browsing / transport qualification
          -> at most one immutable-candidate retry for typed transient whole-probe failure
       -> per-service AI qualification
       -> OpenAI client-path hardening
  -> ProductionPipeline post-qualification composite audit
  -> fetch exact client-visible current production baseline
  -> Promotion Guard relative degradation check
  -> every stable Mihomo core in tools/mihomo-versions.json
  -> stage and read-back verify immutable SHA-256 release objects
  -> activate exact production-config bytes and commit release pointers
  -> persist best-effort AI cache / scheduler history
  -> aggregate production proof / release manifest
  -> persist bounded aggregate-only production metrics
```

The release-progress contract is:

```text
prepared -> qualified -> promoted -> published -> verified
```

A dry run uses `prepared -> qualified -> promoted -> verified` because it never enters the client-visible `published` phase.

Any failure before production activation leaves the previous production value active. The Promotion Guard is deliberately positioned before the stable-core matrix and release transaction because its purpose is to reject a technically valid but sharply degraded refresh before any production mutation occurs. A failure during the multi-key Cloudflare KV commit invokes the compensating P17 restoration path when previous production bytes exist. Auxiliary state and observability are derived data and must never become prerequisites that relax a live safety gate. If client-visible publication has already committed, a later proof/manifest/metrics problem is reported as a post-release observability issue instead of falsely turning the committed release into a pre-publication failure.

## Retry invariant

Retry is not a second policy opinion. P39 allows a single retry only when the child qualification protocol explicitly classifies a whole browsing probe as `transient` and reports zero successful samples with only bounded infrastructure-style outcomes. Configuration errors, policy/inventory rejection, transport admission failure, Mihomo/core failure, protocol errors, and partial live success are not retryable. Every retry starts from the immutable generated candidate so a rejected attempt cannot mutate the next attempt.

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

## Privacy invariant

Public logs, proof, release manifest, doctor output, failure categories, and metrics may contain aggregate counts, hashes, stage names, durations, and bounded status values. They must not contain subscription URLs, node names, node servers, ports, credentials, Cloudflare tokens, probe secrets, private child stderr, or generated configuration bytes.
