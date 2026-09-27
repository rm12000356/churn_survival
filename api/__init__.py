"""FastAPI serving layer (ROADMAP Phase 8).

The API never recomputes decisions on read; ``POST /runs`` delegates to the
orchestrator for the single computing path. See ``api.app.create_app``.
"""

from __future__ import annotations
