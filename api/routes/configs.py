"""Deployment config listing (Horizon frontend, additive).

Read-only and decision-free: it lists the available Node 1 deployment configs
(``config/node1/v<version>.json``) so a browser UI can offer an explicit override
of the automatic deployment-config resolution. It never executes a node,

``POST /runs`` remains the single computing trigger.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends

from api.deps import get_app_settings
from api.schemas import Node1ConfigInfo, Node1ConfigListResponse
from config.loader import load_node1_config
from config.settings import Settings

router = APIRouter(tags=["configs"])


@router.get("/node1-configs", response_model=Node1ConfigListResponse)
def list_node1_configs(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Node1ConfigListResponse:
    """List the Node 1 deployment configs available to ``POST /runs`` (read-only)."""
    node1_dir = Path(settings.CONFIG_DIR) / "node1"
    configs: list[Node1ConfigInfo] = []
    if node1_dir.is_dir():
        for path in sorted(node1_dir.glob("v*.json")):
            version = path.stem[1:]  # strip the leading "v"
            config = load_node1_config(version)
            configs.append(
                Node1ConfigInfo(
                    version=version,
                    approved_core_keys=list(config.approved_core_keys),
                    allow_missing_core_passthrough=config.allow_missing_core_passthrough,
                )
            )
    return Node1ConfigListResponse(configs=configs, total=len(configs))
