"""기체 프리셋 저장·불러오기 (%APPDATA%/HoverThrottle/presets.json)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, fields
from pathlib import Path

from .analysis.pipeline import CraftInput
from .applog import app_dir

_log = logging.getLogger(__name__)


def presets_path() -> Path:
    return app_dir() / "presets.json"


class PresetStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else presets_path()
        self._data: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = raw.get("presets", {}) if isinstance(raw, dict) else {}
        except FileNotFoundError:
            self._data = {}
        except (OSError, ValueError) as e:
            _log.warning("presets.json 읽기 실패: %s", e)
            self._data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "presets": self._data}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def names(self) -> list[str]:
        return sorted(self._data, key=str.lower)

    def get(self, name: str) -> CraftInput | None:
        d = self._data.get(name)
        if d is None:
            return None
        known = {f.name for f in fields(CraftInput)}
        return CraftInput(**{k: v for k, v in d.items() if k in known})

    def put(self, name: str, craft: CraftInput) -> None:
        d = asdict(craft)
        d["name"] = name
        self._data[name] = d
        self.save()

    def delete(self, name: str) -> None:
        if self._data.pop(name, None) is not None:
            self.save()
