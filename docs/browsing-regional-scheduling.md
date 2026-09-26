# Browsing Regional Scheduling

Generic web browsing compares regional latency on the client access network. Each region retains Stable/Reserve recovery. AI scheduling is unchanged.

## Policy

The canonical order is declared independently from AI routing:

```yaml
routing:
  browsing:
    preferred_regions: [US, SG, JP, TW, KR, HK, OTHER]
```

The canonical browsing pool declares the same regions and fallback order. Country classification continues to use `country_classification` aliases plus exact per-node metadata overrides.

## Automatic mode

`网页自动` is a hidden cross-region `url-test`. Each candidate is a regional fallback that prefers its Stable tier and recovers through its Reserve tier. The client measures these regional candidates on its current network.

`routing.browsing.preferred_regions` preserves deterministic display and initial candidate order; it no longer forces a healthy US region ahead of a faster region. The browsing probe's `tolerance` (150 ms in the canonical policy) suppresses switches for small latency differences. Mihomo may change the browsing exit country when another region is sufficiently faster.

The canonical `scheduler.browsing.region_switch_interval` is 300 seconds. Node-level browsing checks remain at 180 seconds. These intervals and tolerance reduce churn but do not guarantee session continuity across an exit change.

## Manual regional mode

`网页浏览` exposes a fixed-region choice for every region that survives live qualification. Selecting `网页 · 日本`, for example, pins browsing to Japan:

```text
网页 · 日本
├── Japan Stable
└── Japan Reserve
```

The group never references another region. If Japan is unavailable, that manual choice is unavailable rather than silently changing the user's requested exit country.

## Qualification

Browsing qualification still uses three live HTTPS samples:

- 3/3 success: Stable;
- 2/3 success: Reserve;
- fewer than 2/3: rejected from browsing for the publication.

Qualification prunes failed nodes inside each regional provider. Regions with no remaining qualified nodes are omitted from `网页自动` and from the `网页浏览` choices. At least one region must survive for publication to continue.

## Historical preference

Historical success state cannot widen live admission. Within each region it may narrow the preferred Stable subset after the configured maturity/freshness thresholds are met. A currently qualified node that is historically demoted moves to the same region's Reserve tier. It is never moved to another region and a live Reserve/failed node is never promoted into Stable.

## Source isolation

Regional scheduling is downstream of source-use admission:

```text
subscription source
  -> allowed_uses
    -> browsing inventory
      -> region
        -> Stable / Reserve
          -> node
```

Therefore regional scheduling does not widen source permissions. The canonical first subscription remains `allowed_uses: [browsing, ai]`; it cannot enter media, download, general, or final routing. Explicit multiplier markers strictly greater than 2x are still rejected before classification.

## FlClash surface

The only top-level scenario groups remain `代理选择`, `网页浏览`, and `人工智能`. Regional browsing groups are hidden implementation groups but are valid choices inside `网页浏览`. Raw provider/runtime nodes are never direct members of the public selector.

## Validation

The CI contract covers:

- region/provider matching;
- provider-free public browsing surface;
- same-region Stable-to-Reserve recovery;
- cross-region selection when a healthy region is measurably slower;
- manual region pinning;
- source isolation and multiplier filtering;
- deterministic generation;
- real startup on Mihomo v1.19.29 and v1.19.30.
