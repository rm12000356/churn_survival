"""Raw-file listing/reading + upload endpoints (Horizon frontend, additive).

These endpoints exist so a browser UI can:

- list the files under ``RAW_DATA_DIR`` and know what each one *is* (a Node 1
  customer dataset vs a Node 3 support-threads JSON), via ``GET /raw-files``;
- read a support-threads JSON's content so the UI can pass it as
  ``support_data`` on ``POST /runs``, via ``GET /raw-files/{name}`` (support
  files only — customer datasets are never served back);
- place a file there for ``POST /runs`` to reference, via ``POST /uploads``.

They are **read-only or write-gated** and never compute anything: no parsing,
no routing, no node execution. ``POST /runs`` remains the single computing
trigger.

Classification (mirrors what the pipeline can actually ingest):

- ``"dataset"``  -> Node 1 customer data; CSV/Excel (``node1.SUPPORTED_EXTENSIONS``)
- ``"support"``  -> Node 3 support-threads JSON (``RunTriggerRequest.support_data``)

Anything else is not listed, so the UI can never offer an unrunnable file as a
customer dataset. Upload is confined to ``RAW_DATA_DIR``: the filename is
reduced to its basename, path separators / traversal are rejected, and only the
above extensions are accepted. Files are written atomically and never replaced
by different content under the same name (past runs keep their source).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse

from api.deps import get_app_settings, require_auth, require_writes
from api.schemas import RawFileInfo, RawFileListResponse, UploadResponse
from config.settings import Settings
from node1.node import SUPPORTED_EXTENSIONS

router = APIRouter(tags=["uploads"])

_SUPPORT_EXTENSIONS = {".json"}
_DATASET_EXTENSIONS = set(SUPPORTED_EXTENSIONS)
_ALLOWED_SUFFIXES = _DATASET_EXTENSIONS | _SUPPORT_EXTENSIONS
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MiB guard for a demo instance
#: Largest support-threads file served back to the UI.
MAX_SUPPORT_READ_BYTES = 50 * 1024 * 1024
_CHUNK = 1024 * 1024


def _file_kind(name: str) -> Literal["dataset", "support"] | None:
    """Classify a file by extension, or ``None`` if the pipeline cannot ingest it."""
    suffix = Path(name).suffix.lower()
    if suffix in _DATASET_EXTENSIONS:
        return "dataset"
    if suffix in _SUPPORT_EXTENSIONS:
        return "support"
    return None


def _safe_filename(name: str | None) -> str:
    """Reduce an uploaded filename to a confined, safe basename.

    Rejects empty names, path separators, traversal components and disallowed
    extensions. Never returns a path outside ``RAW_DATA_DIR``.
    """
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="uploaded file must have a filename",
        )
    candidate = name.replace("\\", "/").split("/")[-1].strip()
    if not candidate or candidate in {".", ".."} or candidate.startswith("."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid upload filename",
        )
    if candidate != Path(candidate).name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="upload filename must not contain a path",
        )
    if _file_kind(candidate) is None:
        allowed = ", ".join(sorted(_ALLOWED_SUFFIXES))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported file extension; allowed: {allowed}",
        )
    return candidate


def _raw_dir(settings: Settings) -> Path:
    base = Path(settings.RAW_DATA_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _resolve_listed_file(settings: Settings, name: str) -> Path:
    """Resolve a listed filename inside ``RAW_DATA_DIR`` or 400/404.

    Only a bare basename is accepted: any path separator, ``..`` or drive prefix
    is rejected so the read endpoint can never escape ``RAW_DATA_DIR``.
    """
    if not name or name != Path(name).name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid file name")
    path = _raw_dir(settings) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"raw file not found: {name}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@router.get("/raw-files", response_model=RawFileListResponse)
def list_raw_files(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> RawFileListResponse:
    """List the ingestible raw files available to ``POST /runs`` (read-only)."""
    base = _raw_dir(settings)
    files: list[RawFileInfo] = []
    for path in sorted(base.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        kind = _file_kind(path.name)
        if kind is None:
            continue
        stat = path.stat()
        files.append(
            RawFileInfo(
                name=path.name,
                raw_path=str(path),
                kind=kind,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )
        )
    return RawFileListResponse(files=files, total=len(files))


@router.get("/raw-files/{name}", response_class=PlainTextResponse)
def read_raw_file(
    name: str,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> PlainTextResponse:
    """Return a **support-threads** JSON as text (read-only, confined to RAW_DATA_DIR).

    Used by the UI to pass it as ``support_data``. Customer datasets are never
    served back through the API (they are only ever referenced by ``raw_path``).
    """
    path = _resolve_listed_file(settings, name)
    if _file_kind(path.name) != "support":
        raise HTTPException(status_code=404, detail=f"raw file not found: {name}")
    if path.stat().st_size > MAX_SUPPORT_READ_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"support file exceeds {MAX_SUPPORT_READ_BYTES} bytes",
        )
    return PlainTextResponse(path.read_text(encoding="utf-8"))


@router.post("/uploads", response_model=UploadResponse)
def upload_raw_file(
    file: UploadFile,
    settings: Annotated[Settings, Depends(get_app_settings)],
    _writes: Annotated[None, Depends(require_writes)],
    _actor: Annotated[str, Depends(require_auth)],
) -> UploadResponse:
    """Store an uploaded raw file under ``RAW_DATA_DIR`` for a subsequent run.

    A sync handler (runs in the threadpool, so file I/O never blocks the event
    loop). The upload streams into a temp file next to the target and is moved
    into place atomically; a failure never touches an existing file.
    """
    filename = _safe_filename(file.filename)
    kind = _file_kind(filename)
    assert kind is not None  # _safe_filename guarantees this
    base = _raw_dir(settings)
    target = base / filename

    digest = hashlib.sha256()
    written = 0
    temp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=base, prefix=f".{filename}.", suffix=".part", delete=False
        ) as handle:
            temp = Path(handle.name)
            while chunk := file.file.read(_CHUNK):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"upload exceeds {MAX_UPLOAD_BYTES} bytes",
                    )
                digest.update(chunk)
                handle.write(chunk)
        if written == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty"
            )
        if target.exists():
            if _sha256(target) != digest.hexdigest():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"a different file named {filename} already exists; rename the "
                        "upload (existing runs keep referencing the original)"
                    ),
                )
            temp.unlink(missing_ok=True)  # identical re-upload: nothing to change
        else:
            os.replace(temp, target)
    except HTTPException:
        if temp is not None:
            temp.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - surface as a structured client error
        if temp is not None:
            temp.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"upload failed ({type(exc).__name__})",
        ) from exc
    finally:
        file.file.close()

    return UploadResponse(
        filename=filename, raw_path=str(target), kind=kind, size_bytes=written
    )
