"""Shared fixtures for API contract tests (ROADMAP Phase 8, Task 8.2)."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config.settings import Settings
from orchestration.graph import run_pipeline
from orchestration.persistence import RunStore

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures"

API_KEY = "secret-key"


class SyncExecutor:
    """Run submitted jobs inline so API tests are deterministic."""

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        fn(*args, **kwargs)

        class _Done:
            def result(self) -> None:
                return None

        return _Done()

    def shutdown(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _api_env(fresh_settings: None) -> Iterator[None]:
    yield


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    target = tmp_path / "raw"
    target.mkdir()
    shutil.copy(FIXTURES / "clean_customers.csv", target / "clean_customers.csv")
    shutil.copy(FIXTURES / "unmapped_export.csv", target / "unmapped_export.csv")
    return target


@pytest.fixture
def clean_csv(raw_dir: Path) -> Path:
    return raw_dir / "clean_customers.csv"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(REPO / "config", target)
    return target


@pytest.fixture
def api_settings(tmp_path: Path, raw_dir: Path, config_dir: Path) -> Settings:
    return Settings(
        REFERENCE_DATE=date(2026, 8, 15),
        RAW_DATA_DIR=raw_dir,
        RUN_DIR=tmp_path / "runs",
        MODEL_DIR=tmp_path / "models",
        CONFIG_DIR=config_dir,
        LLM_PROVIDER="none",
        API_ENABLE_WRITES=True,
        API_KEY=API_KEY,
        API_ALLOW_ARBITRARY_PATHS=False,
    )


@pytest.fixture
def store(api_settings: Settings) -> RunStore:
    return RunStore(api_settings.RUN_DIR)


@pytest.fixture
def app(api_settings: Settings, store: RunStore):
    return create_app(
        api_settings,
        store=store,
        executor=SyncExecutor(),
        run_startup_recovery=False,
    )


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_client() -> Callable[[Settings], TestClient]:
    """Build a TestClient for arbitrary settings (auth/writes matrix tests)."""

    def _make(settings: Settings) -> TestClient:
        store = RunStore(settings.RUN_DIR)
        application = create_app(
            settings,
            store=store,
            executor=SyncExecutor(),
            run_startup_recovery=False,
        )
        return TestClient(application)

    return _make


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


@pytest.fixture
def seed_run(store: RunStore, api_settings: Settings):
    """Return a helper that runs + persists a real run into the temp store."""

    def _seed(raw_path: Path, **kwargs: Any) -> Any:
        result = run_pipeline(
            raw_path,
            settings=api_settings,
            config_dir=api_settings.CONFIG_DIR,
            reference_date=date(2026, 8, 15),
            **kwargs,
        )
        assert result.state.run_id is not None
        store.save(result)
        return result

    return _seed
