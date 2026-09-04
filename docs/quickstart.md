# Fork quickstart

This is the shortest supported path from a fresh fork to a private production configuration. A normal first-time setup should require only repository settings plus one dry run; no tracked source file needs real subscription credentials.

## 10-minute checklist

```text
1. Fork the repository
2. Add CLASH_RELAY_SUBSCRIPTIONS
3. Add Cloudflare token + account/namespace variables
4. Run `clash-relay doctor --public-only`
5. Run `clash-relay doctor` with private inputs
6. Manually run Generate, validate, and publish with publish=false
7. Inspect aggregate proof; only then publish=true
```

The first manual workflow defaults to `publish=false`. Treat a successful dry run as the prerequisite for an intentional first publication.

## 1. Fork without adding credentials

Keep `config.yaml`, `subscriptions.yaml`, `policies.yaml`, policy fragments, schemas, rules, source code, and workflows public. Real subscription URLs and generated production `config.yaml` bytes **must never be committed**. Private credentials and generated config are never committed or uploaded as an Artifact/Release/Gist.

Canonical production requires Policy Model v2. `policies.yaml` is only the manifest; routing, scheduling, classification, and topology have separate owned fragments. A legacy monolithic policy file is not a runtime input. Convert it offline with `scripts/migrate_policy_v2.py` before running clash-relay.

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

The example URLs above are placeholders only. `clash-relay doctor --public-only` reports the enabled Secret names, never their values.

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

The public report includes:

- enabled subscription count and Secret names;
- Policy Model v2 readiness;
- pinned stable Mihomo-core count;
- scheduler declaration status;
- concrete next steps for the first dry run.

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

A successful dry-run performs the same private generation, source audit, browsing/transport qualification, AI qualification, post-qualification audit, Promotion Guard semantics where applicable, and every stable Mihomo validation from `tools/mihomo-versions.json`, but does not activate Cloudflare KV production bytes.

Browsing/transport retry is deliberately narrow: only a structured whole-probe transient infrastructure failure may retry once, and the retry starts from the immutable generated candidate. Policy rejection, partial live success, transport admission failure, core rejection, configuration failure, and unstructured protocol failure remain fail closed without retry.

Inspect the GitHub Actions summary. It intentionally exposes only aggregate production proof.

## 6. Publish

Run the workflow with `publish = true`, or merge a validated change to `main` when the repository is intentionally configured for push publication.

Publication stages immutable release objects first, verifies exact read-back bytes, activates the fixed client-facing production key, then commits release pointers. Cloudflare KV does not provide cross-key transactions, so pointer-commit failures use compensating restoration of the previous exact production bytes.

Release progress is explicit:

```text
prepared -> qualified -> promoted -> published -> verified
```

A proof/manifest/metrics problem after the client-visible release transaction has committed is reported as post-release observability degradation; it does not falsely claim the release was never published. Before publication, every mandatory gate remains fail closed.

After the first successful publication, P26 automatically runs the same production workflow every six hours (`17 */6 * * *` UTC). Scheduled refresh is not a shortcut: subscription fetch, generation, source isolation, browsing/transport and AI qualification, OpenAI client-path hardening, post-audit, Promotion Guard, and every stable Mihomo validation must all pass before publication. If the final bytes have not changed, the release remains active with `status: unchanged` and `previous-release-v1` is not rotated.

Overlapping production Actions are serialized by the workflow concurrency group with `cancel-in-progress: false`; an older transaction is never cancelled mid-commit by a newer refresh.

## 7. Configure FlClash / Mihomo

Use the existing private endpoint that serves the fixed production KV key. The client-facing key does not change when the internal release SHA changes, and scheduled refresh does not require replacing the subscription URL in FlClash.

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

A historical config that violates current source isolation is intentionally not rollback-eligible. Automated tests also rehearse the exact previous-release round trip and pointer reversal without touching real production data.

## What stays private

Never publish any of the following to GitHub:

- subscription URLs;
- generated proxy credentials;
- private qualified candidates;
- node-level browsing or AI probe results;
- scheduler fingerprint keys;
- AI cache fingerprints.

Private longitudinal metrics remain bounded to 30 runs and aggregate-only. They may contain safe counts, hashes, stage durations, retry-recovery counts, Promotion Guard status, and release phase; they do not contain node identities, servers, credentials, or subscription URLs.

## Compatibility safety contract

The established browsing scheduler contract remains explicit: **3/3** successful live samples is Stable and **2/3** is Reserve. Private scheduler history continues to use **HMAC-SHA256** fingerprints and cannot promote a current live-failed node. OpenAI, Claude, and Gemini remain independently qualified. Manual recovery still uses **Roll back production config** with **confirm = true**, validates every stable core in **tools/mihomo-versions.json**, resolves **previous-release-v1**, and applies the **current-policy** audit before activation.
