"""Claude によるバッチ関連度スコアリング.

``rank_batch_size`` 件ずつまとめて 0-100 の関連度スコアを付け、降順に並べて返す。
``client`` が ``None`` の場合(``--dry-run``)は決定的なモックスコアを返す。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import anthropic

log = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 40
DEFAULT_ABSTRACT_CHARS = 300
DEFAULT_SCORE_ON_FAILURE = 50

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

#: structured outputs が使えない SDK / モデルだった場合に落とすためのフラグ
_STRUCTURED_OUTPUT_SUPPORTED = True

RANK_SYSTEM_PROMPT = """\
あなたは学術ダイジェストの編集者です。読者は「音楽研究の最新動向を分野横断的に追う人」
(音楽情報検索・音楽心理学・音楽療法・音響学・民族音楽学・音楽教育・音楽理論・演奏科学
などを横断して読む研究者や実務家)です。

与えられた論文それぞれについて、この読者にとっての関連度を 0-100 の整数で採点してください。

採点の目安:
  90-100: 音楽そのものを主題とし、分野を越えて広く共有する価値がある重要な知見
  70-89 : 音楽研究として明確に興味深いが、対象や射程がやや限定的
  50-69 : 音楽に関係するが、貢献が小さい/技術的に極めて限定的/既知の追試
  20-49 : 音楽との関係が周辺的(音声認識一般、一般的な音響工学など)
  0-19  : 音楽研究とは実質的に無関係

同じ内容の論文が複数あっても互いに影響させず、独立に採点してください。
出力は指定された JSON のみとし、説明文やコードフェンスを付けないでください。\
"""


def _cfg(config: dict, path: str, default: Any) -> Any:
    node: Any = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node


# --------------------------------------------------------------------------
# Claude 呼び出し(JSON)
# --------------------------------------------------------------------------


def extract_json(text: str) -> dict:
    """モデル出力から JSON オブジェクトを取り出す。失敗時は ValueError。"""
    if not text:
        raise ValueError("空のレスポンス")
    candidate = text.strip()

    fence = _JSON_FENCE_RE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None

    if parsed is None:
        # 前後に説明文が付いた場合に備え、最初の対応の取れた {...} を探す
        start = candidate.find("{")
        while start != -1:
            depth = 0
            in_str = False
            escape = False
            for pos in range(start, len(candidate)):
                ch = candidate[pos]
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(candidate[start : pos + 1])
                        except json.JSONDecodeError:
                            parsed = None
                        break
            if parsed is not None:
                break
            start = candidate.find("{", start + 1)

    if not isinstance(parsed, dict):
        raise ValueError("JSON オブジェクトを取り出せませんでした")
    return parsed


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "\n".join(parts).strip()


def _create_message(
    client: Any,
    config: dict,
    *,
    system: str,
    user: str,
    max_tokens: int,
    schema: dict | None,
) -> str:
    global _STRUCTURED_OUTPUT_SUPPORTED

    kwargs: dict[str, Any] = {
        "model": _cfg(config, "model", "claude-haiku-4-5"),
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if schema and _STRUCTURED_OUTPUT_SUPPORTED:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}

    try:
        response = client.messages.create(**kwargs)
    except TypeError as exc:
        if "output_config" not in kwargs:
            raise
        log.warning("structured outputs が使えないため通常モードに切り替えます: %s", exc)
        _STRUCTURED_OUTPUT_SUPPORTED = False
        kwargs.pop("output_config", None)
        response = client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 - SDK 固有の例外型に依存しない
        if "output_config" in kwargs and "output_config" in str(exc):
            log.warning("structured outputs が拒否されたため通常モードに切り替えます: %s", exc)
            _STRUCTURED_OUTPUT_SUPPORTED = False
            kwargs.pop("output_config", None)
            response = client.messages.create(**kwargs)
        else:
            raise

    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError("Claude がリクエストを拒否しました (stop_reason=refusal)")
    return _response_text(response)


class FatalClaudeError(RuntimeError):
    """リトライしても回復しない Claude API エラー(認証・権限など)。

    呼び出し側はこの例外を握り潰してはならない。握り潰すと、要約もスコアも
    生成できていないのに「成功」として空同然のダイジェストを配信してしまう。
    """


def _is_fatal_claude_error(exc: Exception) -> bool:
    """リトライやフォールバックをしてはいけないエラーか判定する。

    対象は「何度呼んでも同じ結果になるもの」:
      - 401/403: キーが無効・失効、権限不足
      - 400 かつクレジット残高不足: アカウント側の課金設定の問題
    """
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return True
    if getattr(exc, "status_code", None) in (401, 403):
        return True
    # 残高不足は 400 (invalid_request_error) で返るため、メッセージで判別する
    message = str(exc).lower()
    return "credit balance" in message or "plans & billing" in message


def call_claude_json(
    client: Any,
    config: dict,
    *,
    system: str,
    user: str,
    max_tokens: int,
    schema: dict | None = None,
    retries: int = 1,
) -> dict:
    """Claude を呼び、JSON オブジェクトとして返す。パース失敗時は ``retries`` 回再試行。

    認証・権限エラーの場合は再試行せず ``FatalClaudeError`` を送出する。
    """
    last_error: Exception | None = None
    prompt = user
    for attempt in range(retries + 1):
        try:
            text = _create_message(
                client,
                config,
                system=system,
                user=prompt,
                max_tokens=max_tokens,
                schema=schema,
            )
            return extract_json(text)
        except Exception as exc:  # noqa: BLE001
            if _is_fatal_claude_error(exc):
                raise FatalClaudeError(
                    "Claude API を利用できませんでした(再試行しても回復しないため中断します)。"
                    "ANTHROPIC_API_KEY の有効性と、console.anthropic.com のクレジット残高を"
                    f"確認してください: {exc}"
                ) from exc
            last_error = exc
            if attempt >= retries:
                break
            log.warning(
                "Claude 応答の処理に失敗しました(%s)。再試行 %d/%d",
                exc,
                attempt + 1,
                retries,
            )
            prompt = (
                user + "\n\n（重要）直前の応答は有効な JSON として解析できませんでした。"
                "説明文・コードフェンスを一切付けず、指定された JSON オブジェクトのみを出力してください。"
            )
    raise RuntimeError(f"Claude から有効な JSON を得られませんでした: {last_error}")


# --------------------------------------------------------------------------
# スコアリング
# --------------------------------------------------------------------------


def _batch_schema() -> dict:
    """一括スコアリング用の JSON Schema(structured outputs)。"""
    return {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "i": {"type": "integer"},
                        "score": {"type": "integer"},
                    },
                    "required": ["i", "score"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["scores"],
        "additionalProperties": False,
    }


def _build_batch_prompt(batch: list[dict], abstract_chars: int) -> str:
    lines = [
        f"次の {len(batch)} 件の論文を採点してください。",
        "",
        '出力形式: {"scores": [{"i": <番号>, "score": <0-100の整数>}, ...]}',
        "すべての番号について、過不足なく 1 件ずつ出力してください。",
        "",
    ]
    for idx, paper in enumerate(batch):
        abstract = (paper.get("abstract") or "").strip()[:abstract_chars]
        lines.append(f"[{idx}] タイトル: {paper.get('title', '')}")
        lines.append(f"    アブスト冒頭: {abstract}")
        lines.append("")
    return "\n".join(lines)


def _clamp_score(value: Any) -> int | None:
    try:
        score = int(round(float(value)))
    except TypeError, ValueError:
        return None
    return max(0, min(100, score))


def _score_batch(batch: list[dict], config: dict, client: Any) -> dict[int, int]:
    abstract_chars = int(_cfg(config, "rank.abstract_chars", DEFAULT_ABSTRACT_CHARS))
    prompt = _build_batch_prompt(batch, abstract_chars)
    max_tokens = 64 + 24 * len(batch)

    payload = call_claude_json(
        client,
        config,
        system=RANK_SYSTEM_PROMPT,
        user=prompt,
        max_tokens=max_tokens,
        schema=_batch_schema(),
        retries=1,
    )

    scores: dict[int, int] = {}
    for item in payload.get("scores") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("i"))
        except TypeError, ValueError:
            continue
        score = _clamp_score(item.get("score"))
        if score is None or not (0 <= idx < len(batch)):
            continue
        scores[idx] = score
    return scores


# --------------------------------------------------------------------------
# モック(--dry-run)
# --------------------------------------------------------------------------

_MOCK_STRONG_TERMS = (
    "music",
    "musical",
    "song",
    "singing",
    "melody",
    "harmony",
    "rhythm",
    "instrument",
    "orchestra",
    "choir",
    "piano",
    "violin",
    "guitar",
    "composer",
    "performance",
    "ethnomusicolog",
)


def mock_score(paper: dict) -> int:
    """--dry-run 用の決定的なスコア(0-100)。"""
    digest = hashlib.sha256((paper.get("id") or paper.get("title") or "").encode("utf-8"))
    base = 35 + digest.digest()[0] % 45  # 35-79
    text = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
    hits = sum(1 for term in _MOCK_STRONG_TERMS if term in text)
    return max(0, min(100, base + min(hits, 4) * 5))


# --------------------------------------------------------------------------
# 公開 API
# --------------------------------------------------------------------------


def rank_papers(papers: list[dict], config: dict, client: Any) -> list[dict]:
    """各 Paper に ``score`` を付与し、スコア降順で返す。"""
    if not papers:
        return []

    ranked = [dict(paper) for paper in papers]

    if client is None:
        for paper in ranked:
            paper["score"] = mock_score(paper)
        log.info("スコアリング(モック): %d 件", len(ranked))
    else:
        batch_size = max(1, int(_cfg(config, "rank_batch_size", DEFAULT_BATCH_SIZE)))
        fallback = int(_cfg(config, "rank.default_score", DEFAULT_SCORE_ON_FAILURE))
        total_batches = (len(ranked) + batch_size - 1) // batch_size

        failed_batches = 0

        for batch_no in range(total_batches):
            batch = ranked[batch_no * batch_size : (batch_no + 1) * batch_size]
            try:
                scores = _score_batch(batch, config, client)
            except FatalClaudeError:
                # 認証エラー等はリトライしても回復しない。既定値で埋めて
                # 「成功」扱いにすると無内容のダイジェストを配信してしまうため中断する。
                raise
            except Exception as exc:  # noqa: BLE001 - 1 バッチの失敗で全体を落とさない
                failed_batches += 1
                log.error(
                    "バッチ %d/%d のスコアリングに失敗しました: %s",
                    batch_no + 1,
                    total_batches,
                    exc,
                )
                scores = {}

            missing = 0
            for idx, paper in enumerate(batch):
                score = scores.get(idx)
                if score is None:
                    score = fallback
                    missing += 1
                paper["score"] = score
            if missing:
                log.warning(
                    "バッチ %d/%d: %d 件のスコアが得られず既定値 %d を使用しました",
                    batch_no + 1,
                    total_batches,
                    missing,
                    fallback,
                )
            log.info("スコアリング: バッチ %d/%d 完了", batch_no + 1, total_batches)

        if total_batches and failed_batches == total_batches:
            # 全バッチが失敗した = 関連度判定が一切効いていない。
            # 既定値だけのダイジェストを配信しても意味がないため中断する。
            raise RuntimeError(
                f"スコアリングの全 {total_batches} バッチが失敗しました。"
                "Claude API の状態(レート制限・モデル名・ネットワーク)を確認してください"
            )

    # 同点は新しい論文を上に(安定ソートを二段掛け)
    ranked.sort(key=lambda p: (p.get("published") or "", p.get("title") or ""), reverse=True)
    ranked.sort(key=lambda p: int(p.get("score", 0)), reverse=True)
    return ranked
