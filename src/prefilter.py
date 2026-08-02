"""ルールベースの事前フィルタ.

Claude に投げる前に、明らかに読む価値のないレコードを落とす:

* アブストラクトが無い / 極端に短い
* 書評・エラータ・社説など非研究タイプ(OpenAlex の ``type`` フィールド)
* 撤回済み(``is_retracted``)

通過したレコードからは fetch が付けた内部キー(``_type`` / ``_retracted``)を
取り除き、INTERFACES.md の Paper レコード形式そのものにして返す。
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

#: OpenAlex の type のうち研究論文として扱わないもの
DEFAULT_EXCLUDED_TYPES = [
    "editorial",
    "erratum",
    "retraction",
    "paratext",
    "peer-review",
    "letter",
    "grant",
    "supplementary-materials",
    "reference-entry",
    "libguides",
    "standard",
    "dataset",
]

#: タイトルから非研究記事を判定する正規表現(大文字小文字は無視)
DEFAULT_EXCLUDED_TITLE_PATTERNS = [
    r"^\s*(book\s+)?review\s+of\b",
    r"^\s*book\s+review",
    r"^\s*(record|album|concert|performance)\s+review\b",
    r"^\s*erratum\b",
    r"^\s*corrigendum\b",
    r"^\s*correction\s+to\b",
    r"^\s*retraction\b",
    r"^\s*retracted\b",
    r"^\s*editorial\b",
    r"^\s*(in\s+)?memoriam\b",
    r"^\s*obituary\b",
    r"^\s*(table\s+of\s+)?contents\b",
    r"^\s*front\s+matter\b",
    r"^\s*back\s+matter\b",
    r"^\s*call\s+for\s+papers\b",
    r"^\s*index\s*$",
]

#: Paper レコードから取り除く内部キー
INTERNAL_KEYS = ("_type", "_retracted")

DEFAULT_MIN_ABSTRACT_CHARS = 120


def _cfg(config: dict, path: str, default: Any) -> Any:
    node: Any = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node


def _compile_title_patterns(config: dict) -> list[re.Pattern[str]]:
    raw = _cfg(config, "prefilter.excluded_title_patterns", DEFAULT_EXCLUDED_TITLE_PATTERNS)
    patterns: list[re.Pattern[str]] = []
    for item in raw:
        try:
            patterns.append(re.compile(str(item), re.IGNORECASE))
        except re.error as exc:
            log.warning("prefilter: 正規表現を無視します %r (%s)", item, exc)
    return patterns


def _strip_internal(paper: dict) -> dict:
    return {k: v for k, v in paper.items() if k not in INTERNAL_KEYS}


def prefilter(papers: list[dict], config: dict) -> list[dict]:
    """非研究レコード・アブスト無し・撤回済みを除外した Paper リストを返す。"""
    min_chars = int(_cfg(config, "prefilter.min_abstract_chars", DEFAULT_MIN_ABSTRACT_CHARS))
    excluded_types = {
        str(t).strip().lower()
        for t in _cfg(config, "prefilter.excluded_types", DEFAULT_EXCLUDED_TYPES)
    }
    title_patterns = _compile_title_patterns(config)

    kept: list[dict] = []
    reasons: dict[str, int] = {}

    def drop(reason: str, paper: dict) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1
        log.debug("除外(%s): %s / %s", reason, paper.get("id"), paper.get("title"))

    for paper in papers:
        title = (paper.get("title") or "").strip()
        if not title:
            drop("タイトル無し", paper)
            continue

        if paper.get("_retracted"):
            drop("撤回済み", paper)
            continue

        paper_type = str(paper.get("_type") or "").strip().lower()
        if paper_type and paper_type in excluded_types:
            drop(f"非研究タイプ({paper_type})", paper)
            continue

        if any(pattern.search(title) for pattern in title_patterns):
            drop("タイトルが非研究記事", paper)
            continue

        abstract = (paper.get("abstract") or "").strip()
        if not abstract:
            drop("アブストラクト無し", paper)
            continue
        if len(abstract) < min_chars:
            drop(f"アブストラクトが短い(<{min_chars}字)", paper)
            continue

        cleaned = _strip_internal(paper)
        cleaned["abstract"] = abstract
        cleaned["title"] = title
        kept.append(cleaned)

    if reasons:
        detail = " / ".join(f"{k}: {v}" for k, v in sorted(reasons.items()))
        log.info("事前フィルタで %d 件を除外 (%s)", sum(reasons.values()), detail)
    log.info("事前フィルタ通過: %d 件 / %d 件", len(kept), len(papers))
    return kept
