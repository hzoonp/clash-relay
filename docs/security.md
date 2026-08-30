# Security model

## Secrets that must never be committed

- subscription URLs and tokens;
- node usernames, passwords, UUID-bearing private links, or generated proxy payloads;
- API keys and Gist tokens;
- private controller addresses/secrets;
- ignored local secret mappings;
- generated `config.yaml` and build caches.

The initial `.gitignore` covers common secret filenames, `.env*`, `.secrets/`, generated `dist/` content, downloaded cores, caches, and runtime provider directories. Public `config.yaml` and `subscriptions.yaml` are intentionally commit-able, so repository audit and code review must ensure they contain metadata only.

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

Mihomo output is captured only for failure diagnosis and redacted against known secret URLs. Node credentials can still appear in unusual core errors; GitHub Actions secret masking and restricted workflow access remain defense-in-depth, not a reason to publish logs.

## Generated output is sensitive

The project deliberately emits inline providers so clients need no private subscription URL. This means the resulting file contains node credentials. Security guarantees are therefore:

- no credential is committed to this source repository;
- no subscription URL is written into candidate YAML or reports;
- no failed candidate is promoted;
- public backends require explicit opt-in.

They do **not** make a published standalone config non-sensitive.

### Artifact

Artifact is the baseline transport required by the project. Treat repository read access as config access. Use a private repository or an external private publisher for real credentials.

### GitHub Release

A Release asset is publicly downloadable in a public repository. Enabling it requires both configuration consent and repository variable `PUBLISH_PUBLIC_RELEASE=true` plus the exact publication acknowledgement variable. The workflow creates a draft, uploads both files, and only then makes it latest/public.

### Gist

An unlisted Gist is not private. It requires a separate token, existing Gist ID, declaration consent, and repository variable `PUBLISH_UNLISTED_GIST=true` plus the exact publication acknowledgement variable.

## GitHub hardening before production

- protect `main` and require all PR status checks;
- restrict who can modify workflows;
- use environments/required reviewers for the `promote` job if available;
- minimize Actions token permissions;
- review Dependabot updates before merge;
- consider pinning third-party Actions to full commit SHAs under your supply-chain policy;
- enable private vulnerability reporting;
- rotate any credential that ever appeared in repository history, logs, Artifacts, Releases, or Gists;
- test from a new fictional secret before adding production subscriptions.
