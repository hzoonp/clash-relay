# Fork quickstart

This guide takes a fresh fork from private subscription URLs to a validated Mihomo / FlClash production profile without committing credential-bearing configuration to GitHub.

## 1. Fork the repository

Fork `hzoonp/clash-relay` into your GitHub account. Do not add subscription URLs to tracked YAML files, workflow inputs, issues, or commit messages.

The tracked `subscriptions.yaml` contains only secret names and source permissions. The generated production `config.yaml` is private runtime output and **must never be committed**.

## 2. Create the subscription secret

Open your fork:

`Settings -> Secrets and variables -> Actions -> Secrets`

Create this repository secret:

```text
CLASH_RELAY_SUBSCRIPTIONS
```

Use a JSON object as the value:

```json
{
  "SUBSCRIPTION_1_URL": "https://example.invalid/subscription-1",
  "SUBSCRIPTION_2_URL": "https://example.invalid/subscription-2",
  "SUBSCRIPTION_3_URL": "https://example.invalid/subscription-3",
  "SUBSCRIPTION_4_URL": "https://example.invalid/subscription-4"
}
```

Replace every `.invalid` example with your private URL only inside the GitHub Secret UI.

The canonical source policy is intentionally asymmetric:

```text
subscription_1 -> browsing + AI only
subscription_2 -> general + browsing + AI
subscription_3 -> general + browsing + AI
subscription_4 -> general + browsing + AI
```

For `subscription_1`, nodes with an explicit multiplier above `2x` are removed before classification and deduplication. Exactly `2x` and nodes without an explicit multiplier marker are retained.

## 3. Configure private Cloudflare Workers KV

Create a private Workers KV namespace in your Cloudflare account, then configure GitHub Actions.

Repository Secret:

```text
CLOUDFLARE_API_TOKEN
```

Repository Variables:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_KV_NAMESPACE_TITLE
```

The API token should have only the permissions needed to read/write the selected Workers KV namespace. Do not place the token, account ID, namespace ID, or subscription URLs in tracked files.

Production uses the key configured in `config.yaml`, currently `production-config`.

## 4. Run a dry-run first

Open:

`Actions -> Generate, validate, and publish -> Run workflow`

Leave:

```text
publish = false
```

This is the default. A manual run with `publish=false` performs the private build and validation chain but does **not** replace the production KV value.

A successful dry-run proves the candidate passed the production gates, including:

```text
subscription fetch / parsing
source permission admission
source-to-scenario reachability audit
browsing HTTPS qualification
media/messaging transport qualification
AI service qualification
post-qualification current-policy audit
all stable Mihomo cores pinned by tools/mihomo-versions.json
aggregate production proof
```

Only aggregate information is written to GitHub Actions Summary. Node names, servers, credentials, subscription URLs, stage files, and the generated candidate are never committed or uploaded as an Artifact/Release/Gist.

## 5. Publish production

After a successful dry-run, run the same workflow manually with:

```text
publish = true
```

A push to `main` that touches production inputs follows the same validated publication path automatically.

P17 publishes a versioned private release without changing the client-facing key. The exact final bytes are first stored and read-back verified under an immutable SHA-256 release ID. Only then can the fixed `production-config` value be activated and `current-release-v1` / `previous-release-v1` pointers committed. The legacy `previous-v1` recovery slot remains a migration fallback.

If pointer commit fails after the client-facing value changed, the release layer attempts to restore the previous exact production bytes. Cloudflare KV does not provide a multi-key transaction, so this is a compensating transaction rather than a claim of cross-key atomicity.

AI qualification cache and browsing scheduler history are derived state. They are persisted after the production release commits. If either optional state write fails, Actions shows a warning while the already validated and committed production release remains successful; a later run safely rebuilds missing state by probing again.

## 6. Understand browsing scheduling

The browser inventory has a live three-sample qualification boundary:

```text
3/3 successful probes -> stable -> automatic browsing candidate
2/3 successful probes -> reserve -> manual browsing candidate
0/3 or 1/3          -> rejected from browsing inventory
```

If fewer than three stable nodes remain, automatic browsing safely falls back to the full qualified browsing provider instead of thinning the pool too far.

The runtime `url-test` group still handles live latency selection and tolerance. High latency by itself does not delete a node.

### Anonymous history

Cross-run history can further demote an established unstable node from the automatic stable tier, but it never promotes a node that failed the current live qualification.

History is stored privately in Workers KV as HMAC-SHA256 fingerprints plus aggregate stability fields only. It does not store runtime node names, servers, credentials, or subscription URLs. Missing, invalid, or temporarily unavailable history degrades to current-run scheduling rather than widening eligibility.

## 7. Understand AI scheduling

OpenAI, Claude, and Gemini are qualified independently through temporary loopback Mihomo processes. A node can qualify for one service and fail another.

Each service receives its own qualified routing graph. If one service has no qualified nodes, that service fails closed instead of borrowing an unqualified route; other AI services can continue when they have valid candidates.

## 8. Verify the production proof

After a successful dry-run or publish, inspect the GitHub Actions Summary. The final production proof contains aggregate data such as:

```text
candidate byte length and SHA-256
source reachability status
browsing tested / qualified / stable / reserve / rejected
AI qualified counts per service
validated stable Mihomo core versions from tools/mihomo-versions.json
publication = dry-run or published
```

The SHA-256 identifies the exact validated candidate without exposing its contents.

## 9. Roll back a bad production release

Open:

`Actions -> Roll back production config -> Run workflow`

Set:

```text
confirm = true
```

Rollback is manual-only and main-only. It resolves the private versioned `previous-release-v1` pointer first, with legacy `previous-v1` fallback for older deployments. Before activation, the previous candidate must pass the **current repository** production/source-isolation and Routing V2 audits, then every stable core pinned in `tools/mihomo-versions.json`. It is activated through the same versioned release transaction.

This means an old config that is still valid Mihomo syntax but no longer satisfies today's source policy cannot be restored.

Do not use rollback as a substitute for fixing a failed build. A failed candidate never replaces the current production value in the first place.

## Troubleshooting

### The subscription step fails

Check that `CLASH_RELAY_SUBSCRIPTIONS` is valid JSON and contains every secret name required by the enabled entries in `subscriptions.yaml`. Keep URLs in the Secret UI only.

### Browsing qualification leaves no usable provider

The workflow fails closed and keeps the existing production KV value. Check subscription availability and whether the probe endpoint is reachable through candidate nodes. Do not bypass the 2/3 qualification boundary just to force publication.

### One AI service has zero qualified nodes

That service is intentionally fail-closed. OpenAI, Claude, and Gemini are independent; a zero for one service does not imply the other services are unusable.

### Mihomo rejects the candidate

The public workflow suppresses credential-bearing core output. Fix structural subscription input or project configuration rather than skipping the stable core matrix. Change supported core pins only through `tools/mihomo-versions.json` and its checksum manifest entries.

### Cloudflare publication fails

Confirm `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `CLOUDFLARE_KV_NAMESPACE_TITLE`, and confirm the token can read/write the intended Workers KV namespace. Failure before activation leaves the current production value unchanged; a later release-pointer commit failure invokes compensating restoration when a previous value exists.

### Scheduler history or AI cache cannot be persisted

These are derived optimization states. A post-publication state-write warning does not invalidate the committed production config. The next run rebuilds missing state with live qualification.

### Rollback says no previous release is available

A previous release exists only after a successful publication replaces a *different* existing production value. First publication and identical republish operations do not create a new previous release.

## Security checklist

Before publishing a fork, verify all of these remain true:

- Real subscription URLs exist only in GitHub Secrets.
- Private generated/qualified `config.yaml` is never committed or uploaded as an Artifact/Release/Gist.
- `subscription_1` remains limited to `browsing` and `ai` unless you intentionally redesign the source permission model.
- Final `MATCH` remains on the general graph.
- Manual dispatch defaults to `publish=false`.
- Rollback requires explicit `confirm=true`, current-policy audit, and the full pinned stable Mihomo matrix.
- `tools/mihomo-versions.json` is the only workflow source of truth for supported core versions.
- Cloudflare credentials have only the permissions needed for the private KV namespace.
