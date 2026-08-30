# Security model

## Secrets that must never be committed

- subscription URLs and tokens;
- node usernames, passwords, UUID-bearing private links, or generated proxy payloads;
- API keys and Gist tokens;
- private controller addresses/secrets;
- ignored local secret mappings;
- generated `config.yaml` and build caches.

The initial `.gitignore` covers common secret filenames, `.env*`, `.secrets/`, generated `dist/` content, downloaded cores, caches, and runtime provider directories. Public `config.yaml` and `subscriptions.yaml` are intentionally commit-able, so repository audit and code review must ensure they contain metadata only.

## Public source repository vs. private production repository

The public repository is source code and examples only. A production build that has both `config.yaml` and `subscriptions.yaml` is allowed only when GitHub reports the repository as private.

The publish workflow checks repository privacy in the `prepare` job before any step receives `CLASH_RELAY_SUBSCRIPTIONS`. If production declarations are present in a public or otherwise non-private repository, the workflow fails closed before subscription Secrets are read, candidate YAML is generated, or an Artifact can be uploaded.

Recommended deployment model:

1. keep the upstream/template repository public;
2. create a private repository for real production configuration;
3. put only subscription metadata in tracked YAML;
4. keep subscription URLs in GitHub Actions Secrets;
5. generate and retain credential-bearing Artifacts only in that private repository.

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

Current limitation: URL hostnames are not DNS-pinned throughout the HTTP connection, so a hostile resolver capable of rebinding remains outside the current standard-library fetcher threat model. Do not accept subscription URLs from untrusted strangers, and run production builds on hardened runners when this matters.

## Logs and reports

Build reports contain source IDs, counts, statuses, and a candidate digest, never source URLs or proxy payloads. Error paths redact full secret values, common query credentials, Authorization headers, and password fields. Workflows do not enable shell tracing and never echo the secret bundle.

`CLASH_RELAY_SUBSCRIPTIONS` is a structured Secret containing multiple URL values. Before generation begins, the workflow parses that mapping in-memory and emits a GitHub `::add-mask::` command for every individual URL. This makes each derived URL independently maskable even though GitHub originally received the bundle as one Secret value.

Mihomo output is captured only for failure diagnosis and redacted against known secret URLs. Node credentials can still appear in unusual core errors; runner-side masking and private workflow access remain defense-in-depth, not a reason to publish logs.

## Generated output is highest-sensitivity data

The project deliberately emits inline providers so clients need no private subscription URL. This means the resulting file contains node credentials. Treat generated `config.yaml` as equivalent to a credential bundle.

Security guarantees are therefore:

- no credential is committed to the source repository;
- no subscription URL is written into candidate YAML or reports;
- no failed candidate is promoted;
- a production workflow in a non-private repository fails before Secret use;
- public Release and Gist backends are disabled by default and remain behind explicit opt-in gates.

They do **not** make a published standalone config non-sensitive.

### Artifact

Artifact is the baseline transport only for a private production repository. Candidate Artifacts are retained for one day and promoted production Artifacts follow the configured workflow retention. Treat repository read access as config access.

A public repository with production declarations is intentionally blocked before candidate generation, so it cannot upload a credential-bearing candidate or production Artifact through the supported workflow.

### GitHub Release

Release publication is disabled by default. It requires configuration consent, repository variable `PUBLISH_PUBLIC_RELEASE=true`, and the exact publication acknowledgement variable. Generated config remains sensitive even when the repository itself is private; changing repository visibility later can expose retained history and release assets, so do not use Release as the default delivery path.

### Gist

Gist publication is disabled by default. An unlisted Gist is not private. It requires a separate token, existing Gist ID, declaration consent, repository variable `PUBLISH_UNLISTED_GIST=true`, and the exact publication acknowledgement variable. Do not use it as the default delivery path for production credentials.

## GitHub hardening before production

- use a private repository for production generation and sensitive Artifacts;
- protect `main` and require all PR status checks;
- restrict who can modify workflows;
- use environments/required reviewers for the `promote` job if available;
- minimize Actions token permissions;
- review Dependabot updates before merge;
- consider pinning third-party Actions to full commit SHAs under your supply-chain policy;
- enable private vulnerability reporting;
- rotate any credential that ever appeared in repository history, logs, Artifacts, Releases, or Gists;
- test from a new fictional secret before adding production subscriptions.
