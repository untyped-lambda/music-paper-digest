"""音楽論文ウィークリーダイジェストのパイプライン統括.

使い方::

    python -m src.main             # 本番実行(要 ANTHROPIC_API_KEY / GMAIL_*)
    python -m src.main --dry-run   # fixtures 使用・Claude はモック・out/ に出力

終了コード: 成功 0 / 失敗 非0
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import fetch as fetch_mod
from .config import load_config
from .prefilter import prefilter
from .rank import rank_papers
from .sent_db import load_sent_ids, mark_sent
from .summarize import summarize_highlights, write_overview

log = logging.getLogger("music_paper_digest")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_FIXTURES_PATH = REPO_ROOT / "tests" / "fixtures" / "papers_sample.json"
DEFAULT_OUT_DIR = REPO_ROOT / "out"

JST = timezone(timedelta(hours=9))


# --------------------------------------------------------------------------
# 準備
# --------------------------------------------------------------------------


def setup_logging(verbose: bool = False) -> None:
    for stream in (sys.stdout, sys.stderr):
        # Windows のコンソールでも日本語ログを落とさない
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _cfg(config: dict, path: str, default: Any) -> Any:
    node: Any = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node


def _resolve(path_value: str | os.PathLike[str]) -> Path:
    p = Path(path_value)
    return p if p.is_absolute() else REPO_ROOT / p


def build_client(config: dict) -> Any:
    """本番実行用の Anthropic クライアントを作る。"""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "anthropic パッケージが見つかりません。requirements.txt をインストールしてください。"
        ) from exc

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("環境変数 ANTHROPIC_API_KEY が設定されていません。")
    return anthropic.Anthropic()


def load_fixture_papers(config: dict) -> list[dict]:
    path = _resolve(_cfg(config, "dry_run.fixtures_path", DEFAULT_FIXTURES_PATH))
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("papers", [])
    if not isinstance(data, list):
        raise ValueError(f"fixtures の形式が不正です: {path}")
    log.info("fixtures から %d 件を読み込みました (%s)", len(data), path)
    return data


# --------------------------------------------------------------------------
# パイプライン
# --------------------------------------------------------------------------


def compute_window(config: dict) -> tuple[str, str]:
    lookback = int(_cfg(config, "lookback_days", 7))
    today = datetime.now(JST).date()
    since = today - timedelta(days=lookback)
    return since.isoformat(), today.isoformat()


REQUIRED_PRODUCTION_ENV_VARS = ("ANTHROPIC_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")


def _require_production_env() -> None:
    """本番実行(--dry-run 以外)に必要な環境変数が揃っているか確認する。

    未設定があれば、後段の処理(収集・スコアリング等)を無駄に走らせる前に
    明確なメッセージで失敗させる。
    """
    missing = [name for name in REQUIRED_PRODUCTION_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "本番実行には環境変数 " + ", ".join(missing) + " の設定が必要です。"
            " GitHub Secrets(またはローカルの環境変数)に登録してください。"
            " 詳細は README.md の『GitHub Secrets の登録』を参照してください。"
        )


def run(dry_run: bool = False, config_path: str | os.PathLike[str] | None = None) -> int:
    config = load_config(config_path or DEFAULT_CONFIG_PATH)
    if not dry_run:
        _require_production_env()
    since, until = compute_window(config)
    log.info("対象期間: %s 〜 %s (dry_run=%s)", since, until, dry_run)

    # 1. 収集
    if dry_run:
        papers = load_fixture_papers(config)
    else:
        papers = fetch_mod.fetch_papers(config, since, until)
    if not papers:
        log.warning("収集結果が 0 件でした。処理を終了します。")
        return 0

    # 2. 事前フィルタ
    papers = prefilter(papers, config)
    if not papers:
        log.warning("事前フィルタ通過が 0 件でした。処理を終了します。")
        return 0

    # 3. 既読除外
    sent_db_path = str(_resolve(_cfg(config, "sent_db_path", "data/sent_ids.json")))
    already_sent = load_sent_ids(sent_db_path)
    before = len(papers)
    papers = [p for p in papers if p.get("id") not in already_sent]
    log.info("既読除外: %d 件を除外し %d 件が残りました", before - len(papers), len(papers))
    if not papers:
        log.warning("新規論文がありませんでした。処理を終了します。")
        return 0

    client = None if dry_run else build_client(config)

    # 4. スコアリング
    ranked = rank_papers(papers, config, client)

    min_score = int(_cfg(config, "min_score", 40))
    selected = [p for p in ranked if int(p.get("score", 0)) >= min_score]
    log.info(
        "min_score=%d で %d 件を採用(%d 件を除外)",
        min_score,
        len(selected),
        len(ranked) - len(selected),
    )
    if not selected:
        log.warning("min_score を超える論文がありませんでした。処理を終了します。")
        return 0

    highlight_count = int(_cfg(config, "highlight_count", 18))
    highlight_candidates = selected[:highlight_count]
    others = selected[highlight_count:]

    # 5. 要約・分野タグ・概観
    highlights = summarize_highlights(highlight_candidates, config, client)
    overview = write_overview(highlights, len(selected), config, client)
    log.info("ハイライト %d 件 / その他 %d 件 を生成しました", len(highlights), len(others))

    # 6. レンダリング(Agent B 担当)
    render_digest = _import_render()
    if render_digest is None:
        _dump_payload(config, overview, highlights, others, since, until)
        log.warning(
            "src/render.py が未実装のため、レンダリング直前で処理を終了しました"
            "(パイプライン本体は正常に完走しています)。"
        )
        return 0

    result = render_digest(
        overview=overview,
        highlights=highlights,
        others=others,
        week_start=since,
        week_end=until,
        config=config,
    )

    sent_ids = [p["id"] for p in highlights + others if p.get("id")]

    if dry_run:
        _write_preview(config, result)
        log.info(
            "dry-run のため既読DB(%s)は更新しません(%d 件を対象外)",
            sent_db_path,
            len(sent_ids),
        )
        return 0

    from .send import send_email

    send_email(result, config)
    log.info("メールを送信しました: %s", getattr(result, "subject", ""))

    # 7. 既読DB更新(送信に成功した後。dry-run では更新しない)
    mark_sent(sent_db_path, sent_ids)
    return 0


def _import_render():
    """render.py がまだ存在しない場合は None を返す。"""
    try:
        from .render import render_digest
    except ModuleNotFoundError as exc:
        if exc.name in ("src.render", "render"):
            return None
        raise
    return render_digest


def _out_dir(config: dict) -> Path:
    out_dir = _resolve(_cfg(config, "dry_run.out_dir", DEFAULT_OUT_DIR))
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _write_preview(config: dict, result: Any) -> None:
    out_dir = _out_dir(config)

    preview = out_dir / "preview.html"
    preview.write_text(getattr(result, "html_body", "") or "", encoding="utf-8")
    log.info("プレビューを書き出しました: %s", preview)

    attachment_html = getattr(result, "attachment_html", None)
    if attachment_html:
        filename = getattr(result, "attachment_filename", "") or "attachment.html"
        attachment = out_dir / Path(filename).name
        attachment.write_text(attachment_html, encoding="utf-8")
        log.info("添付HTMLを書き出しました: %s", attachment)

    subject = out_dir / "subject.txt"
    subject.write_text(getattr(result, "subject", "") or "", encoding="utf-8")


def _dump_payload(
    config: dict,
    overview: str,
    highlights: list[dict],
    others: list[dict],
    since: str,
    until: str,
) -> None:
    """render 未実装時に、render へ渡す予定だった内容をそのまま書き出す。"""
    out_dir = _out_dir(config)
    payload = {
        "overview": overview,
        "highlights": highlights,
        "others": others,
        "week_start": since,
        "week_end": until,
    }
    path = out_dir / "render_input.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("render へ渡す予定の内容を書き出しました: %s", path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description="音楽論文ウィークリーダイジェストを生成・送信する",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fixtures とモック応答を使い、送信の代わりに out/ へ書き出す",
    )
    parser.add_argument("--config", default=None, help="config.yaml のパス")
    parser.add_argument("--verbose", action="store_true", help="デバッグログを出力する")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    try:
        return run(dry_run=args.dry_run, config_path=args.config)
    except Exception:
        log.exception("パイプラインが失敗しました")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
