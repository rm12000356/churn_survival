from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from router.fingerprint import extract_fingerprint, header_hash

FIXTURES = Path(__file__).parent.parent / "adapters" / "fixtures"


def test_csv_fingerprint_is_stable() -> None:
    frame = pd.read_csv(FIXTURES / "clean_customers.csv")
    first = extract_fingerprint(frame)
    second = extract_fingerprint(frame)
    assert first.model_dump() == second.model_dump()
    assert first.sheet_names == []
    assert "customer_id" in first.column_names
    assert len(first.headers_hash) == 64


def test_fingerprint_depends_on_schema_signature() -> None:
    a = pd.read_csv(FIXTURES / "clean_customers.csv")
    b = pd.read_csv(FIXTURES / "stripe_export.csv")
    assert extract_fingerprint(a).headers_hash != extract_fingerprint(b).headers_hash


def test_header_hash_is_deterministic_and_order_independent() -> None:
    assert header_hash(["a", "b", "c"]) == header_hash(["c", "b", "a"])
    assert header_hash(["a", "b"]) != header_hash(["a", "b", "c"])


def test_excel_fingerprint_records_sheet_names() -> None:
    customers = pd.read_csv(FIXTURES / "clean_customers.csv")
    invoices = pd.DataFrame({"invoice_id": ["INV-1"], "amount": [100.0]})
    fingerprint = extract_fingerprint({"Customers": customers, "Invoices": invoices})
    assert fingerprint.sheet_names == ["Customers", "Invoices"]
    assert fingerprint.column_names[0] == "customer_id"


def test_fingerprint_rejects_unknown_input() -> None:
    with pytest.raises(TypeError):
        extract_fingerprint(["not", "a", "frame"])


def test_multi_sheet_rejects_empty() -> None:
    with pytest.raises(ValueError):
        extract_fingerprint({})
