"""Versioned config loader (ROADMAP Task 1.3, architecture §1.5/§4.2/§5.3/§5.18).

Loads versioned JSON/YAML files from `CONFIG_DIR`, validates them against the
frozen config models, and returns objects that carry their `*_version` fields.
Configs are immutable at runtime; an unknown version fails loudly (file missing
or schema mismatch) — never silently.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from config.models import (
    ActionRulesConfig,
    IdentityMappingConfig,
    MappingConfig,
    Node1Config,
    Node2Config,
    Node3Config,
    Node3SourcesConfig,
    Node4Config,
    Node5Config,
    VocabularyConfig,
)
from config.settings import get_settings


def load_config[T: BaseModel](path: str | Path, model: type[T]) -> T:
    """Load and validate a single versioned config file (JSON or YAML)."""
    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    return model.model_validate(raw)


def config_dir() -> Path:
    """Resolve the versioned config directory from settings."""
    return Path(get_settings().CONFIG_DIR)


# A version is a plain name ("1", "dataset7", "map_20260820T000000Z"): it becomes
# part of a file name, so path separators and traversal are never accepted.
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_version(version: str) -> str:
    """Return ``version`` if it is a safe config version name, else raise ``ValueError``."""
    if not isinstance(version, str) or not _VERSION.match(version) or ".." in version:
        raise ValueError(f"invalid config version {version!r}")
    return version


def _versioned_path(subdir: str, filename: str) -> Path:
    """``config_dir()/subdir/filename``, refusing anything that escapes ``subdir``."""
    base = (config_dir() / subdir).resolve()
    path = (base / filename).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"config path escapes {subdir}/: {filename!r}")
    return path


def load_node1_config(version: str) -> Node1Config:
    """Load `config/node1/v{version}.json` (architecture §1.7)."""
    path = _versioned_path("node1", f"v{validate_version(version)}.json")
    return load_config(path, Node1Config)


def load_node2_config(version: str) -> Node2Config:
    """Load `config/node2/v{version}.json` (architecture §2.4/§2.7)."""
    path = _versioned_path("node2", f"v{validate_version(version)}.json")
    return load_config(path, Node2Config)


def load_node3_config(version: str) -> Node3Config:
    """Load `config/node3/v{version}.json` (architecture §3.2/§3.13)."""
    path = _versioned_path("node3", f"v{validate_version(version)}.json")
    return load_config(path, Node3Config)


def load_vocabulary() -> VocabularyConfig:
    """Load the controlled flag vocabulary `config/vocabulary.json` (§3.4)."""
    return load_config(config_dir() / "vocabulary.json", VocabularyConfig)


def load_node3_sources_config(version: str) -> Node3SourcesConfig:
    """Load `config/node3/sources_v{version}.json` (multi-source addendum §3)."""
    path = _versioned_path("node3", f"sources_v{validate_version(version)}.json")
    return load_config(path, Node3SourcesConfig)


def load_identity_mapping(version: str) -> IdentityMappingConfig:
    """Load `config/identity_mapping/v{version}.json` (addendum §6)."""
    path = _versioned_path("identity_mapping", f"v{validate_version(version)}.json")
    return load_config(path, IdentityMappingConfig)


def load_node4_config(version: str) -> Node4Config:
    """Load `config/node4/v{version}.json` (architecture §4.2)."""
    path = _versioned_path("node4", f"v{validate_version(version)}.json")
    return load_config(path, Node4Config)


def load_node5_config(version: str) -> Node5Config:
    """Load `config/node5/v{version}.json` (architecture §5.3)."""
    path = _versioned_path("node5", f"v{validate_version(version)}.json")
    return load_config(path, Node5Config)


def load_action_rules(version: str) -> ActionRulesConfig:
    """Load `config/action_rules/v{version}.json` (architecture §5.18)."""
    path = _versioned_path("action_rules", f"v{validate_version(version)}.json")
    return load_config(path, ActionRulesConfig)


def load_mapping_config(mapping_version: str) -> MappingConfig:
    """Load a confirmed mapping from `config/mappings/` (architecture §1.5)."""
    path = _versioned_path("mappings", f"{validate_version(mapping_version)}.json")
    return load_config(path, MappingConfig)
