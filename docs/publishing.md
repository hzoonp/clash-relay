# Publishing and promotion

Publication is intentionally downstream of generation. A publisher receives already validated bytes and cannot influence node selection or policy.

## Repository privacy gate

A real production run is supported only in a private repository. The public upstream remains suitable for source code, examples, CI, and fictional fixtures, but not for real subscription Secrets or credential-bearing output.

The `prepare` job checks whether both canonical production declarations (`config.yaml` and `subscriptions.yaml`) exist. If they do, GitHub must report `repository.private == true`. Otherwise the workflow fails before the `candidate` job is scheduled. That means a public repository cannot reach the step that receives `CLASH_RELAY_SUBSCRIPTIONS`, cannot fetch real subscriptions, and cannot upload a credential-bearing candidate or production Artifact through the supported workflow.

If the canonical production declarations do not exist, the workflow exits successfully without reading Secrets or generating output. This keeps the public template repository safe to maintain and test.

## Secret masking before generation

`CLASH_RELAY_SUBSCRIPTIONS` is intentionally one structured GitHub Secret so an arbitrary number of subscriptions can be declared without changing workflow YAML. Because individual URLs are values derived from that bundle, the candidate job parses the mapping in-memory and emits GitHub `::add-mask::` commands for every URL before any subscription fetch begins.

No URL is written to tracked YAML, generated candidate YAML, or build reports. Application-level redaction remains in place as a second layer.

## Candidate lifecycle

1. `prepare` verifies the canonical declarations and private-repository requirement before Secret use.
2. `candidate` installs the locked runtime dependencies.
3. every individual subscription URL is registered with `::add-mask::`.
4. `candidate` resolves Secrets and generates `.work/candidate/config.yaml` once.
5. static output validation completes before the candidate file is written.
6. the candidate and redacted report are uploaded for one day inside the private repository.
7. every stable matrix job downloads the same named Artifact.
8. each job runs static validation, Mihomo config load, startup smoke, and provider `HEAD` behavior tests.
9. `promote` has normal successful `needs` dependencies. There is no `always()` or tolerated stable failure.

## Production Artifact

After rechecking static structure and `publishing.artifact`, `promote` uploads a versioned 90-day Artifact. Existing production Artifacts are immutable; a failed run does not modify them.

The generated `config.yaml` contains inline node credentials and must be treated as highest-sensitivity data. Artifact transport is therefore supported only after the private-repository gate passes. Repository read access should be treated as access to the production configuration.

## GitHub Release

Release publishing is optional and disabled by default. It is never part of the default production path. The workflow:

1. checks the tracked declaration and `PUBLISH_PUBLIC_RELEASE=true`;
2. executes the declaration + acknowledgement gate;
3. creates a new draft Release with a unique run/commit tag;
4. uploads the exact `config.yaml` and `build-report.json` while the Release remains a draft;
5. changes the fully uploaded draft to latest in the final command.

Generated config remains sensitive regardless of repository visibility. Do not use Release merely as a convenient distribution path unless its access model is appropriate for the credentials it contains. Changing a repository from private to public later can expose retained release assets.

Any earlier failure leaves the previous latest Release unchanged. A failed final edit may leave a draft for manual cleanup.

## Gist

Gist is an optional backend after the same stable validation and promotion dependencies and is disabled by default. An unlisted Gist is not private. It requires:

- `publishing.gist.enabled: true`;
- repository variable `PUBLISH_UNLISTED_GIST=true`;
- repository variable `CLASH_RELAY_PUBLICATION_ACKNOWLEDGEMENT=I_UNDERSTAND_THIS_PUBLISHES_PROXY_CREDENTIALS`;
- Secrets `GITHUB_GIST_TOKEN` and `GITHUB_GIST_ID`.

The publisher uses the GitHub API and returns only the Gist ID. It never logs the token or URL content. Because the resulting config contains node credentials, Gist should not be used as the default production delivery mechanism.

## Adding another backend

Implement the small publisher protocol under `src/clash_relay/publishers/`, add a publication gate mode/consent, and call it only in `promote` after stable validation. A backend must not regenerate or mutate candidate bytes, and its access-control model must be suitable for credential-bearing output.
