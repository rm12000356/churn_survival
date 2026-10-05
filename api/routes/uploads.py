from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import PlainTextResponse

from api.deps import client_key, get_app_settings, require_auth, require_writes
from api.schemas import RawFileInfo, RawFileListResponse, UploadResponse
from config.settings import Settings
from node1.node import SUPPORTED_EXTENSIONS

router = APIRouter(tags=["uploads"])

_SUPPORT_EXTENSIONS = {".json"}
_DATASET_EXTENSIONS = set(SUPPORTED_EXTENSIONS)
_ALLOWED_SUFFIXES = _DATASET_EXTENSIONS | _SUPPORT_EXTENSIONS
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_SUPPORT_READ_BYTES = 50 * 1024 * 1024
_CHUNK = 1024 * 1024


def _file_kind(name: str) -> Literal["dataset", "support"] | None:
    suffix = Path(name).suffix.lower()
    if suffix in _DATASET_EXTENSIONS:
        return "dataset"
    if suffix in _SUPPORT_EXTENSIONS:
        return "support"
    return None


def _safe_filename(name: str | None) -> str:
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


def _raw_dir(settings: Settings, *, create: bool = False) -> Path:
    base = Path(settings.RAW_DATA_DIR)
    if create:
        base.mkdir(parents=True, exist_ok=True)
    return base


def _resolve_listed_file(settings: Settings, name: str) -> Path:
    if not name or name != Path(name).name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid file name")
    base = _raw_dir(settings).resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base) or not path.is_file():
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
    base = _raw_dir(settings)
    files: list[RawFileInfo] = []
    if not base.is_dir():
        return RawFileListResponse(files=files, total=0)
    resolved_base = base.resolve()
    for path in sorted(base.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if not path.resolve().is_relative_to(resolved_base):
            continue
        kind = _file_kind(path.name)
        if kind is None:
            continue
        stat = path.stat()
        files.append(
            RawFileInfo(
                name=path.name,
                raw_path=path.name,
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
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    _writes: Annotated[None, Depends(require_writes)],
    _actor: Annotated[str, Depends(require_auth)],
) -> UploadResponse:
    request.app.state.upload_limit.acquire(client_key(request))
    filename = _safe_filename(file.filename)
    kind = _file_kind(filename)
    assert kind is not None
    base = _raw_dir(settings, create=True)
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
        if not _publish_new(temp, target) and _sha256(target) != digest.hexdigest():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"a different file named {filename} already exists; rename the "
                    "upload (existing runs keep referencing the original)"
                ),
            )
        temp.unlink(missing_ok=True)
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

    return UploadResponse(filename=filename, raw_path=filename, kind=kind, size_bytes=written)


_PUBLISH_LOCK = threading.Lock()


def _publish_new(temp: Path, target: Path) -> bool:
    try:
        os.link(temp, target)
        return True
    except FileExistsError:
        return False
    except OSError:
        with _PUBLISH_LOCK:
            if target.exists():
                return False
            os.replace(temp, target)
            return True
