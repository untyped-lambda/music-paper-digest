"""既読論文DB(data/sent_ids.json)の読み書き.

形式::

    {"sent": {"<paper id>": "YYYY-MM-DD"}}

保存時に 26 週(182 日)より古いエントリを削除する。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

#: 既読情報を保持する期間(週)
RETENTION_WEEKS = 26
RETENTION_DAYS = RETENTION_WEEKS * 7


def _today() -> date:
    """JST の「今日」。"""
    return datetime.now(JST).date()


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except TypeError, ValueError:
        return None


def _load_raw(path: str | os.PathLike[str]) -> dict[str, str]:
    """{id: "YYYY-MM-DD"} を返す。ファイルが無い/壊れている場合は空 dict。"""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("既読DBを読めなかったため空として扱います (%s): %s", p, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("既読DBの形式が不正です (%s)", p)
        return {}
    sent = data.get("sent")
    if not isinstance(sent, dict):
        return {}
    return {str(k): str(v) for k, v in sent.items()}


def load_sent_ids(path: str) -> set[str]:
    """送信済み論文 ID の集合を返す。"""
    ids = set(_load_raw(path))
    log.info("既読DB: %d 件を読み込みました (%s)", len(ids), path)
    return ids


def mark_sent(path: str, ids: list[str]) -> None:
    """``ids`` を今日の日付で追記し、26 週より古いエントリを削除して保存する。"""
    sent = _load_raw(path)
    today = _today()
    stamp = today.isoformat()

    added = 0
    for paper_id in ids:
        if not paper_id:
            continue
        key = str(paper_id)
        if key not in sent:
            added += 1
        sent[key] = stamp

    cutoff = today - timedelta(days=RETENTION_DAYS)
    kept: dict[str, str] = {}
    dropped = 0
    for key, value in sent.items():
        recorded = _parse_date(value)
        if recorded is None or recorded < cutoff:
            dropped += 1
            continue
        kept[key] = value

    # id 順に並べておくと差分コミットが読みやすい
    payload = {"sent": dict(sorted(kept.items()))}

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 途中で落ちても既存DBを壊さないよう一時ファイル経由で置き換える
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), prefix=p.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, p)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    log.info(
        "既読DBを保存しました: 新規 %d 件 / 保持 %d 件 / 期限切れ削除 %d 件 (%s)",
        added,
        len(kept),
        dropped,
        p,
    )
