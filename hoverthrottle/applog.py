"""상세 로그를 %APPDATA%/HoverThrottle/app.log 에 기록한다."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import APP_NAME


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging(level: int = logging.INFO) -> Path | None:
    try:
        path = app_dir() / "app.log"
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    # orangebox 는 프레임마다 debug 로그를 남기므로 경고 이상만 기록
    logging.getLogger("orangebox").setLevel(logging.WARNING)
    return path
