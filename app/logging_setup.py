"""Configuracion sencilla de logging para toda la aplicacion."""
from __future__ import annotations

import logging


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(message)s",
    )
