# Production failure and degradation matrix

Production is fail closed at the publication boundary. A failed run may degrade an optional source or independent AI service only where explicitly documented; it must never replace the last validated `production-config` unless the exact candidate passes all mandatory gates.

## Failure matrix

| Failure | Required behavior |
| --- | --- |
| One optional subscription fetch/parse fails with `on_error: skip` | Record the source as failed and continue only if minimum source/node requirements and every required policy pool still pass. |
| Subscription request times out, returns HTTP/network failure, or cannot resolve | Treat it as source failure; never echo the private URL/credentials. Required sources fail the run. |
| Subscription endpoint returns HTML/error text instead of supported subscription data | Reject the untrusted payload; never reinterpret error HTML as nodes. |
| Subscription is empty or has zero usable proxies | It contributes zero inventory and cannot synthesize fallback nodes. |
| Required source or global minimum inventory fails | Abort generation; do not publish. |
| General pool has no eligible nodes | Fail closed; never borrow browsing/AI-only sources. |
| Browsing probe has a whole-probe transient infrastructure failure with zero successes and only bounded transient outcomes | Retry the complete browsing/transport stage once from the immutable generated candidate. |
| Browsing partially succeeds but leaves a required provider empty | Treat as policy/inventory rejection; do not retry it into production. |
| Qualification produces an unstructured protocol failure | Classify as `protocol_error`; fail closed without retry. |
| Mihomo rejects/exits/never becomes ready during qualification | Classify as `core_rejection`; fail closed. |
| Browsing or transport qualification leaves required automatic inventory empty | Fail closed. Historical state cannot revive a live-failed node. |
| OpenAI has zero qualified nodes | Fail closed for the OpenAI service route; unrelated AI services remain independent. |
| Claude has zero qualified nodes | Fail closed for Claude; unrelated AI services remain independent. |
| Gemini has zero qualified nodes | Fail closed for Gemini; unrelated AI services remain independent. |
| Declared service client-path hardening cannot be applied | Fail closed; do not bypass the provider implementation or restore an old runtime shape. |
| ACL4SSR/rule acquisition or parsing fails | Candidate generation fails before publication. |
| Source-to-scenario or Routing V2 audit fails before/after qualification | Abort; do not publish. |
| Promotion Guard detects excessive inventory/source-diversity collapse relative to current production | Abort before release activation and keep exact current bytes active. |
| Current production baseline is absent for the first publication | Promotion Guard records first-release semantics; all other mandatory gates still apply. |
| Any stable Mihomo core declared in `tools/mihomo-versions.json` rejects the exact candidate | Abort before production activation. |
| Cloudflare request fails before client-visible activation | Abort; current production bytes and pointers stay unchanged. |
| Immutable release PUT succeeds but response is lost | Exact read-back determines whether intended bytes committed. |
| Client-facing key changes but pointer commit then fails | Attempt compensating restoration of previous exact production bytes and pointer state; explicitly report incomplete compensation. |
| Current/previous pointer PUT response is lost | Exact pointer read-back may recover only when the intended release ID is present. |
| Scheduler history is missing/corrupt | Use current live browsing qualification without historical narrowing. |
| AI qualification cache is missing/corrupt | Perform live per-service qualification. |
| Production metrics or operational SLO state is missing/corrupt | Reset/sanitize bounded aggregate state; never affect admission policy. |
| Post-commit cache/history/metrics/SLO persistence fails | Keep the already committed validated release active and report best-effort observability degradation. |
| Production proof fails after client-visible release committed | Keep release active and report post-commit observability failure. |
| Production proof fails during dry run | Fail the dry run because no committed release must be preserved. |
| Versioned previous-release pointer is absent | Rollback refuses before activation. |
| Previous release bytes are missing, SHA-mismatched, or manifest-mismatched | Rollback refuses before activation. |
| Historical rollback candidate fails current source/Routing/OpenAI client-path policy or any stable Mihomo core | Rollback aborts; production remains unchanged. |
| Rollback activates the exact previous immutable release | The same transaction makes it current and makes the formerly current release the new previous pointer, preserving immutable manifests. |
| Two publish-triggering Actions overlap | Workflow concurrency serializes them with `cancel-in-progress: false`; an older in-flight transaction is not cancelled mid-commit. |

## Publication ordering invariant

```text
private declarations + Secrets
  -> subscription I/O / NodeInventory
  -> PolicyCompiler / RuntimeGraph / MihomoSerializer
  -> generated current-policy audit
  -> qualification
       -> live browsing / transport
          -> at most one typed transient retry from immutable generated candidate
       -> ordered ServiceQualification registry
       -> declared service client-path hardening
  -> qualified current-policy audit
  -> fetch exact current production baseline
  -> Promotion Guard
  -> every stable Mihomo core in tools/mihomo-versions.json
  -> stage/read-back verify immutable SHA-256 release objects
  -> activate exact production-config bytes and commit release pointers
  -> production proof
  -> best-effort AI cache / scheduler history / metrics / operational SLO
```

Release progress is:

```text
prepared -> qualified -> promoted -> published -> verified
```

A dry run uses `prepared -> qualified -> promoted -> verified` because it never enters the client-visible `published` phase.

Any failure before activation leaves the previous production value active. Promotion Guard is deliberately before the stable-core matrix and release transaction so a technically valid but sharply degraded refresh is rejected before production mutation. Multi-key commit failure invokes compensating restoration when previous bytes are available. Post-commit auxiliary-state failures never relax a live gate or falsely claim the committed release did not occur.

## Retry invariant

Retry is not a second policy opinion. A single retry is permitted only for the typed transient whole-browsing-probe failure class with zero successful samples. Configuration, policy/inventory rejection, transport admission failure, core rejection, protocol errors, and partial live success are not retryable. Every retry starts from the immutable generated candidate.

## Rollback ordering invariant

Manual rollback requires explicit confirmation:

```text
resolve versioned previous-release-v1
  -> verify release-id SHA-256 and immutable manifest
  -> audit historical candidate against current source / Routing V2 / service contracts
  -> require current OpenAI client-path contract
  -> validate every stable Mihomo core in tools/mihomo-versions.json
  -> activate through the same versioned release transaction
```

There is no legacy `previous-v1` value fallback and no legacy OpenAI runtime-shape audit flag in v2. Historical bytes are eligible only when they still satisfy the current complete safety contract.

## Source isolation during degradation

Degradation is not permission escalation. A source admitted only for `browsing` and `ai` cannot rescue `general`, media, messaging, games, downloads, cloud application routes, or final `MATCH`. Empty pools fail according to declared policy instead of borrowing an unauthorized source.

## Privacy invariant

Public logs, proof, release manifest, doctor output, failure categories, metrics, and SLO summaries may contain aggregate counts, hashes, stage names, durations, and bounded status values. They must not contain subscription URLs, node names, node servers, ports, credentials, Cloudflare tokens, private probe data, raw child stderr, or generated configuration bytes.
