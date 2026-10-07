from __future__ import annotations

from contextlib import aclosing
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent, MessageEventResult

from ..api.credentials import has_refreshed_credential
from ..api.qqmusic import (
    MusicApiError,
    QQMusicClient,
    daily_sign_score,
    refresh_credential,
    report_play_time,
)
from ..shared.concurrency import run_blocking, run_blocking_batch
from ..shared.messages import (
    build_account_info_text,
    build_all_auto_play_start_text,
    build_auto_play_start_text,
    build_batch_result_text,
    format_qq_music_client_error,
)
from ..storage.accounts import (
    AccountNotFoundError,
    AccountSelectionError,
    AccountStore,
    AccountStoreError,
    StoredAccount,
)
from ..storage.notifications import DailyRefreshTargetStore
from ..tasks.auto_play import AutoPlayManager
from ..tasks.jobs import start_auto_play_accounts
from .access import AccessControlMixin, ResolvedAccount, require_allowed
from .login import qr_login_flow

BATCH_PLAY_ACCOUNT_DELAY_SECONDS = 3
LOGIN_MESSAGE = "你还没有绑定 QQ 音乐账号，请先使用 /qqyy 登录"


class QQMusicInteractionHandler(AccessControlMixin):
    def __init__(
        self,
        store: AccountStore,
        auto_play: AutoPlayManager,
        temp_dir: Path,
        config: Any,
        daily_refresh_targets: DailyRefreshTargetStore,
        log: Any,
    ) -> None:
        self.store = store
        self.auto_play = auto_play
        self.temp_dir = temp_dir
        self.config = config if config is not None else {}
        self.daily_refresh_targets = daily_refresh_targets
        self.log = log

    def resolve_query_account_or_error(
        self,
        event: AstrMessageEvent,
        alias: str = "",
    ) -> tuple[StoredAccount | None, MessageEventResult | None]:
        resolved, error_result = self.resolve_query_account_context_or_error(
            event, alias
        )
        return (resolved.account if resolved is not None else None), error_result

    def resolve_query_account_context_or_error(
        self,
        event: AstrMessageEvent,
        alias: str = "",
    ) -> tuple[ResolvedAccount | None, MessageEventResult | None]:
        try:
            return self.resolve_query_account_context(event, alias), None
        except (AccountNotFoundError, AccountSelectionError) as exc:
            return None, event.plain_result(str(exc))

    def list_user_accounts_or_error(
        self,
        event: AstrMessageEvent,
        empty_message: str = LOGIN_MESSAGE,
    ) -> tuple[str, list[StoredAccount], MessageEventResult | None]:
        _, _, _, user_key = self.get_sender_context(event)
        accounts = self.store.list_accounts(user_key)
        if not accounts:
            return user_key, [], event.plain_result(empty_message)
        return user_key, accounts, None

    @staticmethod
    def make_client(account: StoredAccount) -> QQMusicClient:
        return QQMusicClient(account.uin)

    async def qr_login(self, event: AstrMessageEvent, alias: str = ""):
        async with aclosing(qr_login_flow(self, event, alias)) as flow:
            async for result in flow:
                yield result

    def refresh_account_key(
        self, user_key: str, account: StoredAccount
    ) -> tuple[bool, str]:
        try:
            resp = refresh_credential(uin=account.uin)
        except Exception as exc:
            self.log.warning("刷新服务端登录缓存失败: %s", exc)
            return False, "刷新服务端登录缓存失败，请稍后重试"

        if not has_refreshed_credential(resp):
            return False, "刷新失败，服务端返回的账号信息异常，请重新登录"

        try:
            self.store.resolve_account(user_key, account.alias)
        except AccountStoreError as exc:
            return False, str(exc)
        return True, "服务端登录缓存刷新成功"

    def daily_sign_account(self, account: StoredAccount) -> tuple[bool, str]:
        try:
            result = daily_sign_score(account.uin)
        except Exception as exc:
            self.log.warning("每日签到失败: %s", exc)
            return False, "签到请求失败，请稍后重试"

        ok = bool(result.get("available", False))
        try:
            code = int(result.get("code", 0))
        except (TypeError, ValueError):
            code = -1
        if code == 0:
            ok = True

        message = str(result.get("message") or ("签到成功" if ok else "签到失败"))
        return ok, message

    @require_allowed
    async def refresh_key(
        self, event: AstrMessageEvent, alias: str = ""
    ) -> MessageEventResult:
        resolved, error_result = self.resolve_query_account_context_or_error(
            event, alias
        )
        if error_result is not None or resolved is None:
            return error_result

        account = resolved.account
        ok, message = await run_blocking(
            self.refresh_account_key, resolved.user_key, account
        )
        return event.plain_result(f'账号 "{account.alias}" {message}')

    @require_allowed
    async def refresh_all_keys(self, event: AstrMessageEvent) -> MessageEventResult:
        user_key, accounts, empty_result = self.list_user_accounts_or_error(event)
        if empty_result is not None:
            return empty_result

        def _refresh(account: StoredAccount) -> tuple[str, bool, str]:
            ok, message = self.refresh_account_key(user_key, account)
            return account.alias, ok, message

        results = await run_alias_batch(
            accounts,
            _refresh,
            "批量刷新账号失败",
            (False, "刷新失败，请稍后重试"),
            self.log,
        )

        lines: list[str] = []
        success_count = 0
        for account in accounts:
            ok, message = results.get(account.alias, (False, "刷新失败"))
            if ok:
                success_count += 1
            status = "成功" if ok else "失败"
            lines.append(f"{account.alias}：{status}，{message}")

        return event.plain_result(
            build_batch_result_text(
                "QQ 音乐全部刷新",
                len(accounts),
                success_count,
                lines,
            )
        )

    @require_allowed
    async def enable_daily_refresh_notification(
        self, event: AstrMessageEvent
    ) -> MessageEventResult:
        return update_daily_refresh_notification(
            self.daily_refresh_targets,
            event,
            enabled=True,
        )

    @require_allowed
    async def disable_daily_refresh_notification(
        self, event: AstrMessageEvent
    ) -> MessageEventResult:
        return update_daily_refresh_notification(
            self.daily_refresh_targets,
            event,
            enabled=False,
        )

    @require_allowed
    async def play_time(
        self, event: AstrMessageEvent, alias: str = ""
    ) -> MessageEventResult:
        account, error_result = self.resolve_query_account_or_error(event, alias)
        if error_result is not None or account is None:
            return error_result

        try:
            await self.auto_play.report_once(account)
        except Exception as exc:
            self.log.warning("刷时长失败: %s", exc)
            return event.plain_result("刷时长失败，请稍后重试")

        return event.plain_result(f'账号 "{account.alias}" 刷时长成功（10 分钟）')

    @require_allowed
    async def auto_play_time(
        self, event: AstrMessageEvent, alias: str = ""
    ) -> MessageEventResult:
        if not self.auto_play.enabled:
            return event.plain_result(
                "本地后台任务已关闭，请联系 AstrBot 管理员使用 /qqyy 开启后台任务"
            )
        resolved, error_result = self.resolve_query_account_context_or_error(
            event, alias
        )
        if error_result is not None or resolved is None:
            return error_result

        account = resolved.account
        user_key = resolved.user_key
        if self.auto_play.is_running(user_key, account.alias):
            return event.plain_result(f'账号 "{account.alias}" 自动刷时长已在运行中')

        if not self.auto_play.start(
            user_key,
            account,
            event,
            initial_report=True,
            initial_delay=True,
        ):
            return event.plain_result(f'账号 "{account.alias}" 自动刷时长已在运行中')

        return event.plain_result(build_auto_play_start_text(account.alias))

    @staticmethod
    def pending_report_paths(event: AstrMessageEvent) -> list[str]:
        pending = getattr(event, "_qqyy_pending_report_paths", None)
        if isinstance(pending, list):
            return pending
        pending = []
        setattr(event, "_qqyy_pending_report_paths", pending)
        return pending

    @require_allowed
    async def switch_account(
        self, event: AstrMessageEvent, alias: str
    ) -> MessageEventResult:
        _, _, _, user_key = self.get_sender_context(event)
        try:
            self.store.set_default_account(user_key, alias)
        except AccountNotFoundError as exc:
            return event.plain_result(str(exc))
        return event.plain_result(f"已切换默认账号为 {alias}")

    @require_allowed
    async def delete_account(
        self, event: AstrMessageEvent, alias: str
    ) -> MessageEventResult:
        _, _, _, user_key = self.get_sender_context(event)
        try:
            self.store.delete_account(user_key, alias)
        except AccountNotFoundError as exc:
            return event.plain_result(str(exc))
        return event.plain_result(f"已删除账号 {alias}")

    @require_allowed
    async def list_accounts(self, event: AstrMessageEvent) -> MessageEventResult:
        user_key, accounts, empty_result = self.list_user_accounts_or_error(event)
        if empty_result is not None:
            return empty_result

        default_alias = self.store.get_default_alias(user_key)
        lines = ["你的 QQ 音乐账户列表"]
        for account in accounts:
            default_mark = "（默认）" if account.alias == default_alias else ""
            lines.append(f"- {account.alias}{default_mark} uin: {account.uin}")
        return event.plain_result("\n".join(lines))

    @require_allowed
    async def account_info(
        self, event: AstrMessageEvent, alias: str = ""
    ) -> MessageEventResult:
        account, error_result = self.resolve_query_account_or_error(event, alias)
        if error_result is not None or account is None:
            return error_result

        client = self.make_client(account)
        try:
            info = await run_blocking(client.get_account_info, account.alias)
        except MusicApiError as exc:
            self.log.warning(
                "查询 QQ 音乐账号信息失败: alias=%s uin=%s error=%s",
                account.alias,
                account.uin,
                exc,
            )
            return event.plain_result(format_qq_music_client_error(exc))
        return event.plain_result(build_account_info_text(info))

    @require_allowed
    async def all_account_info(self, event: AstrMessageEvent) -> MessageEventResult:
        _, accounts, empty_result = self.list_user_accounts_or_error(event)
        if empty_result is not None:
            return empty_result

        def _fetch(account: StoredAccount) -> tuple[str, str | None]:
            client = self.make_client(account)
            try:
                info = client.get_account_info(account.alias)
                return account.alias, build_account_info_text(info)
            except MusicApiError as exc:
                return account.alias, format_qq_music_client_error(exc, "查询失败")

        results = await run_alias_batch(
            accounts,
            _fetch,
            "批量查询账号信息失败",
            None,
            self.log,
        )

        lines: list[str] = []
        for account in accounts:
            text = results.get(account.alias)
            if text:
                lines.append(text)
            else:
                lines.append(f'账号 "{account.alias}" 查询失败')
            lines.append("")

        return event.plain_result("\n".join(lines).strip())

    @require_allowed
    async def all_play_time(self, event: AstrMessageEvent) -> MessageEventResult:
        _, accounts, empty_result = self.list_user_accounts_or_error(event)
        if empty_result is not None:
            return empty_result

        def _report(account: StoredAccount) -> tuple[str, bool]:
            try:
                report_play_time(account.uin)
                return account.alias, True
            except Exception:
                return account.alias, False

        results = await run_alias_batch(
            accounts,
            _report,
            "批量刷时长失败",
            False,
            self.log,
            submit_delay_seconds=BATCH_PLAY_ACCOUNT_DELAY_SECONDS,
        )

        lines: list[str] = []
        for account in accounts:
            ok = results.get(account.alias, False)
            status = "成功" if ok else "失败"
            lines.append(f"{account.alias}：{status}")

        return event.plain_result(
            build_batch_result_text(
                "QQ 音乐全部刷时长",
                len(accounts),
                sum(1 for ok in results.values() if ok),
                lines,
                account_delay_seconds=BATCH_PLAY_ACCOUNT_DELAY_SECONDS,
            )
        )

    @require_allowed
    async def all_auto_play_time(self, event: AstrMessageEvent) -> MessageEventResult:
        if not self.auto_play.enabled:
            return event.plain_result(
                "本地后台任务已关闭，请联系 AstrBot 管理员使用 /qqyy 开启后台任务"
            )
        user_key, accounts, empty_result = self.list_user_accounts_or_error(event)
        if empty_result is not None:
            return empty_result

        stop_generation = self.auto_play.stop_generation
        result = await start_auto_play_accounts(
            self.auto_play,
            user_key,
            accounts,
            event,
            BATCH_PLAY_ACCOUNT_DELAY_SECONDS,
        )
        if (
            not self.auto_play.enabled
            or self.auto_play.stop_generation != stop_generation
        ):
            return event.plain_result(
                "本次批量启动因后台任务关闭而取消，恢复后请重新发送 /qqyy 全部自动刷时长"
            )

        return event.plain_result(
            build_all_auto_play_start_text(
                list(result.started),
                list(result.running),
                BATCH_PLAY_ACCOUNT_DELAY_SECONDS,
            )
        )

    async def send_report(
        self,
        event: AstrMessageEvent,
        report_type: int,
        alias: str = "",
    ):
        denied = self.deny_if_not_allowed(event)
        if denied is not None:
            yield denied
            return
        account, error_result = self.resolve_query_account_or_error(event, alias)
        if error_result is not None or account is None:
            yield error_result
            return

        client = self.make_client(account)
        try:
            image_path = await run_blocking(
                client.download_summary_image, report_type, self.temp_dir
            )
        except MusicApiError:
            yield event.plain_result("生成总结图片失败，请稍后重试")
            return
        except OSError as exc:
            self.log.warning("保存总结图片失败: %s", exc)
            yield event.plain_result("保存总结图片失败，请稍后重试")
            return

        self.pending_report_paths(event).append(str(image_path))
        yield event.image_result(str(image_path))

    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        pending_paths = self.pending_report_paths(event)
        if not pending_paths:
            return

        while pending_paths:
            image_path = Path(pending_paths.pop())
            try:
                image_path.unlink(missing_ok=True)
            except OSError as exc:
                self.log.warning("删除临时图片失败: %s", exc)


async def run_alias_batch(
    accounts: list[StoredAccount],
    worker: Any,
    warning_message: str,
    default_value: Any,
    log: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    # 批量任务统一把异常折叠成默认值，避免每个命令重复写 try/except。
    done = await run_blocking_batch(
        accounts,
        worker,
        return_exceptions=True,
        **kwargs,
    )
    results: dict[str, Any] = {}
    for account, item in zip(accounts, done):
        if isinstance(item, BaseException):
            log.warning("%s: %s", warning_message, item)
            results[account.alias] = default_value
            continue
        alias, *values = item
        results[alias] = values[0] if len(values) == 1 else tuple(values)
    return results


def update_daily_refresh_notification(
    targets: DailyRefreshTargetStore,
    event: AstrMessageEvent,
    *,
    enabled: bool,
) -> MessageEventResult:
    # 群通知依赖 AstrBot 的会话标识，集中校验避免开关命令重复。
    action = "开启" if enabled else "关闭"
    group_id = getattr(event, "get_group_id", lambda: None)()
    if group_id is None:
        return event.plain_result(f"请在群聊中{action}日更通知")

    origin = str(getattr(event, "unified_msg_origin", "")).strip()
    if not origin:
        return event.plain_result(f"当前平台未提供会话标识，无法{action}日更通知")

    if enabled:
        label = f"{event.get_platform_name()} 群 {group_id}"
        return event.plain_result(
            "已开启本群每日 0 点自动刷新通知"
            if targets.add_target(origin, label)
            else "本群已开启每日 0 点自动刷新通知"
        )
    return event.plain_result(
        "已关闭本群每日 0 点自动刷新通知"
        if targets.remove_target(origin)
        else "本群尚未开启每日 0 点自动刷新通知"
    )
