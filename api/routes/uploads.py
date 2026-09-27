"""Raw-file listing/reading + upload endpoints (Horizon frontend, additive).

These endpoints exist so a browser UI can:

- list the files under ``RAW_DATA_DIR`` and know what each one *is* (a Node 1
  customer dataset vs a Node 3 support-threads JSON), via ``GET /raw-files``;
- read a support-threads JSON's content so the UI can pass it as
  ``support_data`` on ``POST /runs``, via ``GET /raw-files/{name}``;
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
above extensions are accepted.
"""

from __future__ import annotations

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
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MiB guard for a demo instance


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
    if not candidate or candidate in {".", ".."}:
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
    """Return one raw file's bytes as text (read-only, confined to RAW_DATA_DIR).

    Used by the UI to load a support-threads JSON and pass it as ``support_data``.
    Read-only: it never routes, parses, or executes a node.
    """
    path = _resolve_listed_file(settings, name)
    return PlainTextResponse(path.read_text(encoding="utf-8"))


@router.post("/uploads", response_model=UploadResponse)
async def upload_raw_file(
    file: UploadFile,
    settings: Annotated[Settings, Depends(get_app_settings)],
    _writes: Annotated[None, Depends(require_writes)],
    _actor: Annotated[str, Depends(require_auth)],
) -> UploadResponse:
    """Store an uploaded raw file under ``RAW_DATA_DIR`` for a subsequent run."""
    filename = _safe_filename(file.filename)
    kind = _file_kind(filename)
    assert kind is not None  # _safe_filename guarantees this
    target = _raw_dir(settings) / filename

    written = 0
    try:
        with target.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > _MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"upload exceeds {_MAX_UPLOAD_BYTES} bytes",
                    )
                handle.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - surface as a structured client error
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"upload failed: {exc}",
        ) from exc
    finally:
        await file.close()

    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty"
        )

    return UploadResponse(
        filename=filename, raw_path=str(target), kind=kind, size_bytes=written
    )
