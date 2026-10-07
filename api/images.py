from __future__ import annotations

import base64
import binascii
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from ..shared.errors import MusicApiError
from .http import DEFAULT_TIMEOUT


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MusicApiError(f"解析 qqyy-php 响应失败: {field_name} 缺失或格式异常")
    return value


def _require_non_empty_str(value: Any, field_name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise MusicApiError(f"解析 qqyy-php 响应失败: {field_name} 缺失")
    return text


def _safe_output_path(output_dir: Path, file_name: Any, default_name: str) -> Path:
    # 服务端返回的文件名只作为展示名使用，不能携带目录跳转。
    name = Path(str(file_name or "")).name.strip()
    if not name or name in {".", ".."}:
        name = default_name
    return output_dir / name


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _decode_data_url_image(data_url: str) -> bytes:
    if not isinstance(data_url, str):
        raise MusicApiError("图片数据格式异常")
    encoded = data_url.split(",", 1)[1] if "," in data_url else data_url
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MusicApiError("图片数据格式异常: base64 解码失败") from exc


def save_data_url_image(data_url: str, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_decode_data_url_image(data_url))
    return output_path


def parse_qr_code_payload(data: Any, qr_type: str) -> tuple[str, str, dict[str, Any]]:
    data = _require_mapping(data, "data")
    qrcode_source = data.get("qrcode") if isinstance(data.get("qrcode"), dict) else data
    qrcode_payload = dict(qrcode_source)
    image = qrcode_payload.get("data") or qrcode_payload.get("qrimg")
    base64_image = _require_non_empty_str(image, "data")
    if base64_image.startswith("data:image/"):
        header, base64_image = base64_image.split(",", 1)
        qrcode_payload["mimetype"] = header[5:].split(";", 1)[0]
    identifier = _require_non_empty_str(
        qrcode_payload.get("identifier")
        or qrcode_payload.get("key")
        or qrcode_payload.get("unikey"),
        "identifier",
    )
    qrcode_payload["data"] = base64_image
    qrcode_payload["identifier"] = identifier
    qrcode_payload["qr_type"] = str(qrcode_payload.get("qr_type") or qr_type)
    return base64_image, identifier, qrcode_payload


def _copy_php_output_image(result: dict[str, Any], output_dir: str | Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data_url = result.get("data") or result.get("data_url") or result.get("base64")
    if isinstance(data_url, str) and data_url.strip():
        return save_data_url_image(
            data_url,
            _safe_output_path(
                output_dir, result.get("file_name"), "qq_music_report.jpg"
            ),
        )

    raw_path = _require_non_empty_str(result.get("url") or result.get("path"), "path")
    parsed = urlparse(raw_path)
    if parsed.scheme in {"http", "https"}:
        try:
            response = requests.request("GET", raw_path, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise MusicApiError(f"下载 qqyy-php 图片 URL 失败: {raw_path}") from exc
        destination = _safe_output_path(
            output_dir, Path(parsed.path).name, "qq_music_report.jpg"
        )
        destination.write_bytes(response.content)
        return destination

    source = Path(raw_path)
    try:
        source = source.resolve(strict=True)
    except OSError as exc:
        raise MusicApiError(f"qqyy-php 返回的图片路径不可访问: {source}") from exc
    output_root = output_dir.resolve()
    if not _is_relative_to(source, output_root):
        raise MusicApiError("qqyy-php 返回的本地图片路径不在允许目录内")

    destination = _safe_output_path(output_dir, source.name, "qq_music_report.jpg")
    try:
        if source.resolve() == destination.resolve():
            return source
    except OSError:
        pass
    shutil.copy2(source, destination)
    return destination
