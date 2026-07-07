"""
block_builder.py — Build turns, sessions, and my_blocks from parsed messages.

Pipeline:
  messages (sorted by time)
    → split into sessions (gap > session_gap_minutes)
      → merge into turns (same sender, gap < turn_gap_seconds)
        → extract my_blocks (my turns + light interruptions)

A "my_block" is a continuous region dominated by "me" that may include
short interruptions from others. It represents one coherent expression.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from qq_raw_filter.qce_parser import ParsedMessage, is_self

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Turn:
    """A contiguous speech segment by one speaker."""

    __slots__ = (
        "speaker_uid", "speaker_uin", "speaker_name",
        "speaker_type",  # "me" | "other"
        "text", "fragments", "char_count",
        "time_start", "time_end",
        "timestamp_ms_start", "timestamp_ms_end",
        "is_system", "msg_type",
    )

    def __init__(
        self,
        speaker_uid: str = "",
        speaker_uin: str = "",
        speaker_name: str = "",
        speaker_type: str = "other",
        text: str = "",
        fragments: Optional[List[str]] = None,
        char_count: int = 0,
        time_start: str = "",
        time_end: str = "",
        timestamp_ms_start: int = 0,
        timestamp_ms_end: int = 0,
        is_system: bool = False,
        msg_type: str = "text",
    ):
        self.speaker_uid = speaker_uid
        self.speaker_uin = speaker_uin
        self.speaker_name = speaker_name
        self.speaker_type = speaker_type
        self.text = text
        self.fragments = fragments or []
        self.char_count = char_count
        self.time_start = time_start
        self.time_end = time_end
        self.timestamp_ms_start = timestamp_ms_start
        self.timestamp_ms_end = timestamp_ms_end
        self.is_system = is_system
        self.msg_type = msg_type

    def to_dict(self) -> Dict[str, Any]:
        return {
            "speaker_type": self.speaker_type,
            "speaker_name": self.speaker_name,
            "text": self.text,
            "char_count": self.char_count,
            "time_start": self.time_start,
            "time_end": self.time_end,
        }


class Session:
    """A conversation session separated by a long gap."""

    __slots__ = ("session_id", "turns", "start_time", "end_time", "source_file")

    def __init__(
        self,
        session_id: str = "",
        turns: Optional[List[Turn]] = None,
        start_time: str = "",
        end_time: str = "",
        source_file: str = "",
    ):
        self.session_id = session_id
        self.turns = turns or []
        self.start_time = start_time
        self.end_time = end_time
        self.source_file = source_file


class MyBlock:
    """A continuous region dominated by 'me' with possible light interruptions."""

    __slots__ = (
        "block_id", "session_id",
        "my_turns", "other_turns",
        "my_text", "context", "fragments",
        "metrics", "scores", "reasons",
        "bucket", "source_file", "style_tags",
    )

    def __init__(self, block_id: str = "", session_id: str = "", source_file: str = ""):
        self.block_id = block_id
        self.session_id = session_id
        self.my_turns: List[Turn] = []
        self.other_turns: List[Turn] = []
        self.my_text = ""
        self.context: List[Turn] = []
        self.fragments: List[str] = []
        self.metrics: Dict[str, Any] = {}
        self.scores: Dict[str, float] = {}
        self.reasons: List[str] = []
        self.bucket = ""
        self.style_tags: List[str] = []
        self.source_file = source_file

    @staticmethod
    def _turn_to_dict(t: Any) -> Dict[str, Any]:
        if isinstance(t, dict):
            return t
        if hasattr(t, 'to_dict'):
            return t.to_dict()
        return {"text": str(t)}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "bucket": self.bucket,
            "decision": "keep" if self.bucket not in ("rejected",) else "reject",
            "source_file": self.source_file,
            "session_id": self.session_id,
            "my_text": self.my_text,
            "context": [self._turn_to_dict(t) for t in self.context],
            "fragments": self.fragments,
            "turns": [self._turn_to_dict(t) for t in self.my_turns + self.other_turns],
            "metrics": self.metrics,
            "scores": self.scores,
            "reasons": self.reasons,
            "style_tags": self.style_tags,
        }


# ---------------------------------------------------------------------------
# Message → Turn merging
# ---------------------------------------------------------------------------

def messages_to_turns(
    messages: List[ParsedMessage],
    me_ids: List[str],
    me_names: List[str],
    turn_gap_seconds: int = 90,
) -> Tuple[List[Turn], List[str]]:
    """Merge consecutive messages from the same speaker into turns.

    Returns:
        (turns, warnings)
    """
    warnings: List[str] = []
    if not messages:
        return [], warnings

    sorted_msgs = sorted(messages, key=lambda m: m.timestamp_ms)
    turns: List[Turn] = []
    current: Optional[Turn] = None

    for msg in sorted_msgs:
        if msg.is_recalled:
            continue

        speaker_type = "me" if is_self(msg, me_ids, me_names) else "other"
        text = msg.text.strip()

        if current is not None:
            gap = msg.timestamp_ms - current.timestamp_ms_end
            same_speaker = (
                current.speaker_type == speaker_type
                or (current.speaker_uid and msg.sender_uid
                    and current.speaker_uid == msg.sender_uid)
            )
            if same_speaker and (turn_gap_seconds <= 0 or gap < turn_gap_seconds * 1000):
                _extend_turn(current, msg, text)
                continue

        t = Turn(
            speaker_uid=msg.sender_uid,
            speaker_uin=msg.sender_uin,
            speaker_name=msg.sender_name or msg.sender_nickname,
            speaker_type=speaker_type,
            text=text,
            fragments=[text] if text else [],
            char_count=len(text),
            time_start=msg.datetime_obj.strftime("%Y-%m-%d %H:%M:%S") if msg.datetime_obj else "",
            time_end=msg.datetime_obj.strftime("%Y-%m-%d %H:%M:%S") if msg.datetime_obj else "",
            timestamp_ms_start=msg.timestamp_ms,
            timestamp_ms_end=msg.timestamp_ms,
            is_system=msg.is_system,
            msg_type=msg.msg_type,
        )
        turns.append(t)
        current = t

    return turns, warnings


def _extend_turn(turn: Turn, msg: ParsedMessage, text: str) -> None:
    """Append a message to an existing turn."""
    if text:
        if turn.text:
            turn.text += "\n" + text
        else:
            turn.text = text
        turn.fragments.append(text)
        turn.char_count += len(text)
    turn.time_end = msg.datetime_obj.strftime("%Y-%m-%d %H:%M:%S") if msg.datetime_obj else turn.time_end
    turn.timestamp_ms_end = msg.timestamp_ms


# ---------------------------------------------------------------------------
# Turn → Session splitting
# ---------------------------------------------------------------------------

def turns_to_sessions(
    turns: List[Turn],
    source_file: str,
    session_gap_minutes: int = 20,
) -> List[Session]:
    """Split turns into sessions based on time gaps."""
    if not turns:
        return []

    sessions: List[Session] = []
    current_turns: List[Turn] = [turns[0]]

    for i in range(1, len(turns)):
        gap = turns[i].timestamp_ms_start - turns[i - 1].timestamp_ms_end
        if gap > session_gap_minutes * 60 * 1000:
            sessions.append(Session(
                turns=list(current_turns),
                start_time=current_turns[0].time_start,
                end_time=current_turns[-1].time_end,
                source_file=source_file,
            ))
            current_turns = []
        current_turns.append(turns[i])

    if current_turns:
        sessions.append(Session(
            turns=list(current_turns),
            start_time=current_turns[0].time_start,
            end_time=current_turns[-1].time_end,
            source_file=source_file,
        ))

    return sessions


# ---------------------------------------------------------------------------
# Session → MyBlock extraction
# ---------------------------------------------------------------------------

def extract_my_blocks(
    sessions: List[Session],
    config: Dict[str, Any],
) -> Tuple[List[MyBlock], List[str]]:
    """Extract my_blocks from sessions using interruption rules.

    Config keys used:
      my_block.min_my_block_chars
      my_block.min_my_msg_count
      my_block.max_block_chars
      my_block.max_fragment_count
      my_block.short_fragment_chars
      my_block.min_my_char_ratio
      interruption.light_interruption_chars
      interruption.hard_interruption_chars
      interruption.max_light_interruptions
      time.my_block_continue_seconds
    """
    warnings: List[str] = []
    blocks: List[MyBlock] = []

    mb_cfg = config.get("my_block", {})
    int_cfg = config.get("interruption", {})
    time_cfg = config.get("time", {})

    min_chars = mb_cfg.get("min_my_block_chars", 50)
    min_msg_count = mb_cfg.get("min_my_msg_count", 2)
    max_block_chars = mb_cfg.get("max_block_chars", 1200)
    max_fragment_count = mb_cfg.get("max_fragment_count", 120)
    short_fragment_chars = mb_cfg.get("short_fragment_chars", 3)
    min_my_ratio = mb_cfg.get("min_my_char_ratio", 0.50)

    light_chars = int_cfg.get("light_interruption_chars", 30)
    hard_chars = int_cfg.get("hard_interruption_chars", 100)
    max_light_int = int_cfg.get("max_light_interruptions", 5)
    continue_secs = time_cfg.get("my_block_continue_seconds", 180)

    for sess_idx, session in enumerate(sessions):
        session_id = f"session_{sess_idx:05d}"
        turns = session.turns

        i = 0
        block_counter = 0
        while i < len(turns):
            turn = turns[i]
            if turn.speaker_type != "me" or turn.is_system:
                i += 1
                continue

            block = MyBlock(
                block_id=f"{session_id}_block_{block_counter:03d}",
                session_id=session_id,
                source_file=session.source_file,
            )
            block.my_turns.append(turn)
            current_my_chars = turn.char_count
            total_chars = turn.char_count
            light_int_count = 0
            fragments = list(turn.fragments)
            j = i + 1

            while j < len(turns):
                next_turn = turns[j]

                if next_turn.speaker_type == "me":
                    gap = next_turn.timestamp_ms_start - turns[j - 1].timestamp_ms_end
                    if gap > continue_secs * 1000:
                        break
                    block.my_turns.append(next_turn)
                    current_my_chars += next_turn.char_count
                    total_chars += next_turn.char_count
                    fragments.extend(next_turn.fragments)
                    j += 1
                    continue

                other_chars = next_turn.char_count
                is_light = other_chars <= light_chars
                is_hard = other_chars >= hard_chars

                if is_hard:
                    break

                if is_light and light_int_count < max_light_int:
                    block.other_turns.append(next_turn)
                    total_chars += other_chars
                    fragments.extend(next_turn.fragments)
                    light_int_count += 1
                    j += 1
                    continue

                # Medium interruption: check if I continue soon
                gap = next_turn.timestamp_ms_start - turns[j - 1].timestamp_ms_end
                if gap <= continue_secs * 1000:
                    look_ahead = j + 1
                    while look_ahead < len(turns) and look_ahead < j + 5:
                        if turns[look_ahead].speaker_type == "me":
                            gap_after = turns[look_ahead].timestamp_ms_start - next_turn.timestamp_ms_end
                            if gap_after <= continue_secs * 1000:
                                block.other_turns.append(next_turn)
                                total_chars += other_chars
                                fragments.extend(next_turn.fragments)
                                j += 1
                                break
                        look_ahead += 1
                    else:
                        break
                else:
                    break

                if len(fragments) > max_fragment_count:
                    warnings.append(f"{block.block_id}: fragment count > {max_fragment_count}")
                    break
                if total_chars > max_block_chars:
                    break

            # Finalize
            block.fragments = fragments
            block.my_text = "\n".join(t.text for t in block.my_turns if t.text)

            ctx_start = max(0, i - 3)
            ctx_end = min(len(turns), j + 3)
            block.context = turns[ctx_start:ctx_end]

            total_my_frags = sum(len(t.fragments) for t in block.my_turns)
            short_frags = sum(1 for t in block.my_turns
                              for f in t.fragments if len(f) <= short_fragment_chars)
            my_ratio = current_my_chars / total_chars if total_chars > 0 else 0

            block.metrics = {
                "my_msg_count": total_my_frags,
                "my_char_count": current_my_chars,
                "total_char_count": total_chars,
                "my_char_ratio": round(my_ratio, 4),
                "my_turn_count": len(block.my_turns),
                "other_turn_count": len(block.other_turns),
                "interruption_count": light_int_count,
                "interruption_chars": sum(t.char_count for t in block.other_turns),
                "short_fragment_count": short_frags,
                "short_fragment_ratio": round(
                    short_frags / total_my_frags if total_my_frags > 0 else 0, 4
                ),
            }

            if (current_my_chars >= min_chars
                    and total_my_frags >= min_msg_count
                    and my_ratio >= min_my_ratio):
                blocks.append(block)
                block_counter += 1
            else:
                # Sub-threshold blocks enter 'rejected' with specific reasons
                # instead of being silently dropped. This makes rejection
                # audit-visible in stats.json and rejected.jsonl.
                sub_reasons = []
                if current_my_chars < min_chars:
                    sub_reasons.append(
                        f"too_few_chars:{current_my_chars}<{min_chars}"
                    )
                if total_my_frags < min_msg_count:
                    sub_reasons.append(
                        f"too_few_msgs:{total_my_frags}<{min_msg_count}"
                    )
                if my_ratio < min_my_ratio:
                    sub_reasons.append(
                        f"low_my_ratio:{my_ratio:.2f}<{min_my_ratio}"
                    )
                block.reasons = sub_reasons
                block.bucket = "rejected"
                blocks.append(block)
                block_counter += 1
                warnings.append(
                    f"{block.block_id}: sub-threshold ({' | '.join(sub_reasons)})"
                )

            i = j

    return blocks, warnings


# ---------------------------------------------------------------------------
# High-level pipeline
# ---------------------------------------------------------------------------

def build_blocks_from_messages(
    messages: List[ParsedMessage],
    source_file: str,
    config: Dict[str, Any],
    me_ids: List[str],
    me_names: List[str],
) -> Tuple[List[MyBlock], List[str], List[str]]:
    """Full pipeline: messages → turns → sessions → my_blocks.

    Returns:
        (my_blocks, session_warnings, block_warnings)
    """
    all_warnings: List[str] = []

    turns, turn_warns = messages_to_turns(
        messages, me_ids, me_names,
        turn_gap_seconds=config.get("time", {}).get("turn_gap_seconds", 90),
    )
    all_warnings.extend(turn_warns)

    sessions = turns_to_sessions(
        turns, source_file,
        session_gap_minutes=config.get("time", {}).get("session_gap_minutes", 20),
    )

    blocks, block_warns = extract_my_blocks(sessions, config)
    all_warnings.extend(block_warns)

    return blocks, [s.source_file for s in sessions], all_warnings
