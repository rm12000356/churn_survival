from __future__ import annotations

import json

from schemas.node5 import Node5Output


def render_json(output: Node5Output) -> str:
    return json.dumps(output.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
