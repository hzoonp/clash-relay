# Traffic sniffing

P12 adds an optional Mihomo traffic-sniffing runtime layer without changing DNS ownership, routing policy, source isolation, or node qualification.

## Canonical production contract

Canonical production keeps client-owned DNS and enables HTTP, TLS, and QUIC sniffing:

```yaml
runtime:
  dns:
    mode: client
  sniffer:
    enabled: true
    force_dns_mapping: false
    parse_pure_ip: true
    sniff:
      http:
        ports: [80, '8080-8880']
        override_destination: true
      tls:
        ports: [443, 8443]
      quic:
        ports: [443, 8443]
```

The generated Mihomo configuration therefore contains `sniffer`, but still omits the generated `dns` block and `profile.store-fake-ip`.

## Why this is separate from DNS

Sniffing recovers application domain identity from HTTP Host, TLS SNI, or QUIC metadata. That identity lets the existing ACL4SSR and scenario rules classify traffic more accurately when an application connects by IP or otherwise hides the original domain from the rule engine.

P12 deliberately does not enable Fake-IP. `force-dns-mapping` remains `false`, and the stable P11 client-owned DNS behavior remains the production baseline.

## Compatibility

`runtime.sniffer` is optional. Projects that omit it preserve the pre-P12 output. Managed DNS and traffic sniffing can also be enabled independently; neither feature implicitly enables the other.

Port declarations accept individual ports from `1` through `65535` and ordered inclusive ranges such as `8080-8880`. Invalid or reversed ranges fail project loading before generation.

## Non-goals

P12 does not change ACL4SSR rule order, BanAD, public proxy groups, browsing/AI qualification, regional Stable/Reserve scheduling, subscription permissions, multiplier filtering, EMBY filtering, publication policy, or the `subscription_1` browsing/AI-only boundary.
