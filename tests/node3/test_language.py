from __future__ import annotations

from datetime import date

from node3.preprocess import detect_language
from tests.node3.conftest import REFERENCE_DATE


def test_detects_english() -> None:
    assert detect_language(["I want to cancel my subscription, please help me"]) == "en"


def test_detects_spanish() -> None:
    assert detect_language(["Quiero dar de baja mi suscripción, gracias"]) == "es"


def test_detects_german() -> None:
    assert detect_language(["Ich möchte meinen Vertrag kündigen, danke"]) == "de"


def test_detects_french() -> None:
    assert detect_language(["Je veux annuler mon abonnement, merci"]) == "fr"


def test_unknown_on_empty() -> None:
    assert detect_language([]) is None
    assert detect_language(["12345"]) is None


def test_short_ambiguous_text_is_documented_behavior() -> None:
    # Deterministic heuristic: single function words are inherently ambiguous;
    # ties break to the earliest candidate (es). "hola" alone is UNKNOWN. These
    # are first-class outcomes, not bugs (documented in node3/preprocess.py).
    assert detect_language(["no"]) == "es"
    assert detect_language(["la"]) == "es"
    assert detect_language(["de"]) == "es"
    assert detect_language(["hola"]) is None


def test_reference_date_constant() -> None:
    assert date(2026, 8, 15) == REFERENCE_DATE
