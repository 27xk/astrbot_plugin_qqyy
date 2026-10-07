from __future__ import annotations

from contextlib import aclosing
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent, MessageEventResult

from ..storage.accounts import AccountStore
from .access import AccessControlMixin, require_allowed
from .login import qr_login_flow


class NetEaseInteractionHandler(AccessControlMixin):
    def __init__(
        self, store: AccountStore, temp_dir: Path, config: Any, log: Any
    ) -> None:
        self.store = store
        self.temp_dir = temp_dir
        self.config = config
        self.log = log

    async def qr_login(self, event: AstrMessageEvent, alias: str = ""):
        async with aclosing(
            qr_login_flow(self, event, alias, platform="netease")
        ) as flow:
            async for result in flow:
                yield result

    @require_allowed
    async def list_accounts(self, event: AstrMessageEvent) -> MessageEventResult:
        _, _, _, user_key = self.get_sender_context(event)
        accounts = self.store.list_accounts(user_key)
        if not accounts:
            return event.plain_result(
                "你还没有绑定网易云账号，请先使用 /qqyy 网易云登录"
            )
        default_alias = self.store.get_default_alias(user_key)
        lines = ["你的网易云账户列表"]
        for account in accounts:
            mark = "（默认）" if account.alias == default_alias else ""
            lines.append(f"- {account.alias}{mark} uid: {account.uin}")
        return event.plain_result("\n".join(lines))
