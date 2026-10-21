from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.loader import load_node2_config
from tests.node2.conftest import make_config


def test_breslow_tie_method_is_rejected() -> None:
    """lifelines CoxPHFitter only implements Efron ties; 'breslow' must not validate."""
    with pytest.raises(ValidationError):
        make_config(tie_method="breslow")


def test_default_tie_method_is_efron() -> None:
    config = load_node2_config("1")
    assert config.tie_method == "efron"
