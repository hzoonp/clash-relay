# Service Qualification API

P52 makes AI service qualification an extension boundary instead of a vendor branch in the production pipeline.

The canonical flow is:

```text
qualified browsing graph
  -> AI application orchestrator
  -> ordered ServiceQualification registry
  -> service-qualified graph
  -> declared service client-path hardening
  -> final qualified candidate
```

`src/clash_relay/service_qualification.py` owns the built-in registry. The main qualification pipeline imports only the generic registry application and contains no OpenAI, Claude, or Gemini dependency.

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

OpenAI keeps its mature App contract, cache fingerprint, route lock, critical/supporting probes, and client-local runtime hardening inside the OpenAI implementation. Claude and Gemini use the default generic service behavior.

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

The generic hardening stage reads Policy Model v2, resolves the service through the registry, and invokes the implementation only when the declaration is true. Declaring hardening for an unregistered service or for an implementation that does not support it fails closed.

The production stage name is vendor-neutral: `service_client_path_hardened`.

## Adding a service

A new service qualification should be implemented by adding one `ServiceQualification` implementation and registering it in the ordered registry. The main `qualification_pipeline.py` and `production_pipeline.py` must not require provider-specific edits.

Routing/classification data may still require its own policy/rule declaration because routing semantics are separate from qualification semantics. The extension boundary specifically prevents service probe, cache, diagnostic, route-postprocess, and client-path behavior from leaking into the main orchestration path.

## Safety and validation

- Service admission remains fail closed.
- The browsing/transport retry policy is unchanged.
- Existing OpenAI App qualification semantics are preserved behind the implementation.
- Client-path hardening is policy-declared rather than implicitly hardcoded in the pipeline.
- The service qualification contract audit rejects provider names/imports in the main qualification pipeline and rejects restoration of the old `if name == "ai_openai"` orchestration branch.
- Registry, AI application, and qualification pipeline are part of the static type gate.
- Full deterministic generation, Routing V2 drift, and pinned real Mihomo matrix remain release gates.
