from __future__ import annotations

from typing import Any

from api.app import create_app
from config.settings import Settings

app = create_app()


def uvicorn_options(settings: Settings) -> dict[str, Any]:
    options: dict[str, Any] = {"host": settings.API_HOST, "port": settings.API_PORT}
    if settings.API_TRUSTED_PROXIES:
        options["proxy_headers"] = True
        options["forwarded_allow_ips"] = settings.API_TRUSTED_PROXIES
    return options


def run() -> None:
    import uvicorn

    from config.settings import get_settings

    settings = get_settings()
    uvicorn.run("api.main:app", **uvicorn_options(settings))


if __name__ == "__main__":
    run()
