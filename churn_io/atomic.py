from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = ["atomic_write"]


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically via a same-directory temp file.

    A crash mid-write leaves the original file untouched and no temp file
    behind: the content is flushed to a temp file in the target directory and
    then swapped in with ``os.replace``.
    """
    temp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temp = Path(handle.name)
            handle.write(text)
        os.replace(temp, path)
    except BaseException:
        if temp is not None:
            temp.unlink(missing_ok=True)
        raise
