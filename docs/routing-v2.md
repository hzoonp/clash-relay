# Routing Model V2

Routing Model V2 separates classification, user presentation, permission boundaries, service qualification, region dimensions, and scheduler behavior. A rule target is not treated as a user-facing selector merely because ACL4SSR names it.

## Dimensions

Every classified route has independent dimensions:

- **scenario**: `direct`, `general`, `browsing`, `media`, `download`, `ai`, or `final`;
- **service**: optional application identity such as `youtube`, `netflix`, `openai`, `claude`, or `gemini`;
- **source_use**: the subscription permission domain that may supply nodes;
- **region**: a scheduling dimension on already-admitted nodes;
- **capability / cost**: optional node constraints;
- **scheduler profile**: how an eligible set is narrowed or ordered.

The dimensions are intersected. A later dimension can narrow an inventory but cannot re-enable a source, region, or node excluded by an earlier safety boundary.

## User surface

The default FlClash surface is intentionally fixed to three user decisions:

- `代理选择` — general scenario control;
- `网页浏览` — browsing scenario control;
- `人工智能` — generic AI scenario control.

Subscription IDs are sources, not groups. Countries are dimensions, not top-level groups. AI products are services, not top-level groups. Media and download schedulers are internal and do not add top-level controls.

The three visible groups are presentation controls, not the classification tree. YouTube, Netflix, downloads, OpenAI, Claude, Gemini, domestic media, and other application routes can all be active concurrently because every connection is classified independently in Mihomo `rule` mode.

## Declarative scenario contract

`policies.yaml` owns the Routing V2 declaration. Every scenario has a `source_use` and scheduler profile. The canonical policy is:

| Scenario | Source use | Scheduler profile |
| --- | --- | --- |
| direct | general | direct |
| general | general | connectivity |
| browsing | browsing | browsing |
| media | general | media |
| download | general | download |
| ai | ai | ai |
| final | general | connectivity |

This table is a permission contract, not documentation only. Production audit compares classified bindings with it and fails closed on disagreement.

## Deterministic internal targets

Hidden ACL4SSR application targets do not behave like invisible user selectors. A group declaring `route.deterministic: true` is materialized with exactly one next hop. Persisted `store-selected` state therefore cannot silently change application routing.

Canonical examples:

- `油管视频 -> 媒体自动`;
- `国外媒体 -> 媒体自动`;
- `电报消息 -> 代理选择`;
- `全球直连 -> DIRECT`;
- `广告拦截 -> REJECT`;
- `下载流量 -> 下载自动`;
- `漏网之鱼 -> 代理选择`, while final `MATCH -> 漏网之鱼` remains intact.

Automatic schedulers are different from selectors. Netflix uses a hidden automatic fallback that prefers the filtered Netflix-capable general pool and then `媒体自动`. It never gains access to the browsing-only source.

## Web classification

`ProxyGFWlist -> 网页浏览` remains the explicit generic foreign-web classification. China-domain/company-IP/GEOIP rules remain direct and are evaluated before the download rule.

Routing V2 deliberately does **not** claim that every non-China domain is web traffic. A domain not covered by an explicit browsing or application rule continues to the final route. This prevents a broad catch-all from accidentally granting `subscription_1` access to media, downloads, messaging, or unknown traffic.

Expanding foreign-web classification requires evidence and a reviewable rule source. It is not implemented by changing final `MATCH` semantics.

## Media

Media is an internal scenario with service-specific classification. It does not add a top-level UI group.

- YouTube and generic foreign media route through hidden `媒体自动`, backed only by the `general` permission domain.
- Netflix uses only the `general` permission domain and applies a Netflix-capable preference before the normal media scheduler.
- Bilibili and domestic media resolve directly under the canonical rules.
- `subscription_1` cannot enter media because it has no `general` permission.

The scheduler profile is declared separately from classification, so later latency/stability/capability improvements do not require new user-facing groups.

## Download

Download is a first-class internal scenario. The canonical production mode is `general_auto`:

```text
known domestic classification -> DIRECT
Download.list -> 下载流量 -> 下载自动 -> general inventory
ProxyGFWlist -> 网页浏览 -> browsing inventory
```

The ordering is intentional: known domestic rules run before `Download.list`, and `Download.list` runs before generic `ProxyGFWlist`. Matched download traffic therefore uses the general automatic inventory rather than the browsing inventory. `subscription_1` remains unreachable from the download scenario.

ACL4SSR `Download.list` includes process-name classifications. These are ordinary routing hints only; process names are never a source-permission or security boundary.

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

Hong Kong is excluded before service qualification and cannot be re-enabled by a service. The preferred order is materialized independently for OpenAI, Claude, and Gemini. A service/region combination with no qualified nodes is not generated.

`人工智能` remains the generic user-facing AI control. Service-specific rules route directly to their independently qualified hidden targets; the visible group does not merge service qualification sets.

## Sparse materialization

Routing Model V2 is logically `scenario × service × region × capability`, but the physical Mihomo graph is sparse. Empty service/region combinations are not materialized. A service with no qualified region gets one fail-closed `REJECT` target instead of a Cartesian product of empty proxy groups.

This keeps the generated configuration bounded as subscriptions, countries, and services grow.

## Fail-closed audit

Production audit checks both source reachability and Routing V2 declarations before publication. Among other invariants it verifies:

- each classified scenario's `source_use` matches the declarative policy;
- deterministic hidden targets are actually one-hop hidden runtime groups;
- media and download remain in the general permission domain;
- AI pools do not materialize excluded regions;
- post-qualification OpenAI/Claude/Gemini targets reference only their own service anchors;
- the final qualified canonical UI exposes only `代理选择`, `网页浏览`, and `人工智能`.

AI qualification has two audit stages. Before qualification, temporary AI country wrappers may exist so the service resolver can construct service-specific anchors. After qualification, those wrappers are hidden and only the three canonical user controls remain visible.

## Complex concurrent scenarios

Tests explicitly cover simultaneous route intents for domestic web, explicit foreign web, YouTube, Netflix, domestic/foreign media, downloads, OpenAI, generic AI, and final unknown traffic. Specific application, AI, and download classifications stay ahead of generic `ProxyGFWlist`; final traffic remains separate.

The same complex generated graph is validated by both pinned Mihomo cores in integration CI.

## Drift guard

`Routing V2 Drift Guard` is a separate no-Secrets workflow. It validates the finalized declarations and configuration graph after cutover. It does **not** inspect runtime traffic and never records domains, node names, endpoints, credentials, subscription URLs, or user activity.

It fails when the canonical graph drifts from the finalized policy, including media/download scheduler wiring or AI region ordering. Its aggregate report records only configuration facts such as explicit foreign-web rule-source count, scheduler application state, and AI region policy state.

A drift-guard failure cannot publish or replace production configuration. Production remains owned by the existing fail-closed publish workflow.

## Non-negotiable boundaries

- `subscription_1` remains usable only by `browsing` and `ai` inventories.
- AI excludes Hong Kong before service qualification.
- OpenAI, Claude, and Gemini remain independently qualified.
- media and download use only the `general` permission domain.
- `ProxyGFWlist -> 网页浏览` remains canonical.
- final `MATCH -> 漏网之鱼` remains canonical.
- no process-name rule is used as a source-permission or security boundary.
- production publication remains fail-closed after source reachability audit and dual-Mihomo validation.
