"""ハイライトの日本語要約・分野タグ付けと、週全体のエディターズノート生成.

``client`` が ``None`` の場合(``--dry-run``)は決定的なモック文面を返す。
"""

from __future__ import annotations

import logging
from typing import Any

from .rank import call_claude_json

log = logging.getLogger(__name__)

DEFAULT_ABSTRACT_CHARS = 1500

#: モック時・生成失敗時に使う分野タグ候補(部分一致キーワード → タグ)
FIELD_TAG_RULES: list[tuple[tuple[str, ...], str]] = [
    (
        (
            "music information retrieval",
            "mir ",
            "transcription",
            "beat tracking",
            "source separation",
            "chord",
            "symbolic music",
            "generation",
            "retrieval",
            "audio",
            "neural",
            "model",
        ),
        "音楽情報検索",
    ),
    (
        (
            "therapy",
            "therapeutic",
            "intervention",
            "dementia",
            "rehabilitation",
            "patient",
            "clinical",
        ),
        "音楽療法",
    ),
    (
        (
            "perception",
            "cognition",
            "cognitive",
            "emotion",
            "listener",
            "memory",
            "brain",
            "eeg",
            "fmri",
            "neural correlates",
            "infant",
        ),
        "音楽心理学",
    ),
    (
        (
            "acoustic",
            "vibration",
            "room",
            "resonance",
            "timbre",
            "spectral",
            "loudspeaker",
            "psychoacoustic",
        ),
        "音響学",
    ),
    (
        (
            "ethnograph",
            "fieldwork",
            "indigenous",
            "traditional",
            "ritual",
            "diaspora",
            "vernacular",
            "folk",
        ),
        "民族音楽学",
    ),
    (
        ("education", "classroom", "curriculum", "pedagog", "student", "teacher", "learner"),
        "音楽教育",
    ),
    (
        (
            "performance",
            "performer",
            "ensemble",
            "conductor",
            "practice",
            "expressive timing",
            "musician",
        ),
        "演奏研究",
    ),
    (
        (
            "theory",
            "analysis",
            "counterpoint",
            "tonal",
            "corpus",
            "notation",
            "score",
            "historical",
            "manuscript",
            "archival",
        ),
        "音楽理論・音楽学",
    ),
    (("industry", "streaming", "platform", "copyright", "market", "recommendation"), "音楽と社会"),
]

DEFAULT_FIELD_TAG = "音楽研究一般"

SUMMARY_SYSTEM_PROMPT = """\
あなたは音楽研究のウィークリーダイジェストを編集する日本語のサイエンスライターです。
読者は音楽研究の最新動向を分野横断的に追う研究者・実務家で、必ずしも当該分野の専門家では
ありません。

与えられた論文について、次の JSON を出力してください。

  {"field_tag": "<日本語の分野タグ>", "summary_ja": "<日本語2〜3文の要約>"}

要件:
- summary_ja は日本語で 2〜3 文。「何を対象に」「どうやって」「何が分かったか」が分かるように書く。
- 「本研究は」「〜と報告している」といった紋切り型に頼りすぎず、具体的な対象・手法・結果を入れる。
- アブストラクトに書かれていないことを推測して書かない。
- field_tag は「音楽情報検索」「音楽心理学」「音楽療法」「音響学」「民族音楽学」「音楽教育」
  「演奏研究」「音楽理論・音楽学」のような、10 文字程度までの日本語の分野名。
- 説明文やコードフェンスを付けず、JSON オブジェクトのみを出力する。\
"""

OVERVIEW_SYSTEM_PROMPT = """\
あなたは音楽研究のウィークリーダイジェストの編集長です。
今週のハイライト論文の一覧をもとに、メール冒頭に置く「エディターズノート」を書いてください。

要件:
- 日本語で 3〜4 文。
- 今週どんな話題が目立ったか、分野をまたいでどんな傾向が読み取れるかを述べる。
- 個々の論文の羅列ではなく、全体を俯瞰した文章にする。必要なら 1〜2 本だけ具体的に触れてよい。
- 誇張や無根拠な一般化を避け、落ち着いた編集者の語り口で書く。
- 出力は次の JSON のみ: {"overview": "<エディターズノート本文>"}
  説明文やコードフェンスは付けない。\
"""


def _cfg(config: dict, path: str, default: Any) -> Any:
    node: Any = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node


def guess_field_tag(paper: dict) -> str:
    """タイトル+アブストラクトのキーワードから分野タグを推定する。"""
    text = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
    best_tag = DEFAULT_FIELD_TAG
    best_hits = 0
    for keywords, tag in FIELD_TAG_RULES:
        hits = sum(1 for kw in keywords if kw in text)
        if hits > best_hits:
            best_hits, best_tag = hits, tag
    return best_tag


# --------------------------------------------------------------------------
# モック(--dry-run)
# --------------------------------------------------------------------------


def _mock_summary(paper: dict) -> str:
    title = (paper.get("title") or "").strip().rstrip(".")
    venue = (paper.get("venue") or "").strip()
    where = f"{venue}に掲載された" if venue else ""
    abstract = (paper.get("abstract") or "").strip()
    first_sentence = abstract.split(". ")[0][:120] if abstract else ""
    return (
        f"【ドライラン用のモック要約】{where}「{title}」を取り上げる。"
        f"アブストラクト冒頭は「{first_sentence}」であり、"
        "本番実行では Claude が日本語 2〜3 文の要約に置き換える。"
    )


def _mock_overview(highlights: list[dict], total_count: int) -> str:
    tags: list[str] = []
    for paper in highlights:
        tag = paper.get("field_tag") or DEFAULT_FIELD_TAG
        if tag not in tags:
            tags.append(tag)
    tag_text = "、".join(tags[:4]) if tags else "音楽研究全般"
    return (
        "【ドライラン用のモック概観】今週は "
        f"{total_count} 件の音楽関連論文を収集し、うち {len(highlights)} 件をハイライトとして選びました。"
        f"目立った分野は{tag_text}です。"
        "分野横断で見ると、計算的手法と人を対象とした実証研究の双方から音楽現象に迫る動きが続いています。"
        "本番実行ではこの文面が Claude 生成のエディターズノートに置き換わります。"
    )


# --------------------------------------------------------------------------
# 公開 API
# --------------------------------------------------------------------------

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "field_tag": {"type": "string"},
        "summary_ja": {"type": "string"},
    },
    "required": ["field_tag", "summary_ja"],
    "additionalProperties": False,
}

_OVERVIEW_SCHEMA = {
    "type": "object",
    "properties": {"overview": {"type": "string"}},
    "required": ["overview"],
    "additionalProperties": False,
}


def _summarize_one(paper: dict, config: dict, client: Any) -> tuple[str, str]:
    abstract_chars = int(_cfg(config, "summarize.abstract_chars", DEFAULT_ABSTRACT_CHARS))
    authors = "、".join((paper.get("authors") or [])[:5])
    user = "\n".join(
        [
            f"タイトル: {paper.get('title', '')}",
            f"著者: {authors or '不明'}",
            f"掲載: {paper.get('venue') or '不明'}"
            + ("(プレプリント)" if paper.get("is_preprint") else ""),
            f"公開日: {paper.get('published', '')}",
            "アブストラクト:",
            (paper.get("abstract") or "")[:abstract_chars],
        ]
    )
    payload = call_claude_json(
        client,
        config,
        system=SUMMARY_SYSTEM_PROMPT,
        user=user,
        max_tokens=800,
        schema=_SUMMARY_SCHEMA,
        retries=1,
    )
    summary = str(payload.get("summary_ja") or "").strip()
    tag = str(payload.get("field_tag") or "").strip()
    if not summary:
        raise ValueError("summary_ja が空です")
    return tag or guess_field_tag(paper), summary


def summarize_highlights(papers: list[dict], config: dict, client: Any) -> list[dict]:
    """上位論文に ``summary_ja`` と ``field_tag`` を付与して返す(順序は維持)。"""
    if not papers:
        return []

    result: list[dict] = []
    for index, original in enumerate(papers, start=1):
        paper = dict(original)
        if client is None:
            paper["field_tag"] = guess_field_tag(paper)
            paper["summary_ja"] = _mock_summary(paper)
        else:
            try:
                tag, summary = _summarize_one(paper, config, client)
                paper["field_tag"] = tag
                paper["summary_ja"] = summary
            except Exception as exc:  # noqa: BLE001 - 1 件の失敗で全体を落とさない
                log.error("要約に失敗しました (%s): %s", paper.get("id"), exc)
                paper["field_tag"] = guess_field_tag(paper)
                paper["summary_ja"] = (
                    "(自動要約を生成できませんでした。原文のアブストラクトをご参照ください。)"
                )
            log.info("要約: %d/%d 件完了", index, len(papers))
        result.append(paper)

    if client is None:
        log.info("要約(モック): %d 件", len(result))
    return result


def write_overview(highlights: list[dict], total_count: int, config: dict, client: Any) -> str:
    """週全体のエディターズノート(日本語 3〜4 文)を返す。"""
    if client is None:
        return _mock_overview(highlights, total_count)

    lines = [
        f"今週収集した音楽関連論文は全部で {total_count} 件、"
        f"うちハイライトは {len(highlights)} 件です。",
        "",
        "ハイライト一覧:",
    ]
    for paper in highlights:
        lines.append(f"- [{paper.get('field_tag') or '分野不明'}] {paper.get('title', '')}")
        summary = (paper.get("summary_ja") or "").strip()
        if summary:
            lines.append(f"  {summary}")

    try:
        payload = call_claude_json(
            client,
            config,
            system=OVERVIEW_SYSTEM_PROMPT,
            user="\n".join(lines),
            max_tokens=1200,
            schema=_OVERVIEW_SCHEMA,
            retries=1,
        )
        overview = str(payload.get("overview") or "").strip()
        if overview:
            return overview
        raise ValueError("overview が空です")
    except Exception as exc:  # noqa: BLE001
        log.error("エディターズノートの生成に失敗しました: %s", exc)
        return (
            f"今週は {total_count} 件の音楽関連論文を収集し、"
            f"うち {len(highlights)} 件をハイライトとして紹介します。"
            "(概観の自動生成に失敗したため、各論文の要約をご覧ください。)"
        )
