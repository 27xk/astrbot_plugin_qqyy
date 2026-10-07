from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from astrbot.api.event import AstrMessageEvent

from ..storage.accounts import AccountStore, StoredAccount, StoredUserAccount
from .auto_play import AutoPlayManager


@dataclass(frozen=True)
class AutoPlayStartResult:
    started: tuple[str, ...] = ()
    running: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()

    @property
    def started_count(self) -> int:
        return len(self.started)

    @property
    def running_count(self) -> int:
        return len(self.running)

    @property
    def failed_count(self) -> int:
        return len(self.failed)


class AutoPlayStartCollector:
    def __init__(self) -> None:
        self._started: list[str] = []
        self._running: list[str] = []
        self._failed: list[str] = []

    def record(self, alias: str, started: bool) -> None:
        (self._started if started else self._running).append(alias)

    def record_failed(self, alias: str) -> None:
        self._failed.append(alias)

    def to_result(self) -> AutoPlayStartResult:
        return AutoPlayStartResult(
            started=tuple(self._started),
            running=tuple(self._running),
            failed=tuple(self._failed),
        )


@dataclass(frozen=True)
class DailySignResult:
    success_count: int = 0
    failed_count: int = 0


class DailySignCollector:
    def __init__(self) -> None:
        self.success_count = 0
        self.failed_count = 0

    def record(self, ok: bool) -> None:
        if ok:
            self.success_count += 1
        else:
            self.failed_count += 1

    def to_result(self) -> DailySignResult:
        return DailySignResult(
            success_count=self.success_count,
            failed_count=self.failed_count,
        )


def start_daily_auto_play_tasks(
    store: AccountStore,
    auto_play: AutoPlayManager,
    refreshed_accounts: list[StoredUserAccount],
    log: Any | None = None,
) -> AutoPlayStartResult:
    collector = AutoPlayStartCollector()

    for item in refreshed_accounts:
        try:
            account = store.resolve_account(item.user_key, item.account.alias)
            collector.record(account.alias, auto_play.start(item.user_key, account))
        except Exception as exc:
            collector.record_failed(item.account.alias)
            if log is not None:
                log.warning(
                    "[每日刷新] 启动自动刷时长失败: user=%s alias=%s error=%s",
                    item.user_key,
                    item.account.alias,
                    exc,
                )
    return collector.to_result()


def start_daily_sign_tasks(
    store: AccountStore,
    accounts: list[StoredUserAccount],
    signer: Any,
    log: Any | None = None,
    should_continue: Callable[[], bool] | None = None,
) -> DailySignResult:
    collector = DailySignCollector()

    for item in accounts:
        if should_continue is not None and not should_continue():
            break
        try:
            account = store.resolve_account(item.user_key, item.account.alias)
            ok, message = signer(account)
        except Exception as exc:
            collector.record(False)
            if log is not None:
                log.warning(
                    "[每日签到] 签到异常: user=%s alias=%s error=%s",
                    item.user_key,
                    item.account.alias,
                    exc,
                )
            continue

        collector.record(ok)
        if not ok and log is not None:
            log.warning(
                "[每日签到] 签到失败: user=%s alias=%s message=%s",
                item.user_key,
                account.alias,
                message,
            )
    return collector.to_result()


async def start_auto_play_accounts(
    auto_play: AutoPlayManager,
    user_key: str,
    accounts: list[StoredAccount],
    event: AstrMessageEvent | None = None,
    account_delay_seconds: int = 0,
    sleep: Any = asyncio.sleep,
) -> AutoPlayStartResult:
    collector = AutoPlayStartCollector()
    stop_generation = auto_play.stop_generation

    for index, account in enumerate(accounts):
        if index > 0 and account_delay_seconds > 0:
            await sleep(account_delay_seconds)
        if not auto_play.enabled or auto_play.stop_generation != stop_generation:
            break
        collector.record(account.alias, auto_play.start(user_key, account, event))
    return collector.to_result()
