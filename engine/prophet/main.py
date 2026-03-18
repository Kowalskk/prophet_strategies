"""
Prophet Engine entry point.

Usage
-----
Run directly::

    python -m prophet.main

Or via uvicorn CLI::

    uvicorn prophet.api.app:app --host 0.0.0.0 --port 8000

The module reads host/port from :data:`~prophet.config.settings` so values
in the ``.env`` file are picked up automatically.
"""

from __future__ import annotations

import logging

import uvicorn

from prophet.config import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

logger = logging.getLogger(__name__)


def main() -> None:
    logger.info(
        "Starting Prophet Engine on %s:%d  (paper_trading=%s)",
        settings.api_host,
        settings.api_port,
        settings.paper_trading,
    )
    uvicorn.run(
        "prophet.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
