from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .errors import MusicApiError

MessageField = tuple[str, Any]

REPORT_TYPE_MAP = {
    "年报": 4,
    "月报": 3,
    "周报": 2,
    "日报": 1,
}


def build_message(
    title: str,
    fields: Iterable[MessageField] = (),
    extra_lines: Iterable[str] = (),
    detail_title: str | None = None,
    details: Iterable[str] = (),
) -> str:
    lines = [str(title)]
    for label, value in fields:
        lines.append(f"{label}：{value}")

    lines.extend(str(item) for item in extra_lines if str(item))

    detail_lines = [str(item) for item in details if str(item)]
    if detail_lines:
        if detail_title:
            lines.append(f"{detail_title}：")
        lines.extend(f"- {item}" for item in detail_lines)

    return "\n".join(lines)


def build_auto_play_start_text(alias: str) -> str:
    return build_message(
        "QQ 音乐自动刷时长",
        fields=[
            ("状态", "已启动"),
            ("账号", alias),
            ("上报间隔", "1-3 分钟随机"),
            ("停止条件", "信息显示 24 小时或连续 3 次上报失败"),
        ],
    )


def build_all_auto_play_start_text(
    started: list[str],
    running: list[str],
    account_delay_seconds: int,
) -> str:
    extra_lines: list[str] = []
    if started:
        extra_lines.append("新启动账号：" + "、".join(started))
    if running:
        extra_lines.append("已运行账号：" + "、".join(running))
    return build_message(
        "QQ 音乐全部自动刷时长",
        fields=[
            ("状态", "已提交"),
            ("新启动", len(started)),
            ("已运行", len(running)),
            ("账号错峰", f"{account_delay_seconds} 秒"),
            ("上报间隔", "1-3 分钟随机"),
            ("停止条件", "信息显示 24 小时或连续 3 次上报失败"),
        ],
        extra_lines=extra_lines,
    )


def build_batch_result_text(
    title: str,
    total_count: int,
    success_count: int,
    detail_lines: list[str],
    account_delay_seconds: int | None = None,
) -> str:
    failed_count = max(0, total_count - success_count)
    fields: list[tuple[str, Any]] = [
        ("状态", "完成"),
        ("成功", f"{success_count}/{total_count}"),
        ("失败", failed_count),
    ]
    if account_delay_seconds is not None:
        fields.append(("账号错峰", f"{account_delay_seconds} 秒"))
    return build_message(
        title,
        fields=fields,
        detail_title="明细",
        details=detail_lines,
    )


def build_account_info_text(info: dict[str, Any]) -> str:
    def get_text(key: str, default: str = "未知") -> str:
        value = info.get(key, default)
        return str(value) if value is not None and str(value) else default

    return "\n".join(
        [
            "QQ 音乐账号信息",
            f"昵称：{get_text('nick')}",
            f"等级：{get_text('level')}",
            f"成长值：{get_text('value')}",
            f"距离下一级：{get_text('need')}",
            f"好友排名：{get_text('rank')}",
            f"已播放时长：{get_text('play_time')}",
            f"当前账号别名：{get_text('alias')}",
        ]
    )


def format_play_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} 秒"

    minutes, remain_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {remain_seconds:02d} 秒"

    hours, remain_minutes = divmod(minutes, 60)
    return f"{hours} 小时 {remain_minutes} 分"


def build_daily_refresh_notification_text(
    total_count: int,
    success_count: int,
    failed_count: int,
    daily_sign_success_count: int = 0,
    daily_sign_failed_count: int = 0,
    auto_play_started_count: int = 0,
    auto_play_running_count: int = 0,
    auto_play_failed_count: int = 0,
) -> str:
    if total_count <= 0:
        return build_message(
            "QQ 音乐每日任务",
            fields=[
                ("状态", "完成"),
                ("绑定账号", 0),
                ("提示", "当前没有绑定账号"),
            ],
        )

    fields: list[tuple[str, Any]] = [
        ("状态", "完成"),
        ("刷新成功", f"{success_count}/{total_count}"),
        ("刷新失败", failed_count),
        ("签到成功", f"{daily_sign_success_count}/{total_count}"),
        ("签到失败", daily_sign_failed_count),
        ("刷时长新启动", auto_play_started_count),
        ("刷时长已运行", auto_play_running_count),
    ]
    if auto_play_failed_count:
        fields.append(("刷时长启动失败", auto_play_failed_count))
    if failed_count:
        fields.append(("提示", "失败账号请使用 /qqyy 刷新 或 /qqyy 登录 重新处理"))
    return build_message(
        "QQ 音乐每日任务",
        fields=fields,
    )


def format_qq_music_client_error(
    exc: MusicApiError,
    prefix: str = "QQ 音乐接口请求失败",
) -> str:
    detail = str(exc).strip()
    guidance = "请检查 uin 是否与服务端登录缓存匹配，必要时重新扫码登录"
    return f"{prefix}：{detail}\n{guidance}" if detail else f"{prefix}，{guidance}"
