# Fork quickstart

This is the shortest supported path from a fresh fork to a private production configuration.

## 1. Fork without adding credentials

Keep `config.yaml`, `subscriptions.yaml`, `services.yaml`, `policies.yaml`, schemas, rules, source code, and workflows public. Real subscription URLs and generated production `config.yaml` bytes **must never be committed**. Private credentials and generated config are never committed or uploaded as an Artifact/Release/Gist.

## 2. Add subscription secrets

Create the repository secret `CLASH_RELAY_SUBSCRIPTIONS` with a JSON or YAML mapping whose keys match the enabled `secret_name` values in `subscriptions.yaml`.

Example shape:

```json
{
  "SUBSCRIPTION_1_URL": "https://example.invalid/subscription-1",
  "SUBSCRIPTION_2_URL": "https://example.invalid/subscription-2",
  "SUBSCRIPTION_3_URL": "https://example.invalid/subscription-3",
  "SUBSCRIPTION_4_URL": "https://example.invalid/subscription-4"
}
```

The example URLs above are placeholders only.

## 3. Configure private Cloudflare KV

Configure:

```text
Secret:   CLOUDFLARE_API_TOKEN
Variable: CLOUDFLARE_ACCOUNT_ID
Variable: CLOUDFLARE_KV_NAMESPACE_TITLE
```

The token needs the minimum Workers KV permissions required by the workflow. The namespace title must resolve to exactly one namespace.

## 4. Run doctor before production

Local public-only validation:

```bash
clash-relay doctor --public-only
```

Private readiness validation with local environment variables or a local ignored secret file:

```bash
clash-relay doctor --secret-file .secrets.yaml
```

Optionally perform the same bounded fetch policy against every enabled subscription without exposing URL or payload details:

```bash
clash-relay doctor --secret-file .secrets.yaml --check-subscriptions
```

To validate Cloudflare account/token/namespace/key read connectivity without publishing bytes:

```bash
clash-relay doctor --secret-file .secrets.yaml --check-cloudflare
```

Both connectivity checks can be requested together. Doctor output is aggregate-only. It never prints subscription URLs, subscription payloads, Cloudflare credentials, or production configuration bytes. Connectivity failures are reduced to safe public identifiers/status messages.

## 5. Dry-run the production workflow

Run `Generate, validate, and publish` manually with the workflow input `publish = false`.

A successful dry-run performs the same private generation, source audit, browsing/transport qualification, AI qualification, post-qualification audit, and every stable Mihomo validation from `tools/mihomo-versions.json`, but does not activate Cloudflare KV production bytes.

Browsing qualification keeps the established `3/3` Stable and `2/3` Reserve sampling semantics. Scheduler history remains private and anonymous through HMAC-SHA256 fingerprints. OpenAI, Claude, and Gemini are qualified independently and fail closed per service.

Inspect the GitHub Actions summary. It intentionally exposes only aggregate production proof.

## 6. Publish

Run the workflow with `publish = true`, or merge a validated change to `main` when the repository is intentionally configured for push publication.

Publication stages immutable release objects first, verifies exact read-back bytes, activates the fixed client-facing production key, then commits release pointers. Cloudflare KV does not provide cross-key transactions, so pointer-commit failures use compensating restoration of the previous exact production bytes.

## 7. Configure FlClash / Mihomo

Use the existing private endpoint that serves the fixed production KV key. The client-facing key does not change when the internal release SHA changes.

Top-level FlClash decisions remain:

```text
代理选择
网页浏览
人工智能
流媒体
消息通讯
下载流量
```

## 8. Roll back safely

Run the manual `Roll back production config` workflow with `confirm = true` only when rollback is intentional. It resolves `previous-release-v1`, falls back to the legacy slot only for migration compatibility, applies the current-policy source/routing audits, validates every currently pinned stable Mihomo core, and activates through the same versioned release transaction.

A historical config that violates current source isolation is intentionally not rollback-eligible.

## What stays private

Never publish any of the following to GitHub:

- subscription URLs;
- generated proxy credentials;
- private qualified candidates;
- node-level browsing or AI probe results;
- scheduler fingerprint keys;
- AI cache fingerprints.

Private metrics remain bounded and aggregate-only.
