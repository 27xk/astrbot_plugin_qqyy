from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Any

from astrbot.api.event import AstrMessageEvent, MessageEventResult

from ..storage.accounts import StoredAccount

DEFAULT_DENY_MESSAGE = "当前用户或群组未被允许使用 QQ 音乐听歌报告插件"


def require_allowed(func):
    @wraps(func)
    async def wrapper(
        self, event: AstrMessageEvent, *args: Any, **kwargs: Any
    ) -> MessageEventResult:
        denied = self.deny_if_not_allowed(event)
        return (
            denied if denied is not None else await func(self, event, *args, **kwargs)
        )

    return wrapper


def build_user_key(platform_name: str, sender_id: str) -> str:
    return f"{platform_name}:{sender_id}"


def is_group_admin(raw_message: dict[str, Any]) -> bool:
    sender = raw_message.get("sender")
    return isinstance(sender, dict) and sender.get("role") in {"admin", "owner"}


def _normalize_sender_id(value: Any) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None


def extract_reply_sender_id(raw_message: dict[str, Any]) -> str | None:
    # 兼容不同平台 adapter 的 reply 字段结构。
    reply = raw_message.get("reply")
    if isinstance(reply, dict):
        sender = reply.get("sender")
        if isinstance(sender, dict):
            for key in ("user_id", "id", "sender_id", "from_user_id"):
                if sender_id := _normalize_sender_id(sender.get(key)):
                    return sender_id
        for key in ("user_id", "sender_id", "from_user_id"):
            if reply_sender_id := _normalize_sender_id(reply.get(key)):
                return reply_sender_id

    message = raw_message.get("message")
    if isinstance(message, list):
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "reply":
                continue
            data = segment.get("data")
            if not isinstance(data, dict):
                continue
            for key in ("user_id", "sender_id", "from_user_id"):
                if reply_sender_id := _normalize_sender_id(data.get(key)):
                    return reply_sender_id
    return None


def resolve_target_user_key(
    platform_name: str,
    sender_id: str,
    raw_message: dict[str, Any],
    allow_reply_target: bool = False,
) -> str:
    if allow_reply_target and (reply_sender_id := extract_reply_sender_id(raw_message)):
        return build_user_key(platform_name, reply_sender_id)
    return build_user_key(platform_name, sender_id)


@dataclass(frozen=True)
class ResolvedAccount:
    user_key: str
    account: StoredAccount


class AccessControlMixin:
    config: Any
    store: Any

    @staticmethod
    def get_sender_context(
        event: AstrMessageEvent,
    ) -> tuple[str, str, dict[str, Any], str]:
        platform_name = str(event.get_platform_name())
        sender_id = str(event.get_sender_id())
        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message", {})
        raw_message = raw_message if isinstance(raw_message, dict) else {}
        return (
            platform_name,
            sender_id,
            raw_message,
            build_user_key(platform_name, sender_id),
        )

    def get_access_control_config(self) -> dict[str, Any]:
        config = (
            self.config.get("access_control", {}) if hasattr(self.config, "get") else {}
        )
        return config if isinstance(config, dict) else {}

    @staticmethod
    def normalize_id_list(value: Any) -> set[str]:
        return (
            {str(item).strip() for item in value if str(item).strip()}
            if isinstance(value, list)
            else set()
        )

    def get_reply_delegate_user_ids(self) -> set[str]:
        access_control = self.get_access_control_config()
        delegate_user_ids = self.normalize_id_list(
            access_control.get("delegate_user_ids", [])
        )
        if delegate_user_ids:
            return delegate_user_ids
        return self.normalize_id_list(access_control.get("allowed_user_ids", []))

    def can_delegate_reply_target(
        self, event: AstrMessageEvent, sender_id: str, raw_message: dict[str, Any]
    ) -> bool:
        access_control = self.get_access_control_config()
        if not access_control.get("enabled", False):
            return False
        group_id = getattr(event, "get_group_id", lambda: None)()
        if group_id is None:
            return False
        # 回复代查同时要求平台识别为群管理者，并命中显式用户白名单。
        return sender_id in self.get_reply_delegate_user_ids() and is_group_admin(
            raw_message
        )

    def is_event_allowed(self, event: AstrMessageEvent) -> bool:
        access_control = self.get_access_control_config()
        if not access_control.get("enabled", False):
            return True

        allowed_user_ids = self.normalize_id_list(
            access_control.get("allowed_user_ids", [])
        )
        allowed_group_ids = self.normalize_id_list(
            access_control.get("allowed_group_ids", [])
        )
        if not allowed_user_ids and not allowed_group_ids:
            return False

        sender_id = str(event.get_sender_id())
        group_id = getattr(event, "get_group_id", lambda: None)()
        return sender_id in allowed_user_ids or (
            group_id is not None and str(group_id) in allowed_group_ids
        )

    def access_denied_result(self, event: AstrMessageEvent) -> MessageEventResult:
        message = str(
            self.get_access_control_config().get("deny_message") or DEFAULT_DENY_MESSAGE
        )
        return event.plain_result(message)

    def deny_if_not_allowed(self, event: AstrMessageEvent) -> MessageEventResult | None:
        return (
            None if self.is_event_allowed(event) else self.access_denied_result(event)
        )

    def resolve_query_account_context(
        self, event: AstrMessageEvent, alias: str = ""
    ) -> ResolvedAccount:
        platform_name, sender_id, raw_message, _ = self.get_sender_context(event)
        user_key = resolve_target_user_key(
            platform_name,
            sender_id,
            raw_message,
            allow_reply_target=self.can_delegate_reply_target(
                event, sender_id, raw_message
            ),
        )
        return ResolvedAccount(
            user_key=user_key,
            account=self.store.resolve_account(user_key, alias or None),
        )
