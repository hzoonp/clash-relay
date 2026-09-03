#!/usr/bin/env python3
"""Emit GitHub Actions masks for configured subscription URLs."""

from __future__ import annotations

import os

from clash_relay.secrets import load_secret_mapping


def _command_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    for value in load_secret_mapping(env=os.environ).values():
        print(f"::add-mask::{_command_escape(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
