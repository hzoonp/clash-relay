"""Safety gates shared by all publication backends."""

from __future__ import annotations

from typing import Any

from .errors import PublicationError

ACKNOWLEDGEMENT = "I_UNDERSTAND_THIS_PUBLISHES_PROXY_CREDENTIALS"


def publication_gate(config: dict[str, Any], mode: str, acknowledgement: str = "") -> None:
    publishing = config["publishing"]
    if mode == "artifact":
        if publishing["artifact"] is not True:
            raise PublicationError("artifact publication is disabled")
        return
    if mode == "cloudflare_kv":
        settings = publishing["cloudflare_kv"]
        if not settings["enabled"]:
            raise PublicationError("Cloudflare KV publication is disabled")
        if publishing["artifact"]:
            raise PublicationError(
                "Cloudflare KV mode requires credential-bearing Actions Artifacts to stay disabled"
            )
        if publishing["github_release"]["enabled"]:
            raise PublicationError(
                "Cloudflare KV mode requires GitHub Release publication to stay disabled"
            )
        if publishing["gist"]["enabled"]:
            raise PublicationError("Cloudflare KV mode requires Gist publication to stay disabled")
        return
    if acknowledgement != ACKNOWLEDGEMENT:
        raise PublicationError(
            f"{mode} publication requires the exact acknowledgement {ACKNOWLEDGEMENT!r}"
        )
    if mode == "github_release":
        settings = publishing["github_release"]
        if not settings["enabled"]:
            raise PublicationError("GitHub Release publication is disabled")
        if not settings["allow_sensitive_public_release"]:
            raise PublicationError(
                "public Release publication is blocked until allow_sensitive_public_release is true"
            )
        return
    if mode == "gist":
        settings = publishing["gist"]
        if not settings["enabled"]:
            raise PublicationError("Gist publication is disabled")
        if not settings["allow_sensitive_unlisted_gist"]:
            raise PublicationError(
                "Gist publication is blocked until allow_sensitive_unlisted_gist is true"
            )
        return
    raise PublicationError(f"unknown publication mode: {mode}")
