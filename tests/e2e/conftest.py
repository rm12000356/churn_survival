"""Full-pipeline E2E fixtures (ROADMAP Phase 8, Task 8.3)."""

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
RAW_DIR = REPO / "data" / "raw"
DATASET7_CSV = RAW_DIR / "dataset7_customers_messy.csv"
DATASET7_THREADS = RAW_DIR / "dataset7_support_threads_messy.json"
FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures"

API_KEY = "e2e-key"
REFERENCE_DATE = date(2026, 8, 15)
DATASET7_VERSIONS = {
    "node1_version": "dataset7",
    "node3_version": "dataset7",
    "node5_version": "dataset7",
}


class SyncExecutor:
    """Run submitted jobs inline so the E2E is deterministic."""

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        fn(*args, **kwargs)

        class _Done:
            def result(self) -> None:
                return None

        return _Done()

    def shutdown(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _e2e_env(fresh_settings: None) -> Iterator[None]:
    yield


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A writable copy of ``config/`` so mapping confirmations never touch the repo."""
    target = tmp_path / "config"
    shutil.copytree(REPO / "config", target)
    return target


@pytest.fixture
def e2e_settings(tmp_path: Path, config_dir: Path) -> Settings:
    return Settings(
        REFERENCE_DATE=REFERENCE_DATE,
        RAW_DATA_DIR=tmp_path / "raw",
        RUN_DIR=tmp_path / "runs",
        MODEL_DIR=tmp_path / "models",
        CONFIG_DIR=config_dir,
        LLM_PROVIDER="none",
        API_ENABLE_WRITES=True,
        API_KEY=API_KEY,
    )


@pytest.fixture
def store(e2e_settings: Settings) -> RunStore:
    return RunStore(e2e_settings.RUN_DIR)


@pytest.fixture
def seed_dataset7(store: RunStore, e2e_settings: Settings, dataset7_corpus: None):
    """Run dataset 7 end-to-end and persist it; returns the result."""
    import json

    threads = json.loads(DATASET7_THREADS.read_text(encoding="utf-8"))

    def _seed() -> Any:
        result = run_pipeline(
            DATASET7_CSV,
            support_data=threads,
            action_rules=None,
            reference_date=REFERENCE_DATE,
            settings=e2e_settings,
            config_dir=e2e_settings.CONFIG_DIR,
            **DATASET7_VERSIONS,
        )
        assert result.state.run_id is not None
        store.save(result)
        return result

    return _seed


@pytest.fixture
def client(e2e_settings: Settings, store: RunStore) -> Iterator[TestClient]:
    app = create_app(
        e2e_settings,
        store=store,
        executor=SyncExecutor(),
        run_startup_recovery=False,
    )
    # Authenticated: with API_KEY set every API route (reads included) needs it.
    with TestClient(app, headers={"X-API-Key": API_KEY}) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


@pytest.fixture
def clean_csv() -> Path:
    return FIXTURES / "clean_customers.csv"
