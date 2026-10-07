from __future__ import annotations

from contextlib import aclosing
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register

from .api.http import configure_api_from_config
from .handlers.netease import NetEaseInteractionHandler
from .handlers.qqmusic import QQMusicInteractionHandler
from .shared.messages import REPORT_TYPE_MAP, build_message
from .storage.accounts import AccountStore
from .storage.notifications import DailyRefreshTargetStore
from .tasks.auto_play import AutoPlayManager
from .tasks.background import BackgroundSettingsError, BackgroundTaskManager


@register("qqyy", "27xk", "QQ 音乐报告与网易云登录插件", "5.9.0")
class QQYYPlugin(Star):
    @filter.command_group("qqyy")
    def qqyy(self):
        pass

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or AstrBotConfig()
        configure_api_from_config(self.config)
        base_dir = Path(__file__).resolve().parent
        self.store = AccountStore(base_dir / "data" / "qqyy_accounts.json")
        self.netease_store = AccountStore(
            base_dir / "data" / "netease_accounts.json",
            id_field="uid",
            platform_name="网易云",
            login_command="/qqyy 网易云登录",
        )
        self.daily_refresh_targets = DailyRefreshTargetStore(
            base_dir / "data" / "qqyy_daily_refresh_targets.json"
        )
        self.temp_dir = base_dir / "tmp"
        self.auto_play = AutoPlayManager(logger)
        self.qq_music = QQMusicInteractionHandler(
            store=self.store,
            auto_play=self.auto_play,
            temp_dir=self.temp_dir,
            config=self.config,
            daily_refresh_targets=self.daily_refresh_targets,
            log=logger,
        )
        self.netease = NetEaseInteractionHandler(
            store=self.netease_store,
            temp_dir=self.temp_dir,
            config=self.config,
            log=logger,
        )
        self.background = BackgroundTaskManager(
            config=self.config,
            store=self.store,
            auto_play=self.auto_play,
            qq_music=self.qq_music,
            targets=self.daily_refresh_targets,
            context=self.context,
            log=logger,
        )
        self.background.ensure_started()

    async def _set_background_tasks_enabled(
        self, event: AstrMessageEvent, enabled: bool
    ) -> MessageEventResult:
        if not event.is_admin():
            return event.plain_result("仅 AstrBot 管理员可以开启或关闭本地后台任务")
        try:
            stopped_count = await self.background.set_enabled(enabled)
        except BackgroundSettingsError as exc:
            return event.plain_result(str(exc))
        if enabled:
            text = build_message(
                "QQ 音乐本地后台任务",
                fields=[("状态", "已开启")],
                extra_lines=["每日任务按配置调度，也可使用 /qqyy 自动刷时长"],
            )
        else:
            text = build_message(
                "QQ 音乐本地后台任务",
                fields=[("状态", "已关闭"), ("已停止刷时长任务", stopped_count)],
                extra_lines=[
                    "本地每日刷新、签到和自动刷时长已停用，重启后保持关闭",
                    "手动命令仍可使用；恢复请使用 /qqyy 开启后台任务",
                ],
            )
        return event.plain_result(text)

    @qqyy.command(
        "关闭后台任务",
        alias={"关闭"},
        desc="关闭本地每日任务和全部自动刷时长（管理员），用法：/qqyy 关闭后台任务",
    )
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def disable_background_tasks(
        self, event: AstrMessageEvent
    ) -> MessageEventResult:
        return await self._set_background_tasks_enabled(event, False)

    @qqyy.command(
        "开启后台任务",
        alias={"开启"},
        desc="开启本地后台任务（管理员），用法：/qqyy 开启后台任务",
    )
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def enable_background_tasks(
        self, event: AstrMessageEvent
    ) -> MessageEventResult:
        return await self._set_background_tasks_enabled(event, True)

    @qqyy.command("登录", desc="扫码登录 QQ 音乐，用法：/qqyy 登录 [别名]")
    async def qr_login(self, event: AstrMessageEvent, alias: str = ""):
        async with aclosing(self.qq_music.qr_login(event, alias)) as flow:
            async for result in flow:
                yield result

    @qqyy.command(
        "网易云登录",
        alias={"网易登录"},
        desc="使用网易云 App 扫码登录，用法：/qqyy 网易云登录 [别名]",
    )
    async def netease_qr_login(self, event: AstrMessageEvent, alias: str = ""):
        async with aclosing(self.netease.qr_login(event, alias)) as flow:
            async for result in flow:
                yield result

    @qqyy.command(
        "网易云账户列表",
        alias={"网易账户列表"},
        desc="查看当前用户绑定的网易云账号，用法：/qqyy 网易云账户列表",
    )
    async def netease_list_accounts(
        self, event: AstrMessageEvent
    ) -> MessageEventResult:
        return await self.netease.list_accounts(event)

    @qqyy.command("刷新", desc="刷新 QQ 音乐服务端登录缓存，用法：/qqyy 刷新 [别名]")
    async def refresh_key(
        self, event: AstrMessageEvent, alias: str = ""
    ) -> MessageEventResult:
        return await self.qq_music.refresh_key(event, alias)

    @qqyy.command(
        "全部刷新",
        desc="刷新所有绑定账号的 QQ 音乐服务端登录缓存，用法：/qqyy 全部刷新",
    )
    async def refresh_all_keys(self, event: AstrMessageEvent) -> MessageEventResult:
        return await self.qq_music.refresh_all_keys(event)

    @qqyy.command(
        "开启日更通知",
        desc="在当前群开启每日 0 点自动刷新结果通知，用法：/qqyy 开启日更通知",
    )
    async def enable_daily_refresh_notification(
        self, event: AstrMessageEvent
    ) -> MessageEventResult:
        return await self.qq_music.enable_daily_refresh_notification(event)

    @qqyy.command(
        "关闭日更通知",
        desc="在当前群关闭每日 0 点自动刷新结果通知，用法：/qqyy 关闭日更通知",
    )
    async def disable_daily_refresh_notification(
        self, event: AstrMessageEvent
    ) -> MessageEventResult:
        return await self.qq_music.disable_daily_refresh_notification(event)

    @qqyy.command("刷时长", desc="刷 QQ 音乐听歌时长，用法：/qqyy 刷时长 [别名]")
    async def play_time(
        self, event: AstrMessageEvent, alias: str = ""
    ) -> MessageEventResult:
        return await self.qq_music.play_time(event, alias)

    @qqyy.command(
        "自动刷时长",
        desc="后台自动刷时长，直到信息显示 24 小时，用法：/qqyy 自动刷时长 [别名]",
    )
    async def auto_play_time(
        self, event: AstrMessageEvent, alias: str = ""
    ) -> MessageEventResult:
        return await self.qq_music.auto_play_time(event, alias)

    @qqyy.command("切换", desc="切换默认账号，用法：/qqyy 切换 <别名>")
    async def switch_account(
        self, event: AstrMessageEvent, alias: str
    ) -> MessageEventResult:
        return await self.qq_music.switch_account(event, alias)

    @qqyy.command("删除", desc="删除已绑定账号，用法：/qqyy 删除 <别名>")
    async def delete_account(
        self, event: AstrMessageEvent, alias: str
    ) -> MessageEventResult:
        return await self.qq_music.delete_account(event, alias)

    @qqyy.command("账户列表", desc="查看当前绑定的全部账号，用法：/qqyy 账户列表")
    async def list_accounts(self, event: AstrMessageEvent) -> MessageEventResult:
        return await self.qq_music.list_accounts(event)

    @qqyy.command("信息", desc="查询账号信息，用法：/qqyy 信息 [别名]")
    async def account_info(
        self, event: AstrMessageEvent, alias: str = ""
    ) -> MessageEventResult:
        return await self.qq_music.account_info(event, alias)

    @qqyy.command("全部信息", desc="查询所有绑定账号的信息，用法：/qqyy 全部信息")
    async def all_account_info(self, event: AstrMessageEvent) -> MessageEventResult:
        return await self.qq_music.all_account_info(event)

    @qqyy.command(
        "全部刷时长",
        desc="为所有绑定账号各刷一次时长（10 分钟），用法：/qqyy 全部刷时长",
    )
    async def all_play_time(self, event: AstrMessageEvent) -> MessageEventResult:
        return await self.qq_music.all_play_time(event)

    @qqyy.command(
        "全部自动刷时长",
        desc="为所有账号后台自动刷时长直到信息各显示 24 小时，用法：/qqyy 全部自动刷时长",
    )
    async def all_auto_play_time(self, event: AstrMessageEvent) -> MessageEventResult:
        return await self.qq_music.all_auto_play_time(event)

    async def terminate(self):
        await self.background.shutdown()

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        await self.qq_music.after_message_sent(event)

    @qqyy.command("年报", desc="发送年度听歌报告图片，用法：/qqyy 年报 [别名]")
    async def yearly_report(self, event: AstrMessageEvent, alias: str = ""):
        async for result in self.qq_music.send_report(
            event, REPORT_TYPE_MAP["年报"], alias
        ):
            yield result

    @qqyy.command("月报", desc="发送月度听歌报告图片，用法：/qqyy 月报 [别名]")
    async def monthly_report(self, event: AstrMessageEvent, alias: str = ""):
        async for result in self.qq_music.send_report(
            event, REPORT_TYPE_MAP["月报"], alias
        ):
            yield result

    @qqyy.command("周报", desc="发送周度听歌报告图片，用法：/qqyy 周报 [别名]")
    async def weekly_report(self, event: AstrMessageEvent, alias: str = ""):
        async for result in self.qq_music.send_report(
            event, REPORT_TYPE_MAP["周报"], alias
        ):
            yield result

    @qqyy.command("日报", desc="发送日度听歌报告图片，用法：/qqyy 日报 [别名]")
    async def daily_report(self, event: AstrMessageEvent, alias: str = ""):
        async for result in self.qq_music.send_report(
            event, REPORT_TYPE_MAP["日报"], alias
        ):
            yield result
