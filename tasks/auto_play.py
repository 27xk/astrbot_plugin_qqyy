from __future__ import annotations

import asyncio
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from astrbot.api.event import AstrMessageEvent, MessageChain

from ..api.qqmusic import QQMusicClient, report_play_time
from ..shared.errors import MusicApiError
from ..shared.messages import build_message, format_play_time
from ..storage.accounts import StoredAccount

AUTO_PLAY_TARGET_SECONDS = 24 * 3600
AUTO_PLAY_MIN_INTERVAL_SECONDS = 60
AUTO_PLAY_MAX_INTERVAL_SECONDS = 3 * 60
AUTO_PLAY_QUERY_DELAY_SECONDS = 15
AUTO_PLAY_CHECK_EVERY = 1
AUTO_PLAY_MAX_CONSECUTIVE_REPORT_FAILURES = 3
AUTO_PLAY_MAX_WORKERS = 16


def calculate_auto_play_interval(current_play_seconds: int) -> int:
    remaining_play_seconds = max(
        0, AUTO_PLAY_TARGET_SECONDS - max(0, current_play_seconds)
    )
    if remaining_play_seconds <= 0:
        return 0

    return random.randint(
        AUTO_PLAY_MIN_INTERVAL_SECONDS,
        AUTO_PLAY_MAX_INTERVAL_SECONDS,
    )


def build_auto_play_result_text(
    alias: str,
    status: str,
    reason: str,
    details: list[str] | None = None,
) -> str:
    return build_message(
        "QQ 音乐自动刷时长",
        fields=[
            ("状态", status),
            ("账号", alias),
            ("原因", reason),
        ],
        extra_lines=details or [],
    )


@dataclass
class AutoPlayTaskState:
    account: StoredAccount
    task: asyncio.Task
    event: AstrMessageEvent | None = None


class AutoPlayManager:
    def __init__(self, logger: Any, enabled: bool = True) -> None:
        self.logger = logger
        self.enabled = enabled
        self.stop_generation = 0
        self.tasks: dict[tuple[str, str], AutoPlayTaskState] = {}
        self.executor = ThreadPoolExecutor(max_workers=AUTO_PLAY_MAX_WORKERS)

    @staticmethod
    def build_task_key(user_key: str, alias: str) -> tuple[str, str]:
        return user_key, alias

    @staticmethod
    def _make_client(account: StoredAccount) -> QQMusicClient:
        return QQMusicClient(account.uin)

    async def send_background_text(
        self, event: AstrMessageEvent | None, text: str
    ) -> None:
        if event is None:
            return
        try:
            await event.send(MessageChain().message(text))
        except Exception as exc:
            self.logger.warning("发送自动刷时长后台消息失败: %s", exc)

    def cleanup(self) -> None:
        for key, state in list(self.tasks.items()):
            if state.task.done():
                self.tasks.pop(key, None)

    def is_running(self, user_key: str, alias: str) -> bool:
        self.cleanup()
        state = self.tasks.get(self.build_task_key(user_key, alias))
        return state is not None and not state.task.done()

    async def report_once(self, account: StoredAccount) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.executor, report_play_time, account.uin)

    def start(
        self,
        user_key: str,
        account: StoredAccount,
        event: AstrMessageEvent | None = None,
        initial_count: int = 0,
        initial_delay: bool = False,
        initial_report: bool = False,
    ) -> bool:
        if not self.enabled:
            return False
        self.cleanup()
        key = self.build_task_key(user_key, account.alias)
        state = self.tasks.get(key)
        if state is not None and not state.task.done():
            return False

        task = asyncio.create_task(
            self._worker(
                user_key,
                account,
                initial_count=initial_count,
                initial_delay=initial_delay,
                initial_report=initial_report,
            )
        )
        self.tasks[key] = AutoPlayTaskState(account=account, task=task, event=event)
        task.add_done_callback(
            lambda done_task, task_key=key: self._on_task_done(task_key, done_task)
        )
        self.logger.info("[自动刷时长] 后台任务已创建: %s (key=%s)", account.alias, key)
        return True

    def _on_task_done(self, key: tuple[str, str], task: asyncio.Task) -> None:
        state = self.tasks.get(key)
        if state is not None and state.task is task:
            self.tasks.pop(key, None)
        else:
            state = None
        alias = state.account.alias if state else key[1]
        try:
            message = task.result()
        except asyncio.CancelledError:
            message = build_auto_play_result_text(alias, "已取消", "任务被取消")
        except Exception as exc:
            self.logger.exception("[自动刷时长] 后台任务异常: %s", exc)
            message = build_auto_play_result_text(alias, "异常停止", str(exc))

        self.logger.info("[自动刷时长] 任务结束: %s, 结果: %s", alias, message)

        if state is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self.logger.warning("[自动刷时长] 结束消息无法发送：事件循环已关闭")
            else:
                loop.create_task(self.send_background_text(state.event, message))

    async def _worker(
        self,
        user_key: str,
        account: StoredAccount,
        initial_count: int = 0,
        initial_delay: bool = False,
        initial_report: bool = False,
    ) -> str:
        self.logger.info(
            "[自动刷时长] worker 启动: %s (count=%d, delay=%s)",
            account.alias,
            initial_count,
            initial_delay,
        )
        client = self._make_client(account)
        loop = asyncio.get_running_loop()

        count = initial_count
        consecutive_report_failures = 0
        next_interval = calculate_auto_play_interval(0)
        if initial_report:
            # 首刷放进 worker，避免并发命令先上报后启动失败。
            try:
                await self.report_once(account)
            except Exception as exc:
                self.logger.warning("[自动刷时长] %s: 首刷失败: %s", account.alias, exc)
                return build_auto_play_result_text(
                    account.alias,
                    "失败停止",
                    "首刷失败",
                    [f"最后错误：{exc}", f"共刷：{count} 次"],
                )
            count += 1
            next_interval = calculate_auto_play_interval(0)

        if initial_delay:
            self.logger.info(
                "[自动刷时长] %s: 初始延迟 %ds 后查询进度",
                account.alias,
                AUTO_PLAY_QUERY_DELAY_SECONDS,
            )
            await asyncio.sleep(AUTO_PLAY_QUERY_DELAY_SECONDS)
            try:
                current = await loop.run_in_executor(
                    self.executor, client.get_account_play_time_seconds
                )
            except MusicApiError as exc:
                self.logger.warning(
                    "[自动刷时长] %s: 首刷进度检测失败，将继续重试: %s",
                    account.alias,
                    exc,
                )
                next_interval = calculate_auto_play_interval(0)
            else:
                self.logger.info(
                    "[自动刷时长] %s: 当前播放时长 %s",
                    account.alias,
                    format_play_time(current),
                )
                if current >= AUTO_PLAY_TARGET_SECONDS:
                    return build_auto_play_result_text(
                        account.alias,
                        "已完成",
                        "已播放时长达到 24 小时",
                        [f"共刷：{count} 次"],
                    )

                next_interval = calculate_auto_play_interval(current)
            self.logger.info(
                "[自动刷时长] %s: 等待 %ds 后开始下一次上报",
                account.alias,
                next_interval,
            )
            await asyncio.sleep(next_interval)

        while True:
            self.logger.info(
                "[自动刷时长] %s: 第 %d 次上报开始", account.alias, count + 1
            )
            try:
                await self.report_once(account)
            except Exception as exc:
                consecutive_report_failures += 1
                self.logger.warning("[自动刷时长] %s: 上报失败: %s", account.alias, exc)
                if (
                    consecutive_report_failures
                    >= AUTO_PLAY_MAX_CONSECUTIVE_REPORT_FAILURES
                ):
                    return build_auto_play_result_text(
                        account.alias,
                        "失败停止",
                        f"连续 {consecutive_report_failures} 次上报失败",
                        [f"最后错误：{exc}", f"共刷：{count} 次"],
                    )

                next_interval = calculate_auto_play_interval(0)
                self.logger.info(
                    "[自动刷时长] %s: 上报失败后等待 %ds 重试",
                    account.alias,
                    next_interval,
                )
                await asyncio.sleep(next_interval)
                continue

            consecutive_report_failures = 0
            count += 1
            self.logger.info("[自动刷时长] %s: 第 %d 次上报成功", account.alias, count)

            if count % AUTO_PLAY_CHECK_EVERY == 0:
                await asyncio.sleep(AUTO_PLAY_QUERY_DELAY_SECONDS)
                try:
                    current = await loop.run_in_executor(
                        self.executor, client.get_account_play_time_seconds
                    )
                except MusicApiError as exc:
                    self.logger.warning(
                        "[自动刷时长] %s: 进度检测失败，将继续重试: %s",
                        account.alias,
                        exc,
                    )
                    next_interval = calculate_auto_play_interval(0)
                    await asyncio.sleep(next_interval)
                    continue

                self.logger.info(
                    "[自动刷时长] %s: 检测播放时长 %s",
                    account.alias,
                    format_play_time(current),
                )

                if current >= AUTO_PLAY_TARGET_SECONDS:
                    return build_auto_play_result_text(
                        account.alias,
                        "已完成",
                        "已播放时长达到 24 小时",
                        [f"共刷：{count} 次"],
                    )

                next_interval = calculate_auto_play_interval(current)

            self.logger.info(
                "[自动刷时长] %s: 等待 %ds 后下一次上报", account.alias, next_interval
            )
            await asyncio.sleep(next_interval)

    async def stop_all(self) -> int:
        self.stop_generation += 1
        self.cleanup()
        states = list(self.tasks.values())
        for state in states:
            state.task.cancel()
        if states:
            await asyncio.gather(
                *(state.task for state in states),
                return_exceptions=True,
            )
        return len(states)

    async def shutdown(self) -> None:
        self.enabled = False
        await self.stop_all()
        self.executor.shutdown(wait=False, cancel_futures=True)
