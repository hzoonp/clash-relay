from __future__ import annotations

from pathlib import Path


def test_acl4ssr_network_retry_is_scoped_and_fail_closed(repo_root: Path) -> None:
    validate = (repo_root / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")

    assert "--ignore=tests/integration/test_acl4ssr_upstream.py" in validate
    assert "tests/integration/test_acl4ssr_upstream.py" in validate
    assert "for attempt in 1 2 3; do" in validate
    assert 'if [ "$attempt" -eq 3 ]; then' in validate
    assert "exit 1" in validate
    assert 'sleep "$attempt"' in validate
    assert "continue-on-error" not in validate
