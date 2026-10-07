from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests

from ..shared.errors import MusicApiError

DEFAULT_TIMEOUT = 15
DEFAULT_API_ENDPOINT = "qqmusic_api.php"
DEFAULT_MOBILE_API_ENDPOINT = "qqmusic_mobile_login.php"


def _environment_value(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


_api_url = _environment_value("QQYY_API_URL", "QQYY_PHP_API_URL")
_api_token = _environment_value("QQYY_API_TOKEN", "QQYY_PHP_API_TOKEN")
_mobile_api_url = _environment_value("QQYY_MOBILE_API_URL", "QQYY_MOBILE_LOGIN_URL")


def _normalize_endpoint_url(api_url: str | None, default_endpoint: str) -> str:
    text = str(api_url or "").strip()
    if not text:
        raise MusicApiError("音乐 HTTP API 地址未配置，请先填写插件配置 qqyy_api.url")

    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MusicApiError("QQ 音乐 HTTP API 地址必须是 http/https URL")
    if parsed.query or parsed.fragment:
        raise MusicApiError("QQ 音乐 HTTP API 地址不能包含 query 或 fragment")
    if not parsed.path or parsed.path.endswith("/"):
        return f"{text.rstrip('/')}/{default_endpoint}"
    return text


def _normalize_api_url(api_url: str | None) -> str:
    return _normalize_endpoint_url(api_url, DEFAULT_API_ENDPOINT)


def _normalize_mobile_api_url(api_url: str | None = None) -> str:
    if _mobile_api_url:
        return _normalize_endpoint_url(_mobile_api_url, DEFAULT_MOBILE_API_ENDPOINT)

    normalized_api_url = _normalize_api_url(_api_url if api_url is None else api_url)
    parsed = urlparse(normalized_api_url)

    parent = parsed.path.rsplit("/", 1)[0]
    mobile_path = (
        f"{parent}/{DEFAULT_MOBILE_API_ENDPOINT}"
        if parent
        else f"/{DEFAULT_MOBILE_API_ENDPOINT}"
    )
    return urlunparse(parsed._replace(path=mobile_path, query="", fragment=""))


def configure_qqyy_api(
    api_url: str | None = None,
    api_token: str | None = None,
    mobile_url: str | None = None,
) -> None:
    global _api_url, _api_token, _mobile_api_url
    if api_url is not None:
        text = str(api_url).strip()
        _api_url = _normalize_api_url(text) if text else ""
    if api_token is not None:
        _api_token = str(api_token).strip()
    if mobile_url is not None:
        mobile_text = str(mobile_url).strip()
        _mobile_api_url = (
            _normalize_endpoint_url(mobile_text, DEFAULT_MOBILE_API_ENDPOINT)
            if mobile_text
            else ""
        )


def _json_form_value(value: Any) -> Any:
    if isinstance(value, dict | list | tuple):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _api_headers(api_token: str | None = None) -> dict[str, str]:
    token = _api_token if api_token is None else str(api_token).strip()
    return {"X-QQYY-API-Token": token} if token else {}


def _first_payload_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def _api_success_error_message(payload: dict[str, Any], action: str) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or f"{action} failed")
    return str(error or f"{action} failed")


def _mobile_success_error_message(payload: dict[str, Any], action: str) -> str:
    return str(payload.get("message") or f"{action} failed")


def _request_action_payload(
    action: str,
    params: dict[str, Any] | None = None,
    *,
    url: str,
    api_token: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
    api_name: str,
    code_message_keys: tuple[str, ...],
    success_error_message: Any,
) -> Any:
    form = {"action": action, **(params or {})}
    requester = session.request if session is not None else requests.request
    try:
        response = requester(
            "POST",
            url,
            data={key: _json_form_value(value) for key, value in form.items()},
            headers=_api_headers(api_token),
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise MusicApiError(f"{api_name} 响应不是合法 JSON: {action}") from exc
    except requests.RequestException as exc:
        raise MusicApiError(f"请求 {api_name} 失败: {action}: {exc}") from exc

    if not isinstance(payload, dict):
        raise MusicApiError(f"{api_name} 响应格式异常: {action}")

    # 服务端兼容两种协议：旧版 code/data 与新版 success/data。
    if "code" in payload:
        try:
            code = int(payload.get("code", 0))
        except (TypeError, ValueError):
            code = -1
        if code != 0:
            message = _first_payload_text(payload, *code_message_keys)
            raise MusicApiError(message or f"{action} failed")
        return payload.get("data")

    if payload.get("success") is True:
        return payload.get("data")
    if payload.get("success") is False:
        raise MusicApiError(success_error_message(payload, action))

    return payload


def _request_action_text(
    action: str,
    params: dict[str, Any] | None = None,
    *,
    url: str,
    api_token: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
    api_name: str,
) -> str:
    form = {"action": action, **(params or {})}
    requester = session.request if session is not None else requests.request
    try:
        response = requester(
            "POST",
            url,
            data={key: _json_form_value(value) for key, value in form.items()},
            headers=_api_headers(api_token),
            timeout=timeout,
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        raise MusicApiError(f"请求 {api_name} 失败: {action}: {exc}") from exc


def _request_api_action(
    action: str,
    params: dict[str, Any] | None = None,
    *,
    api_url: str | None = None,
    api_token: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> Any:
    return _request_action_payload(
        action,
        params,
        url=_normalize_api_url(_api_url if api_url is None else api_url),
        api_token=api_token,
        timeout=timeout,
        session=session,
        api_name="网易云 HTTP API"
        if (params or {}).get("platform") == "netease"
        else "QQ 音乐 HTTP API",
        code_message_keys=("msg", "message"),
        success_error_message=_api_success_error_message,
    )


def _request_api_action_text(
    action: str,
    params: dict[str, Any] | None = None,
    *,
    api_url: str | None = None,
    api_token: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> str:
    return _request_action_text(
        action,
        params,
        url=_normalize_api_url(_api_url if api_url is None else api_url),
        api_token=api_token,
        timeout=timeout,
        session=session,
        api_name="QQ 音乐 HTTP API",
    )


def _request_mobile_login_action(
    action: str,
    params: dict[str, Any] | None = None,
    *,
    api_url: str | None = None,
    api_token: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> Any:
    return _request_action_payload(
        action,
        params,
        url=_normalize_mobile_api_url(api_url),
        api_token=api_token,
        timeout=timeout,
        session=session,
        api_name="QQ 音乐手机扫码接口",
        code_message_keys=("msg", "message"),
        success_error_message=_mobile_success_error_message,
    )


def configure_api_from_config(config: Any) -> None:
    settings = config.get("qqyy_api", {})
    if not isinstance(settings, dict) or not settings:
        settings = config.get("qqyy_php", {})
    settings = settings if isinstance(settings, dict) else {}
    url = str(settings.get("url") or settings.get("api_url") or "").strip()
    token = settings.get("token", settings.get("api_token"))
    mobile_url = settings.get("mobile_url")
    configure_qqyy_api(
        api_url=url or _environment_value("QQYY_API_URL", "QQYY_PHP_API_URL"),
        api_token=token
        if token is not None
        else _environment_value("QQYY_API_TOKEN", "QQYY_PHP_API_TOKEN"),
        mobile_url=mobile_url
        if mobile_url is not None
        else _environment_value("QQYY_MOBILE_API_URL", "QQYY_MOBILE_LOGIN_URL"),
    )
