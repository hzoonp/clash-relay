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

## Deterministic internal targets

Hidden ACL4SSR application targets must not behave like invisible user selectors. A group declaring `route.deterministic: true` is materialized with exactly one next hop. Persisted `store-selected` state therefore cannot silently change application routing.

Examples:

- `油管视频 -> 代理选择`;
- `电报消息 -> 代理选择`;
- `全球直连 -> DIRECT`;
- `广告拦截 -> REJECT`;
- `漏网之鱼 -> 代理选择` while final `MATCH -> 漏网之鱼` remains intact.

Automatic schedulers are different from selectors. For example, Netflix may use an automatic fallback from a filtered Netflix-capable pool to the normal general automatic pool without becoming user-visible.

## Scenario matrix

The pinned ACL4SSR classification remains ordered and explicit. Routing metadata records the intended meaning without relying on group names:

- China/LAN/direct rules -> `direct`;
- `ProxyGFWlist` -> `browsing/foreign_web`;
- YouTube/Netflix/Bilibili/media lists -> `media/<service>`;
- `Download.list` -> `download/download`;
- AI/OpenAI rules -> `ai/<service>`;
- final `MATCH` -> `final` on the general graph.

The model does not claim that every non-China domain can be identified as web traffic. A domain that is not covered by an explicit browsing/application rule continues to reach final routing. Expanding classification requires evidence and must not be implemented by changing final `MATCH` semantics.

## Non-negotiable boundaries

- `subscription_1` remains usable only by `browsing` and `ai` inventories.
- AI excludes Hong Kong before service qualification.
- OpenAI, Claude, and Gemini remain independently qualified.
- `ProxyGFWlist -> 网页浏览` remains canonical.
- final `MATCH -> 漏网之鱼` remains canonical.
- no process-name rule is used as a source-permission or security boundary.
- production publication remains fail-closed after source reachability audit and dual-Mihomo validation.
