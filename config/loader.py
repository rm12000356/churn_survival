from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from config.models import (
    ActionRulesConfig,
    FeatureScreeningConfig,
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
    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    return model.model_validate(raw)


def config_dir() -> Path:
    return Path(get_settings().CONFIG_DIR)


_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_version(version: str) -> str:
    if not isinstance(version, str) or not _VERSION.match(version) or ".." in version:
        raise ValueError(f"invalid config version {version!r}")
    return version


def _versioned_path(subdir: str, filename: str) -> Path:
    base = (config_dir() / subdir).resolve()
    path = (base / filename).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"config path escapes {subdir}/: {filename!r}")
    return path


def load_node1_config(version: str, *, config_root: str | Path | None = None) -> Node1Config:
    filename = f"v{validate_version(version)}.json"
    if config_root is not None:
        base = (Path(config_root) / "node1").resolve()
        path = (base / filename).resolve()
        if not path.is_relative_to(base):
            raise ValueError(f"config path escapes node1/: {filename!r}")
        if path.is_file():
            return load_config(path, Node1Config)
    return load_config(_versioned_path("node1", filename), Node1Config)


def load_feature_screening_config(
    version: str = "1", *, config_root: str | Path | None = None
) -> FeatureScreeningConfig:
    filename = f"v{validate_version(version)}.json"
    roots = [Path(config_root)] if config_root is not None else []
    roots += [config_dir(), Path(__file__).parent]
    for root in roots:
        path = root / "feature_screening" / filename
        if path.is_file():
            return load_config(path, FeatureScreeningConfig)
    raise FileNotFoundError(f"feature screening config {filename} not found")


def load_node2_config(version: str) -> Node2Config:
    path = _versioned_path("node2", f"v{validate_version(version)}.json")
    return load_config(path, Node2Config)


def load_node3_config(version: str) -> Node3Config:
    path = _versioned_path("node3", f"v{validate_version(version)}.json")
    return load_config(path, Node3Config)


def load_vocabulary() -> VocabularyConfig:
    return load_config(config_dir() / "vocabulary.json", VocabularyConfig)


def load_node3_sources_config(version: str) -> Node3SourcesConfig:
    path = _versioned_path("node3", f"sources_v{validate_version(version)}.json")
    return load_config(path, Node3SourcesConfig)


def load_identity_mapping(version: str) -> IdentityMappingConfig:
    path = _versioned_path("identity_mapping", f"v{validate_version(version)}.json")
    return load_config(path, IdentityMappingConfig)


def load_node4_config(version: str) -> Node4Config:
    path = _versioned_path("node4", f"v{validate_version(version)}.json")
    return load_config(path, Node4Config)


def load_node5_config(version: str) -> Node5Config:
    path = _versioned_path("node5", f"v{validate_version(version)}.json")
    return load_config(path, Node5Config)


def load_action_rules(version: str) -> ActionRulesConfig:
    path = _versioned_path("action_rules", f"v{validate_version(version)}.json")
    return load_config(path, ActionRulesConfig)


def load_mapping_config(mapping_version: str) -> MappingConfig:
    path = _versioned_path("mappings", f"{validate_version(mapping_version)}.json")
    return load_config(path, MappingConfig)
