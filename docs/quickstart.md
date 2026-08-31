# Fork quickstart

This guide takes a fresh fork from private subscription URLs to a validated Mihomo / FlClash production profile without committing any credential-bearing configuration to GitHub.

## 1. Fork the repository

Fork `hzoonp/clash-relay` into your GitHub account. Do not add subscription URLs to tracked YAML files, workflow inputs, issues, or commit messages.

The tracked `subscriptions.yaml` contains only secret names and source permissions. The generated `config.yaml` is private runtime output and must never be committed.

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

Create a private Workers KV namespace in your Cloudflare account. Then configure GitHub Actions in the fork.

Repository Secret:

```text
CLOUDFLARE_API_TOKEN
```

Repository Variables:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_KV_NAMESPACE_TITLE
```

The API token must have the minimum permissions needed to read and write the selected Workers KV namespace. Do not place the token, account ID, namespace ID, or subscription URLs in tracked files.

Production uses the key configured in `config.yaml`, currently `production-config`.

## 4. Run a dry-run first

Open:

`Actions -> Generate, validate, and publish -> Run workflow`

Leave:

```text
publish = false
```

This is the default. A manual run with `publish=false` performs the private build and validation chain but does **not** replace the production KV value.

A successful dry-run proves the candidate passed the relevant gates, including:

```text
subscription fetch / parsing
source permission admission
source-to-scenario reachability audit
browsing live qualification
AI service qualification
post-qualification reachability audit
Mihomo v1.19.30 validation
Mihomo v1.19.29 validation
aggregate production proof
```

Only aggregate information is written to GitHub Actions Summary. Node names, servers, credentials, subscription URLs, and the generated candidate are not uploaded as GitHub artifacts.

## 5. Publish production

After a successful dry-run, run the same workflow manually with:

```text
publish = true
```

A push to `main` that touches production inputs also follows the validated publication path automatically.

Before replacing a different production value, the workflow preserves the currently published validated bytes in a private recovery slot. The new candidate is written only after source audits, live qualification, and both pinned Mihomo validations succeed.

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

History is stored privately in Workers KV as HMAC-SHA256 fingerprints plus aggregate stability fields only. It does not store runtime node names, servers, credentials, or subscription URLs. Missing, invalid, or temporarily unavailable history degrades to current-run scheduling rather than blocking the production config.

## 7. Understand AI scheduling

OpenAI, Claude, and Gemini are qualified independently through the temporary Mihomo instance. A node can qualify for one service and fail another.

Each service receives its own qualified routing graph. If one service has no qualified nodes, that service fails closed instead of borrowing an unqualified route; other AI services can continue if they have valid candidates.

## 8. Verify the production proof

After a successful dry-run or publish, inspect the GitHub Actions Summary. The final production proof contains aggregate data such as:

```text
candidate byte length and SHA-256
source reachability status
browsing tested / qualified / stable / reserve / rejected
AI qualified counts per service
validated Mihomo core versions
publication = dry-run or published
```

Use the SHA-256 to identify the exact validated candidate without exposing its contents.

## 9. Roll back a bad production release

Normal publication preserves the previously active, different validated config before replacement.

To roll back:

`Actions -> Roll back production config -> Run workflow`

Set:

```text
confirm = true
```

Rollback is manual-only and main-only. The workflow fetches the private previous bytes, validates them again with Mihomo v1.19.30 and v1.19.29, and only then activates them as `production-config`.

Do not use rollback as a substitute for fixing a failed build. A failed candidate never replaces the current production value in the first place.

## Troubleshooting

### The subscription step fails

Check that `CLASH_RELAY_SUBSCRIPTIONS` is valid JSON and contains every secret name required by the enabled entries in `subscriptions.yaml`. Keep URLs in the Secret UI only.

### Browsing qualification leaves no usable provider

The workflow fails closed and keeps the existing production KV value. Check subscription availability and whether the probe endpoint is reachable through the candidate nodes. Do not bypass the 2-of-3 qualification boundary just to force publication.

### One AI service has zero qualified nodes

That service is intentionally fail-closed. OpenAI, Claude, and Gemini are independent; a zero for one service does not imply the other services are unusable.

### Mihomo rejects the candidate

The public workflow suppresses credential-bearing core output. Fix structural subscription input or project configuration rather than publishing a candidate that has not passed both pinned core versions.

### Cloudflare publication fails

Confirm `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `CLOUDFLARE_KV_NAMESPACE_TITLE`, and confirm the token can read/write the intended Workers KV namespace. A publication failure leaves the already published production value unchanged.

### Scheduler history cannot be loaded

History is auxiliary. The workflow falls back to current-run live browsing qualification. A transient read failure also prevents that run from overwriting unknown prior history.

### Rollback says no previous config is available

A previous slot exists only after a successful publication replaces a *different* existing production value. First publication and identical republish operations do not create or overwrite that recovery point.

## Security checklist

Before publishing a fork, verify all of these remain true:

- Real subscription URLs exist only in GitHub Secrets.
- `config.yaml` generated from private nodes is never committed or uploaded as an Artifact/Release/Gist.
- `subscription_1` remains limited to `browsing` and `ai` unless you intentionally redesign the source permission model.
- Final `MATCH` remains on the general graph.
- Manual dispatch defaults to `publish=false`.
- Rollback requires explicit `confirm=true` and dual-core revalidation.
- Cloudflare credentials have only the permissions needed for the private KV namespace.
