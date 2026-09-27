"""Uvicorn entry point (ROADMAP Phase 8).

Run with ``churn-survival-api`` or ``uvicorn api.main:app``.
"""

from __future__ import annotations

from api.app import create_app

app = create_app()


def run() -> None:
    """Console-script entry: serve the app with uvicorn."""
    import uvicorn

    from config.settings import get_settings

    settings = get_settings()
    uvicorn.run("api.main:app", host=settings.API_HOST, port=settings.API_PORT)


if __name__ == "__main__":
    run()
