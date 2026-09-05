# Service Qualification API

AI service qualification is an extension boundary instead of a vendor branch in the production pipeline.

The canonical flow is:

```text
qualified browsing graph
  -> AI application orchestrator
  -> ordered ServiceQualification registry
  -> provider-neutral aggregate result
  -> service-qualified graph
  -> declared service client-path hardening
  -> final qualified candidate
```

`src/clash_relay/service_qualification.py` owns the built-in registry. The main qualification pipeline imports only generic registry/application surfaces and contains no OpenAI, Claude, or Gemini branch.

## Service contract

A `ServiceQualification` implementation declares:

- `probe_name`: policy probe identity;
- `label`: stable diagnostic/service label;
- `target_group`: generated service routing target;
- cache key semantics and pass/failure TTL policy;
- primary/critical qualification probe expansion;
- optional supporting diagnostic probes;
- optional provider-specific aggregate diagnostics;
- optional route post-processing;
- optional client-path hardening capability.

The default registry contains `OpenAIQualification`, `ClaudeQualification`, and `GeminiQualification`.

OpenAI keeps its App contract, cache fingerprint, route lock, critical/supporting probes, and client-local runtime hardening inside the OpenAI implementation. Claude and Gemini use the generic service behavior unless they need an explicit provider extension.

## Provider-neutral aggregate result

`src/clash_relay/service_qualification_result.py` projects every registered service into the same aggregate result shape. The qualification pipeline exposes these results under its AI stage without inspecting provider names.

Each result contains only:

- service label and probe identity;
- qualified/rejected/tested candidate counts;
- live tested/qualified counts;
- cache pass/failure hit counts;
- classifier-style aggregate outcome counts;
- aggregate qualification status.

It does not contain node identities, proxy payloads, server addresses, subscription URLs, credentials, raw response bodies, or endpoint response data. Outcome labels must be bounded classifier-style identifiers; unstructured labels fail closed.

A future registered service therefore gets the common result shape through registry iteration rather than a new provider branch in the qualification pipeline.

## Declarative client-path hardening

Client-path hardening is opt-in policy data on the service probe:

```yaml
probes:
  ai_openai:
    url: https://chatgpt.com/
    method: HEAD
    expected_status: "200-399"
    interval: 3600
    timeout: 5000
    lazy: false
    tolerance: 50
    client_path_hardening: true
```

The generic hardening stage reads Policy Model v2, resolves the service through the registry, and invokes the implementation only when the declaration is true. Declaring hardening for an unregistered service or an implementation that does not support it fails closed.

The production stage name is vendor-neutral: `service_client_path_hardened`.

## Adding a service

A new service qualification is implemented by adding one `ServiceQualification` implementation and registering it in the ordered registry. The main `qualification_pipeline.py` and `production_pipeline.py` must not require provider-specific edits.

Routing/classification data may still require its own policy/rule declaration because routing semantics are separate from qualification semantics. The extension boundary prevents service probe, cache, diagnostic, route-postprocess, and client-path behavior from leaking into the main orchestration path.

## Safety and validation

- Service admission remains fail closed.
- Browsing/transport retry remains a separate typed transient policy and cannot be widened by a service implementation.
- OpenAI App qualification semantics live behind `OpenAIQualification`.
- Client-path hardening is policy-declared rather than implicitly hardcoded in the pipeline.
- Provider-neutral result projection exposes aggregate counts only.
- The service qualification contract audit rejects provider names/imports in the main qualification pipeline and rejects restoration of provider-specific orchestration branches.
- Registry, aggregate result projection, AI application, and qualification pipeline are part of the static type gate.
- Full deterministic generation, Routing V2 drift, Promotion Guard, and the manifest-driven real Mihomo matrix remain release gates.
