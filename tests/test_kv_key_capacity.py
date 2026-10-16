from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from clash_relay.errors import ConfigurationError, PublicationError
from clash_relay.kv_keys import (
    AUXILIARY_KEY_SUFFIXES,
    KV_KEY_MAX_BYTES,
    derived_kv_key_names,
    oversized_derived_kv_keys,
)
from clash_relay.release_bundle import publish_release_bundle, release_keys

# The longest derived name is the release manifest: 85 bytes of suffix plus the
# 64-character release id, so only 427 bytes of the base key remain usable.
_MAX_FITTING_BASE_BYTES = KV_KEY_MAX_BYTES - 85
_PRODUCTION_KEY = "production-config"


@dataclass
class RecordingFactory:
    """Fails the test if any release I/O starts before the capacity check."""

    calls: list[str] = field(default_factory=list)

    def __call__(self, key: str):
        self.calls.append(key)
        raise AssertionError("no release I/O is allowed for an over-long derived key")


def test_default_production_key_fits_every_derived_name() -> None:
    names = derived_kv_key_names(_PRODUCTION_KEY)
    assert oversized_derived_kv_keys(_PRODUCTION_KEY) == {}
    assert set(names) == {
        "release-config",
        "release-manifest",
        "current-pointer",
        "previous-pointer",
        "journal",
        "transaction",
        *{f"auxiliary{suffix}" for suffix in AUXILIARY_KEY_SUFFIXES},
    }


def test_derived_names_match_the_release_key_constructor() -> None:
    keys = release_keys(_PRODUCTION_KEY)
    names = derived_kv_key_names(_PRODUCTION_KEY)
    release_id = "0" * 64

    assert names["release-config"] == keys.config(release_id)
    assert names["release-manifest"] == keys.manifest(release_id)
    assert names["current-pointer"] == keys.current_pointer
    assert names["previous-pointer"] == keys.previous_pointer
    assert names["journal"] == keys.journal
    assert names["transaction"] == keys.transaction


@pytest.mark.parametrize("suffix", AUXILIARY_KEY_SUFFIXES)
def test_every_auxiliary_state_suffix_is_covered(suffix: str) -> None:
    names = derived_kv_key_names(_PRODUCTION_KEY)
    assert names[f"auxiliary{suffix}"] == f"{_PRODUCTION_KEY}{suffix}"


def test_capacity_boundary_is_set_by_the_longest_derived_name() -> None:
    names = derived_kv_key_names(_PRODUCTION_KEY)
    overhead = max(len(name) for name in names.values()) - len(_PRODUCTION_KEY)

    assert overhead == 85
    assert oversized_derived_kv_keys("k" * (KV_KEY_MAX_BYTES - overhead)) == {}
    assert oversized_derived_kv_keys("k" * (KV_KEY_MAX_BYTES - overhead + 1)) == {
        "release-manifest": KV_KEY_MAX_BYTES + 1
    }


@pytest.mark.parametrize(
    ("key", "fits"),
    [
        ("k" * _MAX_FITTING_BASE_BYTES, True),
        ("k" * (_MAX_FITTING_BASE_BYTES + 1), False),
        ("\u751f" * 142, True),  # 426 UTF-8 bytes
        ("\u751f" * 143, False),  # 429 UTF-8 bytes
    ],
)
def test_base_key_capacity_is_measured_in_utf8_bytes(key: str, fits: bool) -> None:
    assert (oversized_derived_kv_keys(key) == {}) is fits


def test_schema_maximum_length_character_key_is_rejected_everywhere() -> None:
    oversized = oversized_derived_kv_keys("k" * 512)

    assert set(oversized) == set(derived_kv_key_names(_PRODUCTION_KEY))


def test_over_long_base_key_fails_before_any_release_io() -> None:
    factory = RecordingFactory()

    with pytest.raises(PublicationError, match=r"beyond the 512-byte limit: release-manifest"):
        publish_release_bundle(factory=factory, production_key="k" * 428, content=b"candidate")

    assert factory.calls == []


def test_configuration_semantics_reject_over_long_key(project_factory, yaml_editor) -> None:
    from clash_relay.config_loader import load_project

    _, paths = project_factory()
    yaml_editor(
        paths["config_path"],
        lambda data: data["publishing"]["cloudflare_kv"].update(key="k" * 428),
    )

    with pytest.raises(ConfigurationError, match="beyond the 512-byte limit"):
        load_project(**paths)


def test_configuration_semantics_reject_multibyte_over_long_key(
    project_factory, yaml_editor
) -> None:
    from clash_relay.config_loader import load_project

    _, paths = project_factory()
    yaml_editor(
        paths["config_path"],
        lambda data: data["publishing"]["cloudflare_kv"].update(key="\u751f" * 143),
    )

    with pytest.raises(ConfigurationError, match="beyond the 512-byte limit"):
        load_project(**paths)


def test_configuration_semantics_accept_last_fitting_key(project_factory, yaml_editor) -> None:
    from clash_relay.config_loader import load_project

    _, paths = project_factory()
    yaml_editor(
        paths["config_path"],
        lambda data: data["publishing"]["cloudflare_kv"].update(key="k" * _MAX_FITTING_BASE_BYTES),
    )

    project = load_project(**paths)
    assert project.config["publishing"]["cloudflare_kv"]["key"] == "k" * _MAX_FITTING_BASE_BYTES
