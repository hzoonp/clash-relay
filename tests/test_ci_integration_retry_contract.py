from __future__ import annotations

from pathlib import Path


def test_acl4ssr_network_retry_is_scoped_and_fail_closed(repo_root: Path) -> None:
    ci = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "--ignore=tests/integration/test_acl4ssr_upstream.py" in ci
    assert "tests/integration/test_acl4ssr_upstream.py" in ci
    assert "for attempt in 1 2 3; do" in ci
    assert 'if [ "$attempt" -eq 3 ]; then' in ci
    assert "exit 1" in ci
    assert 'sleep "$attempt"' in ci
    assert "continue-on-error" not in ci
