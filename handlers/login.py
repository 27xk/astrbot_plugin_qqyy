from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic as monotonic_time
from typing import Any
from uuid import uuid4

from astrbot.api.event import AstrMessageEvent

from ..api.credentials import qqmusic_account_id
from ..api.http import DEFAULT_TIMEOUT
from ..api.images import save_data_url_image
from ..api.netease import (
    fetch_netease_qr_code,
    netease_account_id,
    poll_netease_qr_status,
)
from ..api.qqmusic import fetch_qr_code, poll_qr_status
from ..shared.concurrency import run_blocking
from ..shared.errors import MusicApiError
from ..storage.accounts import AccountStoreError


def _cleanup_qr_image(path: Path, log: Any) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("清理登录二维码图片失败: %s", exc)


async def qr_login_flow(
    handler: Any, event: AstrMessageEvent, alias: str = "", *, platform: str = "qq"
):
    denied = handler.deny_if_not_allowed(event)
    if denied is not None:
        yield denied
        return
    _, _, _, user_key = handler.get_sender_context(event)
    name = "网易云" if platform == "netease" else "QQ 音乐"
    command = "/qqyy 网易云登录" if platform == "netease" else "/qqyy 登录"
    settings = handler.config.get("login", {})
    settings = settings if isinstance(settings, dict) else {}
    try:
        timeout_seconds = max(1, min(600, int(settings.get("timeout_seconds", 180))))
    except (TypeError, ValueError):
        timeout_seconds = 180

    alias = alias.strip()
    if not alias:
        alias = "大号"
        if any(a.alias == alias for a in handler.store.list_accounts(user_key)):
            yield event.plain_result(
                f'账号 "大号" 已存在，请指定别名，如：{command} 小号'
            )
            return

    try:
        if platform == "netease":
            base64_image, identifier, qrcode = await run_blocking(
                fetch_netease_qr_code,
                timeout=DEFAULT_TIMEOUT,
                client_platform=str(
                    settings.get("netease_client_platform") or "pc"
                ).lower(),
            )
            poller = poll_netease_qr_status
        else:
            base64_image, identifier, qrcode = await run_blocking(
                fetch_qr_code,
                timeout=DEFAULT_TIMEOUT,
                login_type=str(settings.get("qq_login_type") or "mobile").lower(),
            )
            poller = poll_qr_status
    except Exception as exc:
        handler.log.warning("获取二维码失败: %s", exc)
        detail = str(exc) if isinstance(exc, MusicApiError) else "请稍后重试"
        yield event.plain_result(f"获取{name}二维码失败：{detail}")
        return

    qr_path = handler.temp_dir / f"qr_{platform}_{uuid4().hex}.png"
    try:
        qr_path.parent.mkdir(parents=True, exist_ok=True)
        mimetype = qrcode.get("mimetype") or "image/png"
        save_data_url_image(f"data:{mimetype};base64,{base64_image}", qr_path)
    except Exception as exc:
        _cleanup_qr_image(qr_path, handler.log)
        handler.log.warning("保存二维码图片失败: %s", exc)
        yield event.plain_result("保存二维码图片失败，请稍后重试")
        return

    # Start listening before the QR is sent so quick scans are not missed.
    listener = asyncio.create_task(
        wait_for_qr_login_result(
            identifier,
            qrcode=qrcode,
            poller=poller,
            timeout_seconds=timeout_seconds,
            log=handler.log,
        )
    )
    try:
        await asyncio.sleep(0)
        yield event.image_result(str(qr_path))
        result = await listener
    except MusicApiError as exc:
        handler.log.warning("%s扫码登录监听失败: %s", name, exc)
        yield event.plain_result(f"{name}扫码登录监听失败：{exc}")
        return
    finally:
        if not listener.done():
            listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        _cleanup_qr_image(qr_path, handler.log)

    if result is None:
        yield event.plain_result(
            f"{timeout_seconds} 秒内未完成{name}登录，请重新使用 {command}"
        )
        return

    if result.get("done") is not True:
        status = str(result.get("event") or "").upper()
        messages = {
            "TIMEOUT": "二维码已过期或监听时间已到，请重新登录",
            "REFUSE": "扫码登录已取消，请重新登录",
        }
        message = str(
            result.get("message")
            or messages.get(status)
            or "扫码登录失败，请重新生成二维码"
        )
        yield event.plain_result(f"{name}：{message}")
        return

    credential_raw = result.get("credential")
    if not credential_raw:
        yield event.plain_result("登录失败，未获取到凭证")
        return

    if platform == "netease":
        account_id = netease_account_id(credential_raw)
    else:
        account_id = qqmusic_account_id(credential_raw)
    if not account_id:
        yield event.plain_result("登录失败，凭证中缺少必要字段")
        return

    try:
        handler.store.upsert_account(user_key, alias, account_id)
    except (AccountStoreError, OSError) as exc:
        handler.log.warning("保存%s账号绑定失败: %s", name, exc)
        yield event.plain_result(
            f"{name}登录已完成，但本地账号绑定保存失败，请稍后重试"
        )
        return
    yield event.plain_result(f'{name}扫码登录成功，已绑定账号 "{alias}"')


QR_LOGIN_TIMEOUT_SECONDS = 60
QR_LOGIN_RETRY_INTERVAL_SECONDS = 2
QR_LOGIN_MAX_CONSECUTIVE_ERRORS = 3


async def wait_for_qr_login_result(
    identifier: str,
    *,
    qrcode: dict[str, Any] | None = None,
    timeout_seconds: int = QR_LOGIN_TIMEOUT_SECONDS,
    retry_interval_seconds: float = QR_LOGIN_RETRY_INTERVAL_SECONDS,
    poller: Any = poll_qr_status,
    runner: Any = run_blocking,
    sleep: Any = asyncio.sleep,
    monotonic: Any = monotonic_time,
    log: Any | None = None,
) -> dict[str, Any] | None:
    deadline = monotonic() + max(1, int(timeout_seconds))
    consecutive_errors = 0
    last_error: Exception | None = None
    while (remaining := deadline - monotonic()) > 0:
        try:
            result = await runner(
                poller,
                identifier,
                timeout=max(1, int(remaining)),
                qrcode=qrcode,
            )
        except Exception as exc:
            consecutive_errors += 1
            last_error = exc
            if log is not None:
                log.warning("检测二维码状态失败，将继续等待: %s", exc)
            if consecutive_errors >= QR_LOGIN_MAX_CONSECUTIVE_ERRORS:
                raise MusicApiError(f"扫码登录监听连续失败：{exc}") from exc
            result = None
        else:
            consecutive_errors = 0
            last_error = None

        if result is not None:
            return result
        await sleep(min(retry_interval_seconds, max(0, deadline - monotonic())))
    if last_error is not None:
        raise MusicApiError(f"扫码登录监听失败：{last_error}") from last_error
    return None
