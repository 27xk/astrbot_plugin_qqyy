from __future__ import annotations

from typing import Any

from ..shared.errors import MusicApiError
from .http import DEFAULT_TIMEOUT, _request_api_action
from .images import parse_qr_code_payload


def _netease_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise MusicApiError("网易云 HTTP API 响应格式异常")
    if data.get("platform") == "netease" and isinstance(data.get("data"), dict):
        return data["data"]
    return data


def fetch_netease_qr_code(
    timeout: int = DEFAULT_TIMEOUT, client_platform: str = "pc"
) -> tuple[str, str, dict[str, Any]]:
    if client_platform not in {"pc", "web"}:
        raise MusicApiError("网易云二维码客户端必须是 pc 或 web")
    data = _netease_data(
        _request_api_action(
            "login.get_qrcode",
            {"platform": "netease", "client_platform": client_platform},
            timeout=timeout,
        )
    )
    if data.get("risk_control") or data.get("status") == "risk_control":
        raise MusicApiError(
            "网易云扫码登录受限：" + str(data.get("message") or "上游要求安全环境验证")
        )
    return parse_qr_code_payload(data, "netease")


def poll_netease_qr_status(
    identifier: str,
    timeout: int = DEFAULT_TIMEOUT,
    qrcode: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    data = _netease_data(
        _request_api_action(
            "login.check_qrcode",
            {"platform": "netease", "identifier": identifier},
            timeout=max(1, min(DEFAULT_TIMEOUT, int(timeout))),
        )
    )
    status = str(data.get("status") or "")
    try:
        code = int(data.get("code", -1))
    except (TypeError, ValueError):
        code = -1
    if code == 800 or status == "expired":
        return {**data, "event": "TIMEOUT", "done": False}
    if data.get("risk_control") or status == "risk_control" or code in {8821, -462}:
        return {
            **data,
            "event": "OTHER",
            "done": False,
            "message": data.get("message")
            or "网易云扫码登录受限，上游要求安全环境验证",
        }
    if code == 803 and status in {"", "success"} and data.get("finished") is True:
        return {**data, "event": "DONE", "done": True}
    if code in {801, 802} and not data.get("risk_control"):
        return None
    return {
        **data,
        "event": "OTHER",
        "done": False,
        "message": data.get("message") or "网易云扫码登录失败，请重新生成二维码",
    }


def netease_account_id(credential: Any) -> str:
    if not isinstance(credential, dict) or credential.get("platform") not in {
        None,
        "netease",
    }:
        return ""
    profile = credential.get("profile")
    for value in (
        credential.get("uid"),
        credential.get("musicid"),
        credential.get("userId"),
        profile.get("userId") if isinstance(profile, dict) else None,
    ):
        text = str(value or "").strip()
        if text.isdigit() and int(text) > 0:
            return text
    return ""
