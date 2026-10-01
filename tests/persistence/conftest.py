"""Shared fixtures for persistence tests (ROADMAP Phase 8)."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures"


@pytest.fixture(autouse=True)
def _persistence_settings(fresh_settings: None):
    """Hermetic settings (declared reference date, no LLM) for every test."""
    yield


@pytest.fixture
def clean_csv() -> Path:
    return FIXTURES / "clean_customers.csv"


@pytest.fixture
def unmapped_csv() -> Path:
    return FIXTURES / "unmapped_export.csv"
