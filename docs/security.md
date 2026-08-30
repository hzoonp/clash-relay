# Security model

## Secrets that must never be committed

- subscription URLs and tokens;
- node usernames, passwords, UUID-bearing private links, or generated proxy payloads;
- Cloudflare API tokens;
- the Worker `PROFILE_TOKEN` or complete FlClash profile URL;
- private controller addresses/secrets;
- ignored local secret mappings;
- generated `config.yaml` and build caches.

The `.gitignore` covers common secret filenames, `.env*`, `.secrets/`, generated `dist/` content, downloaded cores, caches, and runtime provider directories. Tracked `config.yaml` and `subscriptions.yaml` are allowed only as public policy/metadata declarations and must contain no URL, token, username, password, or private endpoint.

## Public repository production model

Production generation is intentionally supported in a public GitHub repository, but the public repository is never used as storage or distribution for credential-bearing output.

The supported data path is:

1. tracked public declarations contain metadata only;
2. `CLASH_RELAY_SUBSCRIPTIONS` supplies private source URLs to the generation steps;
3. each URL is registered with GitHub `::add-mask::` before any fetch;
4. one GitHub-hosted runner generates the credential-bearing candidate locally;
5. the same runner validates the exact candidate with both pinned stable Mihomo cores;
6. only after both validations pass does a final step receive `CLOUDFLARE_API_TOKEN` and write the exact candidate bytes to private Cloudflare Workers KV;
7. no credential-bearing Actions Artifact, GitHub Release, Gist, commit, or Pages asset is created.

The workflow only performs production deployment from `refs/heads/main`. Pull-request CI continues to use fictional fixtures and receives no production secrets.

## Secret separation

Secrets are scoped to the narrowest workflow steps:

- `CLASH_RELAY_SUBSCRIPTIONS` is present only while registering masks and generating the candidate;
- `CLOUDFLARE_API_TOKEN` is present only in the final KV publication step;
- Mihomo validation receives neither secret;
- `PROFILE_TOKEN` never enters GitHub at all. It exists only as a Cloudflare Worker Secret and in the FlClash profile URL stored by the user.

A user with write access to a repository can potentially modify trusted workflow/code paths before a future main-branch run. Protect `main`, require CI review, and restrict workflow changes before treating repository Secrets as production credentials.

## Input handling

All subscriptions are untrusted:

- HTTPS is required by default;
- URL userinfo, unsupported schemes, and private/special IP literals are rejected;
- every redirect destination is revalidated;
- transfer and gzip-expanded sizes are bounded;
- UTF-8 is required;
- YAML anchors and aliases are rejected;
- nesting/item counts are bounded;
- only known proxy types and valid ports pass;
- remote provider URLs embedded in subscriptions are ignored;
- subscription-supplied chain/interface/routing controls are stripped;
- output names are regenerated and globally unique.

Current limitation: URL hostnames are not DNS-pinned throughout the HTTP connection, so a hostile resolver capable of rebinding remains outside the current standard-library fetcher threat model. Do not accept subscription URLs from untrusted strangers.

## Logs and reports

Build reports contain source IDs, counts, statuses, and a candidate digest, never source URLs or proxy payloads. Error paths redact full secret values, common query credentials, Authorization headers, and password fields. Workflows do not enable shell tracing and never echo the secret bundle.

`CLASH_RELAY_SUBSCRIPTIONS` is a structured Secret containing multiple URL values. Before generation begins, the workflow parses that mapping in memory and emits a GitHub `::add-mask::` command for every individual URL. This makes each derived URL independently maskable even though GitHub originally received the bundle as one Secret value.

Real Mihomo validation can theoretically include node details in unusual core failures. In the public production workflow, Mihomo stdout/stderr is therefore redirected to runner-local files and is not printed when a real candidate fails. The public log receives only a generic validation failure. Those local files are never uploaded.

Cloudflare API failures also return generic application errors. The API token and candidate body are never included in exception text.

## Generated output is highest-sensitivity data

The project deliberately emits inline providers so clients need no original subscription URL. This means the resulting file contains node credentials. Treat generated `config.yaml` as equivalent to a credential bundle.

The Cloudflare publication gate fails unless all credential-bearing GitHub publishers remain disabled:

- `publishing.artifact: false`;
- `publishing.github_release.enabled: false`;
- `publishing.gist.enabled: false`;
- `publishing.cloudflare_kv.enabled: true`.

The production workflow itself contains no `upload-artifact`, Release, or Gist path for the generated config.

## Cloudflare Workers KV

The GitHub Action uses a narrowly scoped Cloudflare API token with Workers KV edit/write permission. It resolves the configured namespace title exactly and writes the validated bytes to the configured key, normally `production-config`. The publisher enforces Cloudflare KV's 25 MiB value ceiling before attempting the write.

The Worker reads that key only after validating a high-entropy `PROFILE_TOKEN` carried in the URL path. The full profile URL is therefore a bearer credential: anyone who obtains it can download the generated configuration. Keep it out of GitHub, screenshots, analytics, referrers, support tickets, and logs. Rotate the token if it is exposed.

Recommended Worker response controls include `Cache-Control: no-store` and `X-Robots-Tag: noindex, nofollow, noarchive`, and invalid tokens should return a generic `404`.

## GitHub hardening before production

- protect `main` and require all PR status checks;
- restrict who can modify Actions workflows and production-relevant Python code;
- keep Actions token permissions read-only unless a workflow explicitly requires more;
- never use `pull_request_target` with production Secrets for untrusted code;
- review dependency updates before merge;
- consider pinning third-party Actions to full commit SHAs under your supply-chain policy;
- rotate any credential that ever appeared in repository history, logs, Artifacts, Releases, Gists, or screenshots;
- test from fictional subscription data before enabling real production declarations.
