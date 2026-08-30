# Publishing and promotion

Publication is intentionally downstream of generation. A publisher receives already validated bytes and cannot influence node selection or policy.

## Candidate lifecycle

1. `candidate` resolves Secrets and generates `.work/candidate/config.yaml` once.
2. Static output validation completes before the candidate file is written.
3. The candidate and redacted report are uploaded for one day.
4. Every stable matrix job downloads the same named Artifact.
5. Each job runs static validation, Mihomo config load, startup smoke, and provider `HEAD` behavior tests.
6. `promote` has normal successful `needs` dependencies. There is no `always()` or tolerated stable failure.

## Production Artifact

After rechecking static structure and `publishing.artifact`, `promote` uploads a versioned 90-day Artifact. Existing production Artifacts are immutable; a failed run does not modify them.

## GitHub Release

Release publishing is optional and disabled by default. The workflow:

1. checks both the tracked declaration and `PUBLISH_PUBLIC_RELEASE=true`;
2. executes the declaration + acknowledgement gate;
3. creates a new draft Release with a unique run/commit tag;
4. uploads exact `config.yaml` and `build-report.json` while the Release remains a draft;
5. changes the fully uploaded draft to public/latest in the final command.

Any earlier failure leaves the previous latest Release unchanged. A failed final edit may leave a harmless draft for manual cleanup.

## Gist

Gist is an optional backend after the same stable validation and promotion dependencies. It requires:

- `publishing.gist.enabled: true`;
- repository variable `PUBLISH_UNLISTED_GIST=true`;
- repository variable `CLASH_RELAY_PUBLICATION_ACKNOWLEDGEMENT=I_UNDERSTAND_THIS_PUBLISHES_PROXY_CREDENTIALS`;
- Secrets `GITHUB_GIST_TOKEN` and `GITHUB_GIST_ID`.

The publisher uses the GitHub API and returns only the Gist ID. It never logs the token or URL content.

## Adding another backend

Implement the small publisher protocol under `src/clash_relay/publishers/`, add a publication gate mode/consent, and call it only in `promote` after stable validation. A backend must not regenerate or mutate candidate bytes.
