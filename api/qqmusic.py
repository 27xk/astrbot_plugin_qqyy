from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from ..shared.errors import MusicApiError
from ..shared.messages import format_play_time
from .http import (
    DEFAULT_TIMEOUT,
    _request_api_action,
    _request_api_action_text,
    _request_mobile_login_action,
)
from .images import _copy_php_output_image, _require_mapping, parse_qr_code_payload

DEFAULT_QR_LOGIN_TYPE = "mobile"


def fetch_qr_code(
    timeout: int = DEFAULT_TIMEOUT, login_type: str = DEFAULT_QR_LOGIN_TYPE
) -> tuple[str, str, dict[str, Any]]:
    if login_type not in {"mobile", "qq", "wx"}:
        raise MusicApiError("QQ 音乐扫码登录方式必须是 mobile、qq 或 wx")
    if login_type == "mobile":
        data = _request_mobile_login_action(
            "get_qrcode", {"login_type": "mobile"}, timeout=timeout
        )
    else:
        data = _request_api_action(
            "login.get_qrcode",
            {"platform": "qq", "login_type": login_type},
            timeout=timeout,
        )
    return parse_qr_code_payload(data, login_type)


def _last_mobile_qr_result(data: dict[str, Any]) -> dict[str, Any] | None:
    results = data.get("results")
    if not isinstance(results, list):
        return None

    for item in reversed(results):
        if not isinstance(item, dict):
            continue
        event = str(item.get("event") or "").upper()
        if event in {"TIMEOUT", "REFUSE", "OTHER"}:
            return {**item, "done": False}
        if item.get("done") is True:
            return item
        if event == "DONE":
            return item
    return None


def poll_qr_status(
    identifier: str,
    timeout: int = 60,
    request_timeout: int = DEFAULT_TIMEOUT,
    qrcode: dict[str, Any] | None = None,
    generator_limit: int | None = None,
) -> dict[str, Any] | None:
    wait_seconds = max(1, int(timeout))
    qr_type = str((qrcode or {}).get("qr_type") or DEFAULT_QR_LOGIN_TYPE).lower()
    if qr_type in {"qq", "wx"}:
        data = _require_mapping(
            _request_api_action(
                "login.check_qrcode",
                {
                    "platform": "qq",
                    "identifier": identifier,
                    "login_type": qr_type,
                    "qrcode": qrcode or {},
                },
                timeout=max(1, min(request_timeout, wait_seconds)),
            ),
            "data",
        )
        event = str(data.get("event") or "").upper()
        if event in {"TIMEOUT", "REFUSE", "OTHER"}:
            return {**data, "done": False}
        return (
            data
            if data.get("done") is True
            or event in {"DONE", "TIMEOUT", "REFUSE", "OTHER"}
            else None
        )
    if qr_type != "mobile":
        raise MusicApiError(f"不支持的 QQ 音乐二维码类型：{qr_type}")
    params: dict[str, Any] = {"identifier": identifier, "timeout": wait_seconds}
    if isinstance(qrcode, dict):
        qrcode_payload = dict(qrcode)
        qrcode_payload["identifier"] = qrcode_payload.get("identifier") or identifier
        params["qrcode"] = qrcode_payload
    if generator_limit is not None:
        params["generator_limit"] = max(1, int(generator_limit))
    data = _request_mobile_login_action(
        "checking_mobile_qrcode",
        params,
        timeout=max(request_timeout, wait_seconds + 30),
    )
    return _last_mobile_qr_result(_require_mapping(data, "data"))


def refresh_credential(
    *,
    uin: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    params = {"uin": str(uin).strip()}
    data = _request_api_action(
        "login.refresh_credential",
        params,
        timeout=timeout,
    )
    return _require_mapping(data, "data")


def report_play_time(uin: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    # 该 action 的默认原始响应可能带乱码，固定要求服务端返回文本格式。
    params = {**{"uin": str(uin).strip()}, "format": "text"}
    text = _request_api_action_text(
        "report.report_play_duration",
        params,
        timeout=timeout,
    )
    if "RR" not in text:
        raise MusicApiError("上报播放时长失败: 响应未包含 RR")
    return text


def daily_sign_score(
    uin: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    params = {"uin": str(uin).strip()}
    data = _request_api_action(
        "report.every_day_sign_lvz_score",
        params,
        timeout=timeout,
    )
    return _require_mapping(data, "data")


class QQMusicClient:
    def __init__(
        self,
        uin: str,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        api_url: str | None = None,
        api_token: str | None = None,
    ) -> None:
        self.uin = str(uin).strip()
        self.timeout = timeout
        self.api_url = api_url
        self.api_token = api_token
        self.session = session

    def _call_action(self, action: str, params: dict[str, Any] | None = None) -> Any:
        merged = {**{"uin": self.uin}, **(params or {})}
        return _request_api_action(
            action,
            merged,
            api_url=self.api_url,
            api_token=self.api_token,
            timeout=self.timeout,
            session=self.session,
        )

    def fetch_sound_power_response(self) -> dict[str, Any]:
        return _require_mapping(self._call_action("report.get_sound_power"), "data")

    def get_account_play_time_seconds(self) -> int:
        info = self.fetch_sound_power_response()
        if info.get("available") is False:
            raise MusicApiError(str(info.get("message") or "查询 QQ 音乐账号信息失败"))
        try:
            return int(info["play_time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MusicApiError("解析 QQ 音乐账号播放时长失败") from exc

    def get_account_info(self, alias: str) -> dict[str, Any]:
        info = dict(self.fetch_sound_power_response())
        if info.get("available") is False:
            raise MusicApiError(str(info.get("message") or "查询 QQ 音乐账号信息失败"))
        try:
            play_time = int(info.get("play_time", 0))
        except (TypeError, ValueError) as exc:
            raise MusicApiError("解析 QQ 音乐账号信息失败") from exc

        info["alias"] = alias
        info["play_time"] = format_play_time(play_time)
        return info

    def download_summary_image(
        self,
        report_type: int,
        output_dir: str | Path,
    ) -> Path:
        result = _require_mapping(
            self._call_action(
                "report.download_summary_image",
                {"report_type": report_type},
            ),
            "data",
        )
        return _copy_php_output_image(result, output_dir)
