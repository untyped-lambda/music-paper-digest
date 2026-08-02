"""
音楽論文ウィークリーダイジェスト - メールHTML生成モジュール (Agent B 担当)

INTERFACES.md の契約:
    def render_digest(
        overview: str,
        highlights: list[dict],
        others: list[dict],
        week_start: str,
        week_end: str,
        config: dict,
    ) -> RenderResult

構造は3層:
    1. エディターズノート (概観)
    2. ハイライト (field_tag で分野別グルーピング、要約付き)
    3. その他タイトル一覧 (原題 + ジャーナル名、リンク付き。others_max_in_body 件まで)

本文 HTML は 100KB (既定。config["max_body_kb"] で上書き可) 以内に収める。
超過分・others_max_in_body を超えた分は attachment_html (完全な溢れ分リスト) に回す。

メールクライアント互換性のため、<style> ブロックは使わずインライン style 属性のみを使用し、
テーブルレイアウトは使わない。背景色を強制しないことでダークモード表示でも破綻しないように
配慮している (文字色・リンク色・区切り線の色のみを指定)。
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime

DEFAULT_MAX_BODY_KB = 100

# 配色 (背景は指定しない = クライアント/ダークモードのデフォルトに委ねる)
COLOR_TEXT = "#3a3a3a"
COLOR_SUBTEXT = "#767676"
COLOR_MUTED = "#9a9a9a"
COLOR_LINK = "#3366cc"
COLOR_HEADING = "#2a2a2a"
COLOR_DIVIDER = "#d0d0d0"

FONT_STACK = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, "
    "'Hiragino Kaku Gothic ProN', 'Noto Sans JP', sans-serif"
)


@dataclass
class RenderResult:
    subject: str
    html_body: str
    attachment_html: str | None
    attachment_filename: str


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _format_week_label(week_start: str, week_end: str) -> str:
    try:
        start = datetime.strptime(week_start, "%Y-%m-%d")
        end = datetime.strptime(week_end, "%Y-%m-%d")
        start_label = f"{start.month}/{start.day}"
        end_label = f"{end.month}/{end.day}"
        return f"{start_label}–{end_label}"  # en dash
    except ValueError:
        return f"{week_start}–{week_end}"


def _format_authors(authors: list[str]) -> str:
    if not authors:
        return ""
    shown = authors[:5]
    text = ", ".join(_esc(a) for a in shown)
    if len(authors) > 5:
        text += " ほか"
    return text


def _paper_meta_line(paper: dict) -> str:
    authors = _format_authors(paper.get("authors") or [])
    venue = _esc(paper.get("venue") or "")
    parts = [p for p in (authors, venue) if p]
    return " ・ ".join(parts)


def _group_highlights_by_field(highlights: list[dict]) -> list[tuple[str, list[dict]]]:
    """field_tag で分野別にグルーピング。最初に出現した順序を維持する(score降順を保つ)。"""
    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for paper in highlights:
        tag = (paper.get("field_tag") or "未分類").strip() or "未分類"
        if tag not in groups:
            groups[tag] = []
            order.append(tag)
        groups[tag].append(paper)
    return [(tag, groups[tag]) for tag in order]


def _render_highlight_paper(paper: dict) -> str:
    title = _esc(paper.get("title") or "(タイトル不明)")
    url = _esc(paper.get("url") or "")
    meta = _paper_meta_line(paper)
    summary = _esc(paper.get("summary_ja") or "")

    title_html = (
        f'<a href="{url}" style="color:{COLOR_LINK};text-decoration:none;font-weight:bold;">{title}</a>'
        if url
        else f'<span style="font-weight:bold;">{title}</span>'
    )
    meta_html = (
        f'<p style="margin:2px 0 6px 0;font-size:12px;color:{COLOR_SUBTEXT};">{meta}</p>'
        if meta
        else ""
    )
    summary_html = (
        f'<p style="margin:0;font-size:13px;line-height:1.6;color:{COLOR_TEXT};">{summary}</p>'
        if summary
        else ""
    )
    return (
        f'<div style="margin:0 0 16px 0;padding:0 0 16px 0;'
        f'border-bottom:1px solid {COLOR_DIVIDER};">'
        f'<p style="margin:0 0 2px 0;font-size:15px;line-height:1.4;">{title_html}</p>'
        f"{meta_html}{summary_html}"
        f"</div>"
    )


def _render_other_line(paper: dict) -> str:
    title = _esc(paper.get("title") or "(タイトル不明)")
    url = _esc(paper.get("url") or "")
    venue = _esc(paper.get("venue") or "")
    title_html = (
        f'<a href="{url}" style="color:{COLOR_LINK};text-decoration:none;">{title}</a>'
        if url
        else title
    )
    suffix = f" — {venue}" if venue else ""
    return (
        f'<li style="margin:0 0 6px 0;font-size:12.5px;line-height:1.5;color:{COLOR_TEXT};">'
        f"{title_html}{suffix}</li>"
    )


def _build_html(
    *,
    subject: str,
    overview: str,
    week_label: str,
    highlights_shown: list[dict],
    others_shown: list[dict],
    others_total: int,
    overflow_count: int,
    attachment_filename: str,
) -> str:
    overview_esc = _esc(overview).replace("\n", "<br>")

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="ja">')
    parts.append(f'<head><meta charset="utf-8"><title>{_esc(subject)}</title></head>')
    parts.append(
        f'<body style="margin:0;padding:0;background:transparent;">'
        f'<div style="max-width:680px;margin:0 auto;padding:20px 16px;'
        f'font-family:{FONT_STACK};color:{COLOR_TEXT};">'
    )

    # ヘッダー
    parts.append(
        f'<h1 style="font-size:19px;line-height:1.4;margin:0 0 4px 0;color:{COLOR_HEADING};">'
        f"{_esc(subject)}</h1>"
    )
    parts.append(
        f'<p style="font-size:12px;margin:0 0 18px 0;color:{COLOR_MUTED};">対象期間: {_esc(week_label)}</p>'
    )

    # 1. エディターズノート
    if overview:
        parts.append(
            f'<div style="margin:0 0 22px 0;padding:0 0 18px 0;'
            f'border-bottom:1px solid {COLOR_DIVIDER};">'
            f'<p style="margin:0;font-size:14px;line-height:1.7;color:{COLOR_TEXT};">{overview_esc}</p>'
            f"</div>"
        )

    # 2. ハイライト
    if highlights_shown:
        parts.append(
            f'<h2 style="font-size:16px;margin:0 0 12px 0;color:{COLOR_HEADING};">ハイライト</h2>'
        )
        for tag, papers in _group_highlights_by_field(highlights_shown):
            parts.append(
                f'<h3 style="font-size:13.5px;margin:0 0 10px 0;color:{COLOR_LINK};">'
                f"{_esc(tag)}</h3>"
            )
            for paper in papers:
                parts.append(_render_highlight_paper(paper))

    # 3. その他タイトル一覧
    if others_shown or others_total:
        parts.append(
            f'<h2 style="font-size:16px;margin:22px 0 10px 0;color:{COLOR_HEADING};">'
            f"その他の論文"
            f'<span style="font-size:12px;font-weight:normal;color:{COLOR_MUTED};">'
            f" (全{others_total}件中{len(others_shown)}件を掲載)</span></h2>"
        )
        if others_shown:
            parts.append('<ul style="margin:0;padding-left:18px;">')
            for paper in others_shown:
                parts.append(_render_other_line(paper))
            parts.append("</ul>")

    if overflow_count > 0:
        parts.append(
            f'<p style="margin:16px 0 0 0;font-size:12px;color:{COLOR_SUBTEXT};">'
            f"掲載しきれなかった{overflow_count}件は添付ファイル "
            f"「{_esc(attachment_filename)}」に完全版として同梱しています。</p>"
        )

    parts.append(
        f'<p style="margin:26px 0 0 0;font-size:11px;color:{COLOR_MUTED};">'
        f"本メールは自動生成されています。</p>"
    )

    parts.append("</div></body></html>")
    return "".join(parts)


def _render_attachment_line(paper: dict) -> str:
    return _render_other_line(paper)


def _build_attachment_html(subject: str, week_label: str, items: list[dict]) -> str:
    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="ja">')
    parts.append(
        f'<head><meta charset="utf-8"><title>{_esc(subject)} (完全版・溢れ分)</title></head>'
    )
    parts.append(
        f'<body style="margin:0;padding:0;">'
        f'<div style="max-width:680px;margin:0 auto;padding:20px 16px;'
        f'font-family:{FONT_STACK};color:{COLOR_TEXT};">'
    )
    parts.append(
        f'<h1 style="font-size:16px;margin:0 0 4px 0;color:{COLOR_HEADING};">'
        f"{_esc(subject)} — 本文に掲載しきれなかった論文一覧</h1>"
    )
    parts.append(
        f'<p style="font-size:12px;margin:0 0 16px 0;color:{COLOR_MUTED};">対象期間: {_esc(week_label)} '
        f"／ 全{len(items)}件</p>"
    )
    parts.append('<ul style="margin:0;padding-left:18px;">')
    for paper in items:
        parts.append(_render_attachment_line(paper))
    parts.append("</ul>")
    parts.append("</div></body></html>")
    return "".join(parts)


def _body_size(html_str: str) -> int:
    return len(html_str.encode("utf-8"))


def render_digest(
    overview: str,
    highlights: list[dict],
    others: list[dict],
    week_start: str,
    week_end: str,
    config: dict,
) -> RenderResult:
    week_label = _format_week_label(week_start, week_end)
    subject = f"{config.get('subject_prefix', '音楽論文ウィークリー')} {week_label}"
    attachment_filename = f"digest-full-{week_end}.html"

    max_body_bytes = int(config.get("max_body_kb", DEFAULT_MAX_BODY_KB)) * 1024
    others_max_in_body = int(config.get("others_max_in_body", 50))

    others_cap = min(len(others), others_max_in_body)

    def build(highlights_subset: list[dict], others_count: int) -> str:
        return _build_html(
            subject=subject,
            overview=overview,
            week_label=week_label,
            highlights_shown=highlights_subset,
            others_shown=others[:others_count],
            others_total=len(others),
            overflow_count=(len(highlights) - len(highlights_subset))
            + (len(others) - others_count),
            attachment_filename=attachment_filename,
        )

    # 第1段階: highlights は全件表示のまま、others の掲載件数を二分探索で決める。
    lo, hi = 0, others_cap
    best_count = -1
    best_body = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        body = build(highlights, mid)
        if _body_size(body) <= max_body_bytes:
            best_count = mid
            best_body = body
            lo = mid + 1
        else:
            hi = mid - 1

    if best_count >= 0:
        # others の掲載件数だけで 100KB に収まった
        overflow_highlights: list[dict] = []
        others_shown_count = best_count
        html_body = best_body
    else:
        # others を 0 件にしても収まらない = highlights 自体が大きすぎる。
        # highlights の掲載件数についても二分探索で削る (フォールバック)。
        lo2, hi2 = 0, len(highlights)
        best2 = 0
        best_body2 = build([], 0)
        while lo2 <= hi2:
            mid2 = (lo2 + hi2) // 2
            body = build(highlights[:mid2], 0)
            if _body_size(body) <= max_body_bytes:
                best2 = mid2
                best_body2 = body
                lo2 = mid2 + 1
            else:
                hi2 = mid2 - 1
        overflow_highlights = highlights[best2:]
        others_shown_count = 0
        html_body = best_body2

    overflow_others = others[others_shown_count:]
    overflow_items = overflow_highlights + overflow_others

    if overflow_items:
        attachment_html = _build_attachment_html(subject, week_label, overflow_items)
    else:
        attachment_html = None

    return RenderResult(
        subject=subject,
        html_body=html_body,
        attachment_html=attachment_html,
        attachment_filename=attachment_filename,
    )
