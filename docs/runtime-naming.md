# Runtime node naming

Generated Mihomo/FlClash proxy names use a presentation-only source label:

```text
[scope] source-label/original-name #digest
```

For canonical numbered subscription ids, clash-relay shortens only the visible label:

- `subscription_1` -> `sub_1`
- `subscription_2` -> `sub_2`
- `subscription_N` -> `sub_N`

Arbitrary source ids that do not match `subscription_<number>` are left unchanged.

The canonical subscription id remains the policy identity used for source permissions, source isolation, ordering, reporting, and the runtime-name digest. Production audits map the short runtime label back to that canonical id before checking reachability.

Source-exclusion filters accept both the new short label and the previous long `subscription_N/` prefix. This keeps isolation checks compatible with legacy generated candidates while new configurations use only the shorter presentation form.

If two enabled source ids would produce the same visible label, generation fails closed rather than publishing an ambiguous configuration. For example, a fork must not define both `subscription_1` and a literal `sub_1` source at the same time.

Because browsing scheduler history and the AI qualification cache include runtime proxy identity, the first production run after a runtime-name presentation change may refresh private historical/cache state. Live qualification remains authoritative and publication stays fail-closed during that refresh.

FlClash users see the shortened labels after refreshing and reloading the generated clash-relay configuration.
