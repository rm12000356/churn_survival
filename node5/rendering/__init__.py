"""Node 5 renderers (architecture §5.30, D-RENDER).

The core rendering path is deterministic JSON plus dependency-free HTML, both
consuming the same validated `Node5Output`.

**PDF is intentionally deferred (F-7).** The architecture lists PDF as "ideally"
(§5.30) but no PDF dependency is approved in `pyproject.toml` / §8.11, so Node 5
does not ship a PDF renderer and does not add a heavy dependency. The absence of
PDF does not affect JSON/HTML correctness; all renderers would consume the same
validated report object. Re-introduce PDF only when a dependency/format is
explicitly approved.
"""
