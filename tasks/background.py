from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from threading import Event
from typing import Any

from astrbot.api.event import MessageChain

from ..shared.concurrency import run_blocking, run_blocking_batch
from ..shared.messages import build_daily_refresh_notification_text
from ..storage.accounts import StoredUserAccount
from .jobs import start_daily_auto_play_tasks, start_daily_sign_tasks

DAILY_REFRESH_ACCOUNT_DELAY_SECONDS = 1


def seconds_until_next_midnight(now: datetime | None = None) -> int:
    now = now or datetime.now().astimezone()
    next_midnight = (now + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return max(1, int((next_midnight - now).total_seconds()))


class BackgroundSettingsError(RuntimeError):
    pass


class BackgroundTaskManager:
    def __init__(
        self,
        *,
        config: Any,
        store: Any,
        auto_play: Any,
        qq_music: Any,
        targets: Any,
        context: Any,
        log: Any,
    ) -> None:
        self.config = config
        self.store = store
        self.auto_play = auto_play
        self.qq_music = qq_music
        self.daily_refresh_targets = targets
        self.context = context
        self.log = log
        self._task: asyncio.Task | None = None
        self._stop_event = Event()
        self._lock = asyncio.Lock()
        self.auto_play.enabled = self.is_enabled()

    def _section(self, name: str) -> dict[str, Any]:
        value = self.config.get(name, {})
        return value if isinstance(value, dict) else {}

    def is_enabled(self) -> bool:
        return bool(self._section("background_tasks").get("enabled", True))

    def is_daily_enabled(self) -> bool:
        return bool(self._section("daily_refresh").get("enabled", True))

    def is_notify_enabled(self) -> bool:
        return bool(self._section("daily_refresh").get("notify_enabled", True))

    async def set_enabled(self, enabled: bool) -> int:
        async with self._lock:
            had_config = "background_tasks" in self.config
            previous = self.config.get("background_tasks")
            self.config["background_tasks"] = {
                **self._section("background_tasks"),
                "enabled": enabled,
            }
            try:
                self.config.save_config()
            except Exception as exc:
                if had_config:
                    self.config["background_tasks"] = previous
                else:
                    self.config.pop("background_tasks", None)
                self.log.warning("后台任务设置保存失败: %s", exc)
                raise BackgroundSettingsError(
                    "后台任务设置保存失败，请稍后重试"
                ) from exc
            self.auto_play.enabled = enabled
            if enabled:
                self.ensure_started()
                return 0
            await self.stop_daily()
            return await self.auto_play.stop_all()

    async def shutdown(self) -> None:
        await self.stop_daily()
        await self.auto_play.shutdown()

    def ensure_started(self) -> None:
        if not self.is_enabled() or not self.is_daily_enabled():
            return
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            self.log.warning(
                "每日自动刷新任务启动失败：当前没有运行中的事件循环: %s", exc
            )
            return
        self._stop_event = Event()
        self._task = loop.create_task(self._daily_loop())

    async def stop_daily(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _daily_loop(self) -> None:
        while self.is_enabled():
            try:
                delay_seconds = seconds_until_next_midnight()
                self.log.info("[每日刷新] 下次自动刷新将在 %d 秒后执行", delay_seconds)
                await asyncio.sleep(delay_seconds)
                if not self.is_enabled():
                    return
                if not self.is_daily_enabled():
                    self.log.info("[每日刷新] 配置已关闭，跳过本次自动刷新")
                    continue
                await self.run_daily_job()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.log.warning(
                    "[每日刷新] 自动刷新任务异常，将等待下一次调度: %s", exc
                )

    async def run_daily_job(self) -> None:
        if not self.is_enabled() or not self.is_daily_enabled():
            return
        stop_event = self._stop_event

        def should_continue() -> bool:
            return not stop_event.is_set() and self.is_enabled()

        accounts = self.store.list_all_accounts()
        total_count = len(accounts)
        success_count = 0
        failed_count = 0
        refreshed_accounts: list[StoredUserAccount] = []

        if accounts:

            def _refresh(item: StoredUserAccount) -> tuple[str, bool, str]:
                if not should_continue():
                    return item.account.alias, False, "本地后台任务已关闭"
                ok, message = self.qq_music.refresh_account_key(
                    item.user_key, item.account
                )
                return item.account.alias, ok, message

            done = await run_blocking_batch(
                accounts,
                _refresh,
                submit_delay_seconds=DAILY_REFRESH_ACCOUNT_DELAY_SECONDS,
                return_exceptions=True,
            )
            for item, result in zip(accounts, done):
                if isinstance(result, BaseException):
                    failed_count += 1
                    self.log.warning(
                        "[每日刷新] 账号刷新异常: user=%s alias=%s error=%s",
                        item.user_key,
                        item.account.alias,
                        result,
                    )
                    continue

                alias, ok, message = result
                if ok:
                    success_count += 1
                    refreshed_accounts.append(item)
                else:
                    failed_count += 1
                    self.log.warning(
                        "[每日刷新] 账号刷新失败: user=%s alias=%s message=%s",
                        item.user_key,
                        alias,
                        message,
                    )

        auto_play_summary = start_daily_auto_play_tasks(
            self.store,
            self.auto_play,
            refreshed_accounts,
            self.log,
        )
        sign_summary = await run_blocking(
            start_daily_sign_tasks,
            self.store,
            accounts,
            self.qq_music.daily_sign_account,
            self.log,
            should_continue=should_continue,
        )
        text = build_daily_refresh_notification_text(
            total_count,
            success_count,
            failed_count,
            daily_sign_success_count=sign_summary.success_count,
            daily_sign_failed_count=sign_summary.failed_count,
            auto_play_started_count=auto_play_summary.started_count,
            auto_play_running_count=auto_play_summary.running_count,
            auto_play_failed_count=auto_play_summary.failed_count,
        )
        self.log.info(
            "[每日刷新] 自动刷新完成: total=%d success=%d failed=%d sign_success=%d sign_failed=%d auto_play_started=%d auto_play_running=%d auto_play_failed=%d",
            total_count,
            success_count,
            failed_count,
            sign_summary.success_count,
            sign_summary.failed_count,
            auto_play_summary.started_count,
            auto_play_summary.running_count,
            auto_play_summary.failed_count,
        )
        if self.is_notify_enabled():
            await self._notify(text)

    async def _notify(self, text: str) -> None:
        targets = self.daily_refresh_targets.list_targets()
        if not targets:
            self.log.info("[每日刷新] 未配置通知群，跳过主动通知")
            return

        for target in targets:
            try:
                sent = await self.context.send_message(
                    target.unified_msg_origin,
                    MessageChain().message(text),
                )
            except Exception as exc:
                self.log.warning(
                    "[每日刷新] 通知发送失败: target=%s error=%s",
                    target.label,
                    exc,
                )
                continue
            if not sent:
                self.log.warning(
                    "[每日刷新] 通知发送失败，未找到目标平台: target=%s",
                    target.label,
                )
