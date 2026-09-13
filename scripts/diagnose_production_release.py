#!/usr/bin/env python3
"""Temporary safe stage tracing for a production dry-run.

This script intentionally reports only static stage/function names. It never
prints exception messages, node identities, subscription URLs, credentials, or
runtime endpoint details.
"""

from __future__ import annotations

import functools
from pathlib import Path
from types import ModuleType
from typing import Any

import clash_relay.ai_application as ai_application
import clash_relay.production_pipeline as production_pipeline
import clash_relay.production_release_stage as production_release_stage
import clash_relay.qualification_pipeline as qualification_pipeline
from clash_relay.production_lifecycle import ProductionLifecyclePaths, ProductionPipeline


def _trace(module: ModuleType, name: str) -> None:
    original = getattr(module, name)

    @functools.wraps(original)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        print(f"SAFE_STAGE={module.__name__}.{name}:start", flush=True)
        try:
            result = original(*args, **kwargs)
        except Exception:
            print(f"SAFE_STAGE={module.__name__}.{name}:failed", flush=True)
            raise
        print(f"SAFE_STAGE={module.__name__}.{name}:done", flush=True)
        return result

    setattr(module, name, wrapped)


def main() -> int:
    for module, names in (
        (
            production_pipeline,
            (
                "audit_production_candidate",
                "audit_routing_v2",
                "audit_route_lock",
                "audit_openai_client_path",
                "validate_acl4ssr_fidelity",
                "run_qualification_pipeline",
            ),
        ),
        (
            qualification_pipeline,
            (
                "run_browsing_qualification",
                "run_ai_qualification",
                "harden_declared_service_client_paths",
            ),
        ),
        (
            ai_application,
            (
                "load_registered_ai_probe_specs",
                "load_scheduler_policy",
                "load_policy_document",
                "load_routing_policy_v2",
                "_cache_inputs",
                "ai_runtime_fingerprints",
                "cached_service_decisions",
                "_probe_names",
                "update_ai_cache_service",
                "rewrite_ai_service_qualified_candidate",
                "apply_service_route_postprocessing",
            ),
        ),
        (
            production_release_stage,
            (
                "fetch_current_production_config",
                "run_promotion_guard",
                "validate_mihomo_matrix",
                "publish_production_release",
            ),
        ),
    ):
        for name in names:
            _trace(module, name)

    pipeline = ProductionPipeline(
        ProductionLifecyclePaths.canonical(Path(".")),
        publish=False,
        workers=12,
    )
    print("SAFE_STAGE=production_lifecycle:start", flush=True)
    try:
        pipeline.run()
    except Exception:
        print("SAFE_STAGE=production_lifecycle:failed", flush=True)
        return 2
    print("SAFE_STAGE=production_lifecycle:done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
