from __future__ import annotations

from typing import Optional, Tuple


def parse_end_epoch_from_slug(slug: str) -> Optional[int]:
    if not slug:
        return None
    parts = slug.split("-")
    if not parts:
        return None
    tail = parts[-1]
    if not tail.isdigit():
        return None
    try:
        value = int(tail)
    except ValueError:
        return None
    if value <= 0:
        return None
    if value > 1_000_000_000_000:
        return int(value / 1000)
    return value


def window_start_end_ms(slug: str, window_secs: int = 900) -> Optional[Tuple[int, int]]:
    end_sec = parse_end_epoch_from_slug(slug)
    if end_sec is None:
        return None
    end_ms = end_sec * 1000
    start_ms = end_ms - window_secs * 1000
    return start_ms, end_ms
