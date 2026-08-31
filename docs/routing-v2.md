# Routing Model V2

Routing Model V2 separates classification, user presentation, permission boundaries, service qualification, region dimensions, and scheduler behavior. A rule target is no longer treated as a user-facing selector merely because ACL4SSR names it.

## Dimensions

Every classified route has these independent dimensions:

- **scenario**: `direct`, `general`, `browsing`, `media`, `download`, `ai`, or `final`;
- **service**: optional application identity such as `youtube`, `netflix`, `openai`, `claude`, or `gemini`;
- **source_use**: the subscription permission domain that may supply nodes;
- **region**: a scheduling dimension on already-admitted nodes;
- **capability / cost**: optional node constraints;
- **scheduler profile**: how an eligible set is narrowed or ordered.

The dimensions are intersected. A later dimension can narrow an inventory but cannot re-enable a source, region, or node excluded by an earlier safety boundary.

## User surface

The default FlClash surface remains intentionally small:

- `代理选择` — general scenario control;
- `网页浏览` — browsing scenario control;
- `人工智能` — generic AI scenario control.

Subscription IDs are sources, not groups. Countries are dimensions, not top-level groups. AI products are services, not top-level groups.

The three visible groups are presentation controls, not the classification tree. YouTube, Netflix, downloads, OpenAI, Claude, Gemini, domestic media, and other application routes can all be active concurrently because every connection is classified independently in Mihomo `rule` mode.

## Declarative scenario contract

`policies.yaml` owns a Routing V2 declaration. Every scenario has a `source_use` and a scheduler profile. The canonical policy is:

| Scenario | Source use | Scheduler profile |
| --- | --- | --- |
| direct | general | direct |
| general | general | connectivity |
| browsing | browsing | browsing |
| media | general | media |
| download | general | download |
| ai | ai | ai |
| final | general | connectivity |

This table is a permission contract, not merely documentation. Production audit compares every classified binding with it and fails closed on disagreement.

## Deterministic internal targets

Hidden ACL4SSR application targets must not behave like invisible user selectors. A group declaring `route.deterministic: true` is materialized with exactly one next hop. Persisted `store-selected` state therefore cannot silently change application routing.

Examples in the shadow baseline:

- `油管视频 -> 代理选择`;
- `电报消息 -> 代理选择`;
- `全球直连 -> DIRECT`;
- `广告拦截 -> REJECT`;
- `漏网之鱼 -> 代理选择` while final `MATCH -> 漏网之鱼` remains intact.

Automatic schedulers are different from selectors. Netflix uses an automatic hidden route and may prefer a filtered Netflix-capable general pool before falling back to the normal general automatic pool. It never gains access to the browsing-only source.

## Web classification

`ProxyGFWlist -> 网页浏览` remains the explicit generic foreign-web classification. China-domain/company-IP rules remain direct.

Routing V2 deliberately does **not** claim that every non-China domain is web traffic. A domain that is not covered by an explicit browsing or application rule continues to the final route. This prevents a broad catch-all from accidentally granting `subscription_1` access to media, downloads, messaging, or unknown traffic.

Expanding foreign-web classification requires evidence and a reviewable rule source. It is not implemented by changing final `MATCH` semantics.

## Media

Media is an internal scenario with service-specific classification. It does not add a top-level UI group.

- YouTube and generic foreign media use the general permission domain.
- Netflix uses only the general permission domain and may apply a Netflix-capable automatic preference.
- Bilibili and domestic media may resolve directly.
- `subscription_1` cannot enter media because it has no `general` permission.

The scheduler profile is declared separately from classification, so later latency/stability/capability improvements do not require new user-facing groups.

## Download

Download is a first-class internal scenario rather than an unlabelled direct rule. During shadow validation its canonical mode remains `direct`, preserving the current production behavior.

The prospective cutover mode is `general_auto`. That mode will route matched download traffic through the general automatic inventory, never through the browsing inventory. Therefore `subscription_1` remains unreachable even when download behavior changes.

ACL4SSR `Download.list` includes process-name classifications. These may be used as ordinary routing hints, but process names are never treated as a source-permission or security boundary.

## AI service and region model

AI keeps one scenario domain and independent service qualification:

```text
AI
  -> OpenAI-qualified nodes
  -> Claude-qualified nodes
  -> Gemini-qualified nodes
```

The qualified sets are not merged. Each service target can reference only anchors created for that same service; an empty service fails closed to `REJECT`.

Region is a second dimension over the already service-qualified set. Canonical policy:

```text
excluded: HK
preferred: US -> SG -> JP -> TW -> KR -> OTHER
```

The scenario-level HK exclusion is a hard constraint and cannot be re-enabled by a service.

The preferred order is a **declarative automatic-routing policy**. It is intentionally not represented as a fake runtime promise that selecting `人工智能 -> 美国` can synchronously rewrite three independent Mihomo rule targets. At cutover, generation/qualification materializes service-specific anchors in the declared region order.

## On-demand materialization

Routing Model V2 is logically `scenario × service × region × capability`, but the physical Mihomo graph is sparse. Empty service/region combinations are not materialized. A service with no qualified region gets one fail-closed `REJECT` target instead of a Cartesian product of empty proxy groups.

This keeps the generated configuration bounded as subscriptions, countries, and services grow.

## Fail-closed audit

The production audit checks both source reachability and Routing V2 declarations before publication. Among other invariants it verifies:

- the scenario's declared `source_use` matches classification metadata;
- deterministic hidden targets are actually one-hop hidden runtime groups;
- AI pools do not materialize excluded regions;
- post-qualification OpenAI/Claude/Gemini targets reference only their own service anchors;
- the final qualified canonical UI exposes only `代理选择`, `网页浏览`, and `人工智能`.

AI qualification has two audit stages. Before qualification, temporary AI country wrappers may exist so the service resolver can construct service-specific anchors. After qualification, those wrappers are hidden and only the three canonical user controls remain visible.

## Complex-scenario matrix

Tests explicitly cover concurrent route intents for domestic web, explicit foreign web, YouTube, Netflix, domestic/foreign media, downloads, OpenAI, generic AI, and final unknown traffic. Specific application and AI classifications stay ahead of generic `ProxyGFWlist`; final traffic remains separate.

The same complex generated graph is validated by both pinned Mihomo cores in integration CI.

## Shadow validation

`Routing V2 Shadow` is a separate no-Secrets workflow. It compares only declarations and configuration graphs. It does **not** inspect runtime traffic and never records domains, node names, endpoints, credentials, subscription URLs, or user activity.

The shadow report records aggregate facts such as:

- number of explicit foreign-web rule sources;
- whether the foreign-web classifier is being widened;
- how many download rule sources would change under cutover;
- how many media rule sources are candidates for automatic scheduling;
- current versus declared AI region order.

A shadow failure cannot publish or replace production configuration. Production remains owned by the existing fail-closed publish workflow.

## Non-negotiable boundaries

- `subscription_1` remains usable only by `browsing` and `ai` inventories.
- AI excludes Hong Kong before service qualification.
- OpenAI, Claude, and Gemini remain independently qualified.
- `ProxyGFWlist -> 网页浏览` remains canonical.
- final `MATCH -> 漏网之鱼` remains canonical.
- no process-name rule is used as a source-permission or security boundary.
- production publication remains fail-closed after source reachability audit and dual-Mihomo validation.
