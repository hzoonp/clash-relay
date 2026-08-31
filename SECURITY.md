# Security policy

## Supported versions

Security fixes are provided for the latest release and the `main` branch until a longer support policy is published.

## Reporting

Do not open a public issue containing a subscription URL, generated configuration, proxy credential, token, controller secret, workflow log, Cloudflare identifier, or Artifact link. Use GitHub private vulnerability reporting after the repository owner enables it.

A useful report contains a sanitized minimal declaration, affected commit/version, expected security boundary, observed behavior, and reproduction steps using fictional credentials.

## Credential exposure

If private material was ever committed or published, deleting it is not sufficient. Revoke/rotate the credential, remove public Artifacts/Releases/Gists where possible, and rewrite repository history only as an additional containment step.

Generated production `config.yaml`, scheduler history, AI qualification cache, previous-good recovery bytes, and subscription responses are private operational material. They must not be committed, attached to Releases, uploaded as Actions Artifacts, pasted into issues, or copied to public Gists.

## Cloudflare token scope

Use a dedicated Cloudflare API token rather than a global API key. Grant only the account/Workers KV permissions needed to resolve the configured namespace and read/write the project keys. Do not reuse a token that can manage unrelated zones, DNS, billing, users, or other accounts. Rotate the token if a workflow log, local shell history, or repository secret handling incident could have exposed it.

## Subscription fetch boundary

Production subscription fetching is designed for public HTTPS endpoints:

- HTTPS is the default and canonical scheme; HTTP and `file:` are opt-in development features.
- URL userinfo is rejected.
- literal private, loopback, link-local, multicast, reserved, and unspecified addresses are rejected.
- public hostnames are DNS-resolved before use; any answer pointing at private or special-use address space fails closed.
- redirects are revalidated before following them and the final URL is checked again.
- response bytes are bounded before parsing; gzip payloads are bounded again after decompression.
- fetch errors redact path/query credentials rather than echoing the original secret URL.

These checks reduce SSRF and resource-exhaustion risk. Operators should still use public subscription providers they trust and avoid split-horizon/private DNS names for production subscriptions.

## Configuration and parser limits

The canonical production configuration keeps `allow_http_subscription_urls: false`, `allow_file_subscription_urls: false`, `reject_private_proxy_hosts: true`, and an 8 MiB per-subscription byte limit. YAML is parsed with safe loaders and schema validation; malformed proxy option structures are rejected or skipped according to the declared invalid-proxy policy rather than passed blindly into Mihomo.

Do not raise input limits merely to accommodate an unexpectedly huge or malformed subscription without first understanding why it grew.

## Workflow trust

GitHub Actions workflows use minimal job permissions: production generation is `contents: read`; the source-only release workflow is the narrow exception that needs `contents: write` to create tags/releases. Private production bytes are never a GitHub Release asset.

Third-party/action version changes, Mihomo version changes, dependency-lock changes, publication changes, and any code that handles Secrets should be treated as security-sensitive review areas. Pinned Mihomo binaries are verified against GitHub-provided SHA-256 release digests before execution; same-run binary reuse verifies the cached executable hash before copying it.

## Source-permission boundary

A degraded or partially failed run must never widen source permissions. `allowed_uses`, multiplier filtering, provider admission, and end-to-end route reachability audits are security boundaries. In particular, a browsing/AI-only source must never be borrowed to rescue `general`, media, games, messaging, downloads, Microsoft/cloud routes, or final `MATCH`.

## Recovery

Production publication is fail-closed. If generation, live qualification, route audit, either pinned Mihomo core, or the production KV write fails, the workflow does not intentionally replace production with an unvalidated candidate. Manual rollback requires explicit confirmation and revalidates the private previous-good bytes with both supported Mihomo cores before activation.
