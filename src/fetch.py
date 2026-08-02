"""OpenAlex + arXiv からの音楽関連論文の収集・正規化・重複排除.

出力は INTERFACES.md の Paper レコード形式(``score`` 等は後段で付与)。
本モジュールは後段のフィルタ用に ``_type`` / ``_retracted`` の内部キーを
付与する(prefilter がこれらを取り除く)。
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
ARXIV_API_URL = "https://export.arxiv.org/api/query"

ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "ar": "http://arxiv.org/schemas/atom",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
}

USER_AGENT = "music-paper-digest/1.0 (+https://github.com/; mailto:{mailto})"

#: OpenAlex の音楽関連トピック(分野横断)。config で上書き可能。
DEFAULT_OPENALEX_TOPIC_IDS = [
    "T10788",  # Neuroscience and Music Perception
    "T11309",  # Music and Audio Processing
    "T11768",  # Music Therapy and Health
    "T11349",  # Music Technology and Sound Studies
    "T13327",  # Arts, Culture, and Music Studies
    "T14287",  # Music Education and Analysis
    "T14034",  # Musicians' Health and Performance
]
#: OpenAlex の subfield「Music」。musicology / 音楽教育 / 音楽史などを広く拾う。
DEFAULT_OPENALEX_SUBFIELD_IDS = ["1210"]

_ARXIV_ID_RE = re.compile(r"arxiv\.org/abs/(?P<id>.+?)(?:v\d+)?$", re.IGNORECASE)
_DOI_ARXIV_RE = re.compile(r"^10\.48550/arxiv\.(?P<id>.+?)(?:v\d+)?$", re.IGNORECASE)

MAX_ABSTRACT_CHARS = 6000


class TransientHTTPError(RuntimeError):
    """リトライ対象の一時的な HTTP エラー。"""


# --------------------------------------------------------------------------
# 共通ユーティリティ
# --------------------------------------------------------------------------


def _cfg(config: dict, path: str, default: Any) -> Any:
    """``"a.b.c"`` 形式で設定値を取り出す(無ければ default)。"""
    node: Any = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node


def _http_settings(config: dict) -> tuple[float, int, float]:
    timeout = float(_cfg(config, "http.timeout_sec", 30))
    retries = int(_cfg(config, "http.max_retries", 3))
    backoff = float(_cfg(config, "http.backoff_base_sec", 2.0))
    return timeout, retries, backoff


def _request(
    url: str,
    params: dict[str, Any],
    config: dict,
    *,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """GET。ネットワーク/一時エラー時は最大 ``http.max_retries`` 回リトライする。"""
    timeout, retries, backoff = _http_settings(config)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout, headers=headers)
            if resp.status_code in (408, 425, 429, 500, 502, 503, 504):
                raise TransientHTTPError(f"HTTP {resp.status_code} from {url}")
            resp.raise_for_status()
            return resp
        except (requests.RequestException, TransientHTTPError) as exc:
            last_exc = exc
            if attempt >= retries:
                break
            wait = backoff * (2**attempt)
            log.warning(
                "取得に失敗(%s)。%.1f 秒後に再試行 (%d/%d)",
                exc,
                wait,
                attempt + 1,
                retries,
            )
            time.sleep(wait)
    raise RuntimeError(f"{url} の取得に失敗しました: {last_exc}") from last_exc


def normalize_doi(raw: str | None) -> str | None:
    """DOI を小文字・プレフィックス無しに正規化する。"""
    if not raw:
        return None
    doi = str(raw).strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    doi = doi.strip().lower()
    return doi or None


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------
# OpenAlex
# --------------------------------------------------------------------------


def _abstract_from_inverted_index(inverted: dict[str, list[int]] | None) -> str:
    """OpenAlex の abstract_inverted_index を平文に戻す。"""
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        if not isinstance(idxs, list):
            continue
        for idx in idxs:
            if isinstance(idx, int):
                positions.append((idx, word))
    if not positions:
        return ""
    positions.sort(key=lambda pair: pair[0])
    text = " ".join(word for _, word in positions)
    return _clean_text(text)[:MAX_ABSTRACT_CHARS]


def _openalex_short_id(full_id: str | None) -> str:
    if not full_id:
        return ""
    return str(full_id).rstrip("/").rsplit("/", 1)[-1]


def _normalize_openalex_work(work: dict) -> dict | None:
    short_id = _openalex_short_id(work.get("id"))
    if not short_id:
        return None

    doi = normalize_doi(work.get("doi"))
    title = _clean_text(work.get("title") or work.get("display_name"))
    if not title:
        return None

    authors = [
        _clean_text((a.get("author") or {}).get("display_name"))
        for a in (work.get("authorships") or [])
    ]
    authors = [a for a in authors if a]

    primary = work.get("primary_location") or work.get("best_oa_location") or {}
    source = primary.get("source") or {}
    venue = _clean_text(source.get("display_name"))

    work_type = _clean_text(work.get("type")).lower()
    source_type = _clean_text(source.get("type")).lower()
    is_preprint = work_type == "preprint" or source_type == "repository"

    if doi:
        url = f"https://doi.org/{doi}"
    else:
        url = primary.get("landing_page_url") or work.get("id") or ""

    return {
        "id": f"openalex:{short_id}",
        "doi": doi,
        "title": title,
        "abstract": _abstract_from_inverted_index(work.get("abstract_inverted_index")),
        "authors": authors,
        "venue": venue,
        "published": _clean_text(work.get("publication_date"))[:10],
        "url": url,
        "source": "openalex",
        "is_preprint": bool(is_preprint),
        "_type": work_type,
        "_retracted": bool(work.get("is_retracted")),
    }


def _openalex_filter_groups(config: dict) -> list[str]:
    """OpenAlex の音楽関連フィルタ(OR できない軸ごとに分割)。"""
    topic_ids = _cfg(config, "sources.openalex.topic_ids", DEFAULT_OPENALEX_TOPIC_IDS)
    subfield_ids = _cfg(config, "sources.openalex.subfield_ids", DEFAULT_OPENALEX_SUBFIELD_IDS)
    groups: list[str] = []
    if subfield_ids:
        groups.append("primary_topic.subfield.id:" + "|".join(str(s) for s in subfield_ids))
    if topic_ids:
        groups.append("topics.id:" + "|".join(str(t) for t in topic_ids))
    return groups


def _fetch_openalex(config: dict, since: str, until: str) -> list[dict]:
    mailto = _cfg(config, "sources.openalex.mailto", "") or ""
    per_page = int(_cfg(config, "sources.openalex.per_page", 200))
    max_pages = int(_cfg(config, "sources.openalex.max_pages", 10))
    headers = {"User-Agent": USER_AGENT.format(mailto=mailto or "unknown")}

    collected: dict[str, dict] = {}
    for group in _openalex_filter_groups(config):
        filters = ",".join(
            [group, f"from_publication_date:{since}", f"to_publication_date:{until}"]
        )
        cursor = "*"
        for page in range(max_pages):
            params: dict[str, Any] = {
                "filter": filters,
                "per-page": per_page,
                "cursor": cursor,
            }
            if mailto:
                # polite pool
                params["mailto"] = mailto
            resp = _request(OPENALEX_WORKS_URL, params, config, headers=headers)
            payload = resp.json()
            results = payload.get("results") or []
            for work in results:
                paper = _normalize_openalex_work(work)
                if paper:
                    collected[paper["id"]] = paper
            cursor = (payload.get("meta") or {}).get("next_cursor")
            log.debug(
                "OpenAlex [%s] page %d: %d 件 (累計 %d)",
                group,
                page + 1,
                len(results),
                len(collected),
            )
            if not cursor or not results:
                break
        else:
            log.warning("OpenAlex [%s]: max_pages に到達しました", group)

    log.info("OpenAlex: %d 件を取得しました", len(collected))
    return list(collected.values())


# --------------------------------------------------------------------------
# arXiv
# --------------------------------------------------------------------------


def _arxiv_bare_id(entry_id: str) -> str:
    match = _ARXIV_ID_RE.search(entry_id.strip())
    if match:
        return match.group("id")
    return entry_id.rstrip("/").rsplit("/", 1)[-1]


def _normalize_arxiv_entry(entry: ET.Element) -> dict | None:
    entry_id = _clean_text(entry.findtext("a:id", "", ATOM_NS))
    if not entry_id:
        return None
    bare_id = _arxiv_bare_id(entry_id)
    title = _clean_text(entry.findtext("a:title", "", ATOM_NS))
    if not title:
        return None

    authors = [
        _clean_text(a.findtext("a:name", "", ATOM_NS)) for a in entry.findall("a:author", ATOM_NS)
    ]
    authors = [a for a in authors if a]

    doi = normalize_doi(entry.findtext("ar:doi", "", ATOM_NS))
    journal_ref = _clean_text(entry.findtext("ar:journal_ref", "", ATOM_NS))
    published = _clean_text(entry.findtext("a:published", "", ATOM_NS))[:10]
    abstract = _clean_text(entry.findtext("a:summary", "", ATOM_NS))[:MAX_ABSTRACT_CHARS]

    return {
        "id": f"arxiv:{bare_id}",
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "venue": journal_ref or "arXiv",
        "published": published,
        "url": f"https://arxiv.org/abs/{bare_id}",
        "source": "arxiv",
        "is_preprint": True,
        "_type": "preprint",
        "_retracted": False,
    }


def _fetch_arxiv(config: dict, since: str, until: str) -> list[dict]:
    categories = _cfg(config, "sources.arxiv.categories", ["cs.SD", "eess.AS"]) or []
    if not categories:
        return []
    page_size = int(_cfg(config, "sources.arxiv.page_size", 100))
    max_results = int(_cfg(config, "sources.arxiv.max_results", 600))
    delay = float(_cfg(config, "sources.arxiv.request_delay_sec", 3.0))
    mailto = _cfg(config, "sources.openalex.mailto", "") or ""
    headers = {"User-Agent": USER_AGENT.format(mailto=mailto or "unknown")}

    since_date = _parse_iso_date(since)
    until_date = _parse_iso_date(until)

    cat_query = " OR ".join(f"cat:{c}" for c in categories)
    query = f"({cat_query})"
    if since_date and until_date:
        # インデックス遅延に備えて前後 1 日広めに取り、最終的な絞り込みは published で行う
        lo = (since_date - timedelta(days=1)).strftime("%Y%m%d") + "0000"
        hi = (until_date + timedelta(days=1)).strftime("%Y%m%d") + "2359"
        query += f" AND submittedDate:[{lo} TO {hi}]"

    collected: dict[str, dict] = {}
    start = 0
    while start < max_results:
        params = {
            "search_query": query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": start,
            "max_results": min(page_size, max_results - start),
        }
        resp = _request(ARXIV_API_URL, params, config, headers=headers)
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise RuntimeError(f"arXiv のレスポンスを解析できません: {exc}") from exc

        entries = root.findall("a:entry", ATOM_NS)
        if not entries:
            break

        oldest_on_page: date | None = None
        for entry in entries:
            paper = _normalize_arxiv_entry(entry)
            if not paper:
                continue
            published = _parse_iso_date(paper["published"])
            if published is None:
                continue
            if oldest_on_page is None or published < oldest_on_page:
                oldest_on_page = published
            if until_date and published > until_date:
                continue  # 未来日付(念のため)
            if since_date and published < since_date:
                continue  # バッファ分の古い論文
            collected[paper["id"]] = paper

        # 降順ソートなので、ページ末尾まで窓より古くなったら打ち切る
        reached_older = bool(since_date and oldest_on_page and oldest_on_page < since_date)

        log.debug("arXiv start=%d: %d 件取得 (窓内累計 %d)", start, len(entries), len(collected))
        if reached_older or len(entries) < params["max_results"]:
            break
        start += len(entries)
        if delay > 0:
            time.sleep(delay)  # arXiv API の利用規約に従いページ間で待つ

    log.info("arXiv: %d 件を取得しました", len(collected))
    return list(collected.values())


# --------------------------------------------------------------------------
# 重複排除
# --------------------------------------------------------------------------


def _dedup_keys(paper: dict) -> list[str]:
    """同一論文を突き合わせるためのキー群。"""
    keys: list[str] = [paper["id"]]
    doi = paper.get("doi")
    if doi:
        keys.append(f"doi:{doi}")
        # OpenAlex 側の arXiv DOI (10.48550/arXiv.XXXX) を arXiv ID と対応づける
        match = _DOI_ARXIV_RE.match(doi)
        if match:
            keys.append(f"arxiv:{match.group('id').lower()}")
    if paper.get("source") == "arxiv":
        keys.append(paper["id"].lower())
    return keys


def dedupe_papers(papers: Iterable[dict]) -> list[dict]:
    """DOI / ID で重複排除する。同一論文は出版版(OpenAlex)を優先。"""
    # OpenAlex を先に処理することで、後続の arXiv 版が捨てられる
    ordered = sorted(papers, key=lambda p: 0 if p.get("source") == "openalex" else 1)

    seen: dict[str, dict] = {}
    result: list[dict] = []
    for paper in ordered:
        keys = _dedup_keys(paper)
        existing = next((seen[k] for k in keys if k in seen), None)
        if existing is not None:
            log.debug("重複を除外: %s (採用済み %s)", paper.get("id"), existing.get("id"))
            continue
        for key in keys:
            seen[key] = paper
        result.append(paper)
    return result


# --------------------------------------------------------------------------
# 公開 API
# --------------------------------------------------------------------------


def fetch_papers(config: dict, since: str, until: str) -> list[dict]:
    """``since``〜``until``(YYYY-MM-DD)の音楽関連論文を収集して返す。

    OpenAlex(出版版)と arXiv(プレプリント)を統合し、DOI/ID で重複排除する。
    """
    papers: list[dict] = []
    attempted: list[str] = []
    failed: list[str] = []

    sources: list[tuple[str, str, Any]] = [
        ("OpenAlex", "sources.openalex.enabled", _fetch_openalex),
        ("arXiv", "sources.arxiv.enabled", _fetch_arxiv),
    ]

    for name, enabled_key, fetcher in sources:
        if not _cfg(config, enabled_key, True):
            log.info("%s は無効化されています", name)
            continue
        attempted.append(name)
        try:
            papers.extend(fetcher(config, since, until))
        except Exception as exc:
            # 1つのソースが落ちてもダイジェスト全体を失敗させない。
            # (例: arXiv の一時的な 429。残るソースの結果だけで配信を続行する)
            failed.append(name)
            log.warning("%s の取得に失敗したため、このソースを除外して続行します: %s", name, exc)

    if attempted and len(failed) == len(attempted):
        # 全ソースが失敗した場合のみ異常終了させる(空メールの送信を防ぐ)
        raise RuntimeError(f"すべての収集ソースが失敗しました: {', '.join(failed)}")
    if failed:
        log.warning(
            "一部ソースを欠いたまま処理を続行します(欠落: %s)。"
            "恒常的に発生する場合は該当ソースの API 仕様変更を疑ってください",
            ", ".join(failed),
        )

    deduped = dedupe_papers(papers)
    deduped.sort(key=lambda p: (p.get("published") or "", p.get("title") or ""), reverse=True)
    log.info("収集完了: %d 件(重複排除前 %d 件)", len(deduped), len(papers))
    return deduped


def default_window(config: dict) -> tuple[str, str]:
    """config の lookback_days から (since, until) を JST 基準で作る。"""
    lookback = int(_cfg(config, "lookback_days", 7))
    today = datetime.now(timezone(timedelta(hours=9))).date()
    since = today - timedelta(days=lookback)
    return since.isoformat(), today.isoformat()
