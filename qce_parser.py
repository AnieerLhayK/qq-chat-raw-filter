"""
qce_parser.py — Parse QQ Chat Exporter (QCE) v5 JSON files into structured messages.

QCE v5 structure (observed):
  {"metadata": {...}, "chatInfo": {...}, "statistics": {...}, "messages": [...]}

Each message:
  {
    "id": str, "seq": str,
    "timestamp": int (unix ms),
    "time": str ("YYYY-MM-DD HH:mm:ss"),
    "sender": {"uid": str, "uin": str, "name": str, "nickname": str, ...},
    "type": str ("type_1"=text, "type_8"=file, ...),
    "content": {"text": str, "html": str, "elements": [...], "resources": [...], "mentions": [...]},
    "recalled": bool,
    "system": bool,
  }

The parser is tolerant: it will attempt to extract text/sender/time from
various common field names across QCE versions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# -- Constants for field name fallback resolution --

TEXT_FIELDS = ("text", "content", "message", "msg", "raw_message",
               "msgContent", "message_content", "plain", "plain_text")

SENDER_NAME_FIELDS = ("name", "senderName", "sender_name", "nickname",
                      "nick", "from", "fromName")

SENDER_ID_FIELDS = ("uin", "senderUin", "sender_id", "user_id",
                    "uid", "qq", "account", "fromUin")

TIMESTAMP_FIELDS = ("timestamp", "msgTime", "send_time",
                    "datetime", "time", "date")


class ParsedMessage:
    """One normalized message extracted from a QCE file."""

    __slots__ = (
        "msg_id", "seq", "timestamp_ms", "datetime_obj",
        "sender_uid", "sender_uin", "sender_name", "sender_nickname",
        "text", "msg_type", "is_system", "is_recalled",
        "raw_type", "elements",
    )

    def __init__(
        self,
        msg_id: str = "",
        seq: str = "",
        timestamp_ms: int = 0,
        sender_uid: str = "",
        sender_uin: str = "",
        sender_name: str = "",
        sender_nickname: str = "",
        text: str = "",
        msg_type: str = "text",
        is_system: bool = False,
        is_recalled: bool = False,
        raw_type: str = "",
        elements: Optional[List[Dict[str, Any]]] = None,
    ):
        self.msg_id = msg_id
        self.seq = seq
        self.timestamp_ms = timestamp_ms
        self.datetime_obj: Optional[datetime] = None
        self.sender_uid = sender_uid
        self.sender_uin = sender_uin
        self.sender_name = sender_name
        self.sender_nickname = sender_nickname
        self.text = text
        self.msg_type = msg_type  # normalized: "text", "image", "file", "system", "other"
        self.is_system = is_system
        self.is_recalled = is_recalled
        self.raw_type = raw_type
        self.elements = elements or []

    def __repr__(self) -> str:
        return (
            f"<ParsedMessage {self.msg_id} sender={self.sender_name} "
            f"len={len(self.text)} type={self.msg_type}>"
        )


class ChatFileInfo:
    """Metadata about a QCE chat file."""

    __slots__ = (
        "self_uid", "self_uin", "self_name",
        "chat_name", "chat_type",
        "file_path", "message_count",
    )

    def __init__(
        self,
        self_uid: str = "",
        self_uin: str = "",
        self_name: str = "",
        chat_name: str = "",
        chat_type: str = "private",
        file_path: str = "",
        message_count: int = 0,
    ):
        self.self_uid = self_uid
        self.self_uin = self_uin
        self.self_name = self_name
        self.chat_name = chat_name
        self.chat_type = chat_type
        self.file_path = file_path
        self.message_count = message_count


def _resolve_field(obj: Dict[str, Any], candidates: Tuple[str, ...], default: Any = "") -> Any:
    """Return the first present value from a list of candidate keys."""
    for key in candidates:
        val = obj.get(key)
        if val is not None and val != "":
            return val
    return default


def _safe_timestamp(ts_val: Any) -> Tuple[int, Optional[datetime]]:
    """Convert a timestamp value to (unix_ms, datetime_obj)."""
    try:
        if isinstance(ts_val, (int, float)):
            ts_ms = int(ts_val)
        elif isinstance(ts_val, str):
            if ts_val.replace("-", "").replace(":", "").replace(" ", "").isdigit():
                ts_ms = int(ts_val)
            else:
                for fmt in (
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%fZ",
                    "%Y/%m/%d %H:%M:%S",
                ):
                    try:
                        dt = datetime.strptime(ts_val[:26], fmt)
                        dt = dt.replace(tzinfo=timezone.utc)
                        ts_ms = int(dt.timestamp() * 1000)
                        return ts_ms, dt
                    except ValueError:
                        continue
                logger.warning("Could not parse timestamp string: %s", ts_val)
                return 0, None
        else:
            return 0, None
        if ts_ms < 1e12:
            dt = datetime.fromtimestamp(ts_ms, tz=timezone.utc)
        else:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return ts_ms, dt
    except (ValueError, OSError, OverflowError) as e:
        logger.warning("Timestamp conversion error for %r: %s", ts_val, e)
        return 0, None


def _extract_text_from_content(content: Any) -> str:
    """Extract text from content, which may be a dict or string."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = _resolve_field(content, TEXT_FIELDS)
        if text:
            return str(text)
        elements = content.get("elements") or []
        parts = []
        for elem in elements:
            if isinstance(elem, dict):
                elem_data = elem.get("data") or {}
                elem_text = elem_data.get("text", "")
                if elem_text:
                    parts.append(str(elem_text))
        if parts:
            return "\n".join(parts)
    return ""


def parse_qce_json(file_path: Path) -> Tuple[List[ParsedMessage], Optional[ChatFileInfo], List[str]]:
    """Parse a single QCE JSON file into messages and metadata.

    Returns:
        (messages, chat_info, warnings)
    """
    warnings: List[str] = []
    messages: List[ParsedMessage] = []
    chat_info: Optional[ChatFileInfo] = None

    try:
        raw_text = file_path.read_bytes()
        data = json.loads(raw_text.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        warnings.append(f"Cannot parse {file_path.name}: {e}")
        return messages, chat_info, warnings

    chat_info_data = data.get("chatInfo") or {}
    chat_info = ChatFileInfo(
        self_uid=str(chat_info_data.get("selfUid", "")),
        self_uin=str(chat_info_data.get("selfUin", "")),
        self_name=str(chat_info_data.get("selfName", "")),
        chat_name=str(chat_info_data.get("name", "")),
        chat_type=str(chat_info_data.get("type", "private")),
        file_path=str(file_path),
    )

    raw_messages = data.get("messages") or data.get("data") or data.get("list") or []
    if not isinstance(raw_messages, list):
        warnings.append(f"{file_path.name}: 'messages' field is not a list, skipping")
        return messages, chat_info, warnings

    chat_info.message_count = len(raw_messages)

    for raw in raw_messages:
        if not isinstance(raw, dict):
            continue
        try:
            pm = _parse_single_message(raw)
            if pm is not None:
                messages.append(pm)
        except Exception as e:
            warnings.append(f"{file_path.name}: error parsing message: {e}")
            continue

    return messages, chat_info, warnings


def _parse_single_message(raw: Dict[str, Any]) -> Optional[ParsedMessage]:
    """Parse a single raw message dict into a ParsedMessage."""
    is_system = bool(raw.get("system", False))
    is_recalled = bool(raw.get("recalled", False))

    msg_id = str(raw.get("id", raw.get("msgId", raw.get("msg_id", ""))))
    seq = str(raw.get("seq", raw.get("sequence", raw.get("seqId", ""))))

    sender_raw = raw.get("sender") or raw.get("fromUser") or raw.get("from") or {}
    if isinstance(sender_raw, str):
        sender_name = sender_raw
        sender_uid = ""
        sender_uin = ""
        sender_nickname = ""
    elif isinstance(sender_raw, dict):
        sender_name = str(_resolve_field(sender_raw, SENDER_NAME_FIELDS, ""))
        sender_uid = str(sender_raw.get("uid", ""))
        sender_uin = str(_resolve_field(sender_raw, SENDER_ID_FIELDS, ""))
        sender_nickname = str(sender_raw.get("nickname", ""))
    else:
        sender_name = ""
        sender_uid = ""
        sender_uin = ""
        sender_nickname = ""

    ts_val = raw.get("timestamp") or raw.get("time") or raw.get("msgTime") or 0
    ts_ms, dt = _safe_timestamp(ts_val)

    raw_type = str(raw.get("type", "type_1"))
    normalized_type = _normalize_msg_type(raw_type, is_system)

    content_raw = raw.get("content") or raw.get("message") or raw.get("msgContent") or {}
    text = _extract_text_from_content(content_raw)

    if not text:
        text = str(_resolve_field(raw, ("text", "message", "msg", "content"), ""))

    elements = []
    if isinstance(content_raw, dict):
        elements = content_raw.get("elements", [])

    pm = ParsedMessage(
        msg_id=msg_id,
        seq=seq,
        timestamp_ms=ts_ms,
        sender_uid=sender_uid,
        sender_uin=sender_uin,
        sender_name=sender_name,
        sender_nickname=sender_nickname,
        text=text,
        msg_type=normalized_type,
        is_system=is_system,
        is_recalled=is_recalled,
        raw_type=raw_type,
        elements=elements,
    )
    pm.datetime_obj = dt
    return pm


def _normalize_msg_type(raw_type: str, is_system: bool) -> str:
    """Normalize QCE raw type to a simple category."""
    if is_system:
        return "system"
    type_map = {
        "type_1": "text",
        "type_2": "image",
        "type_3": "image",
        "type_4": "file",
        "type_5": "audio",
        "type_6": "video",
        "type_7": "location",
        "type_8": "file",
        "type_9": "forward",
        "type_10": "system",
        "type_11": "reply",
        "type_17": "other",
    }
    return type_map.get(raw_type, "other")


def scan_input_dir(input_dir: Path, limit_files: Optional[int] = None) -> List[Path]:
    """Recursively scan input_dir for .json and .jsonl files."""
    files: List[Path] = []
    for ext in ("*.json", "*.jsonl"):
        files.extend(sorted(input_dir.rglob(ext)))
    if limit_files is not None and limit_files > 0:
        files = files[:limit_files]
    return files


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Load a JSONL file, returning a list of dicts (one per line)."""
    results: List[Dict[str, Any]] = []
    if not path.exists():
        return results
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Skipping invalid JSONL line in %s", path.name)
    return results


def load_all_files(input_dir: Path, limit_files: Optional[int] = None) -> Tuple[List[ParsedMessage], List[Dict[str, Any]], List[str]]:
    """Scan and parse all QCE files in input_dir.

    Returns:
        (all_messages, file_infos, warnings)
    """
    paths = scan_input_dir(input_dir, limit_files)
    all_messages: List[ParsedMessage] = []
    file_infos: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for p in paths:
        msgs, info, warns = parse_qce_json(p)
        all_messages.extend(msgs)
        if info:
            file_infos.append({
                "file": str(p),
                "self_uid": info.self_uid,
                "self_uin": info.self_uin,
                "self_name": info.self_name,
                "chat_name": info.chat_name,
                "chat_type": info.chat_type,
                "message_count": info.message_count,
            })
        warnings.extend(warns)

    return all_messages, file_infos, warnings


def is_self(message: ParsedMessage, me_ids: List[str], me_names: List[str]) -> bool:
    """Check if a message was sent by 'me' based on configured identity."""
    if me_ids and (message.sender_uid in me_ids or message.sender_uin in me_ids):
        return True
    if me_names:
        for name_key in me_names:
            if name_key and name_key.lower() in (
                message.sender_name.lower(),
                message.sender_nickname.lower(),
            ):
                return True
    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import os
    _ai_root = Path(os.environ.get("AI_ROOT", "D:/AI"))
    test_dir = _ai_root / "raw_material/qq/exports/raw/qq-chat-exporter-live"
    msgs, infos, warns = load_all_files(test_dir, limit_files=2)
    print(f"Parsed {len(msgs)} messages from {len(infos)} files")
    print(f"Warnings: {len(warns)}")
    if msgs:
        print(f"First: {msgs[0].sender_name}: {msgs[0].text[:60]}")
        print(f"Last:  {msgs[-1].sender_name}: {msgs[-1].text[:60]}")
