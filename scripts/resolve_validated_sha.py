#!/usr/bin/env python3
"""Resolve the trusted full validation for the exact current main SHA."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from clash_relay.validation_authority import select_trusted_validation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--github-output", type=Path)
    return parser


def _workflow_runs(repository: str, sha: str, token: str) -> list[dict[str, Any]]:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    query = urllib.parse.urlencode(
        {
            "branch": "main",
            "event": "push",
            "status": "success",
            "head_sha": sha,
            "per_page": 20,
        }
    )
    url = f"{api}/repos/{repository}/actions/workflows/publish.yml/runs?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise ValueError("GitHub workflow-runs response is invalid")
    return [item for item in runs if isinstance(item, dict)]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = args.repository.strip()
    sha = args.sha.strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repository or "/" not in repository or len(sha) != 40 or not token:
        print("trusted validation lookup is missing required GitHub context", file=sys.stderr)
        return 2

    try:
        runs = _workflow_runs(repository, sha, token)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"trusted validation lookup failed: {type(exc).__name__}", file=sys.stderr)
        return 2

    trusted = select_trusted_validation(runs, sha=sha)
    if trusted is None:
        print("no successful full main validation exists for the exact SHA", file=sys.stderr)
        return 2

    result = {
        "status": "trusted",
        "validated_sha": trusted.head_sha,
        "validation_run_id": trusted.run_id,
    }
    if args.github_output is not None:
        args.github_output.write_text(
            f"validated_sha={trusted.head_sha}\nvalidation_run_id={trusted.run_id}\n",
            encoding="utf-8",
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
