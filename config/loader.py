"""Versioned config loader (ROADMAP Task 1.3, architecture §1.5/§4.2/§5.3/§5.18).

Loads versioned JSON/YAML files from `CONFIG_DIR`, validates them against the
frozen config models, and returns objects that carry their `*_version` fields.
Configs are immutable at runtime; an unknown version fails loudly (file missing
or schema mismatch) — never silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import BaseModel

from config.models import (
    ActionRulesConfig,
    MappingConfig,
    Node1Config,
    Node2Config,
    Node4Config,
    Node5Config,
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


def load_node1_config(version: str) -> Node1Config:
    """Load `config/node1/v{version}.json` (architecture §1.7)."""
    return load_config(config_dir() / "node1" / f"v{version}.json", Node1Config)


def load_node2_config(version: str) -> Node2Config:
    """Load `config/node2/v{version}.json` (architecture §2.4/§2.7)."""
    return load_config(config_dir() / "node2" / f"v{version}.json", Node2Config)


def load_node4_config(version: str) -> Node4Config:
    """Load `config/node4/v{version}.json` (architecture §4.2)."""
    return load_config(config_dir() / "node4" / f"v{version}.json", Node4Config)


def load_node5_config(version: str) -> Node5Config:
    """Load `config/node5/v{version}.json` (architecture §5.3)."""
    return load_config(config_dir() / "node5" / f"v{version}.json", Node5Config)


def load_action_rules(version: str) -> ActionRulesConfig:
    """Load `config/action_rules/v{version}.json` (architecture §5.18)."""
    return load_config(config_dir() / "action_rules" / f"v{version}.json", ActionRulesConfig)


def load_mapping_config(mapping_version: str) -> MappingConfig:
    """Load a confirmed mapping from `config/mappings/` (architecture §1.5)."""
    return load_config(config_dir() / "mappings" / f"{mapping_version}.json", MappingConfig)
