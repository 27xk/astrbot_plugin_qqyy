from __future__ import annotations

import json
from typing import Any


def _qqmusic_credential(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict) or raw.get("platform") not in (
        None,
        "",
        "qq",
        "qqmusic",
        "tencent",
    ):
        return None
    return raw


def qqmusic_account_id(raw: Any) -> str:
    credential = _qqmusic_credential(raw)
    if credential is None:
        return ""
    return str(credential.get("str_musicid") or credential.get("musicid") or "").strip()


def has_refreshed_credential(response: dict[str, Any]) -> bool:
    data = response.get("data", {})
    credential = data.get("credential") if isinstance(data, dict) else None
    credential = _qqmusic_credential(credential or response.get("credential"))
    return credential is not None and bool(
        qqmusic_account_id(credential) or str(credential.get("musickey") or "").strip()
    )
