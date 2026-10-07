from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import time
from typing import Any


@dataclass(frozen=True)
class DailyRefreshTarget:
    unified_msg_origin: str
    label: str
    updated_at: int


class DailyRefreshTargetStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    def _load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {"targets": {}}
            try:
                with self.path.open("r", encoding="utf-8") as file:
                    payload = json.load(file)
            except json.JSONDecodeError:
                return {"targets": {}}
            return payload if isinstance(payload, dict) else {"targets": {}}

    def _save(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=str(self.path.parent),
                text=True,
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(payload, file, ensure_ascii=False, indent=2)
                    file.write("\n")
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(tmp_path, self.path)
            finally:
                tmp_path.unlink(missing_ok=True)

    @staticmethod
    def _normalize_origin(unified_msg_origin: str) -> str:
        return str(unified_msg_origin).strip()

    @staticmethod
    def _targets(payload: dict[str, Any]) -> dict[str, Any]:
        targets = payload.get("targets")
        if isinstance(targets, dict):
            return targets
        targets = {}
        payload["targets"] = targets
        return targets

    def add_target(self, unified_msg_origin: str, label: str = "") -> bool:
        unified_msg_origin = self._normalize_origin(unified_msg_origin)
        if not unified_msg_origin:
            return False

        with self._lock:
            payload = self._load()
            targets = self._targets(payload)
            is_new = unified_msg_origin not in targets
            targets[unified_msg_origin] = {
                "label": str(label).strip() or unified_msg_origin,
                "updated_at": int(time()),
            }
            self._save(payload)
            return is_new

    def remove_target(self, unified_msg_origin: str) -> bool:
        unified_msg_origin = self._normalize_origin(unified_msg_origin)
        if not unified_msg_origin:
            return False

        with self._lock:
            payload = self._load()
            targets = self._targets(payload)
            if unified_msg_origin not in targets:
                return False
            del targets[unified_msg_origin]
            self._save(payload)
            return True

    def list_targets(self) -> list[DailyRefreshTarget]:
        payload = self._load()
        result: list[DailyRefreshTarget] = []
        for unified_msg_origin, value in self._targets(payload).items():
            if (
                not isinstance(unified_msg_origin, str)
                or not unified_msg_origin.strip()
            ):
                continue
            if not isinstance(value, dict):
                continue
            label = value.get("label")
            updated_at = value.get("updated_at")
            result.append(
                DailyRefreshTarget(
                    unified_msg_origin=unified_msg_origin,
                    label=(
                        label
                        if isinstance(label, str) and label
                        else unified_msg_origin
                    ),
                    updated_at=updated_at if isinstance(updated_at, int) else 0,
                )
            )
        return result
