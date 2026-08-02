"""
音楽論文ウィークリーダイジェスト - 失敗通知モジュール (Agent B 担当)

`python -m src.notify_failure` で単体実行可能。
GitHub Actions のワークフローが失敗した際 (`if: failure()`) に呼び出される想定。

件名: 「【音楽論文ダイジェスト】実行失敗」
本文: 実行日時 + GitHub Actions の run URL
      (環境変数 GITHUB_SERVER_URL / GITHUB_REPOSITORY / GITHUB_RUN_ID から組み立てる)

送信処理は send.py の内部関数 (_deliver) を再利用する。
"""

from __future__ import annotations

import html
import os
import sys
from datetime import datetime, timedelta, timezone

from .config import load_config
from .send import _deliver

FAILURE_SUBJECT = "【音楽論文ダイジェスト】実行失敗"

JST = timezone(timedelta(hours=9))


def _build_run_url() -> str | None:
    server_url = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server_url and repo and run_id:
        return f"{server_url}/{repo}/actions/runs/{run_id}"
    return None


def _build_failure_html(now_str: str, run_url: str | None) -> str:
    run_line = (
        f'<p style="margin:0 0 8px 0;">実行ログ: '
        f'<a href="{html.escape(run_url)}">{html.escape(run_url)}</a></p>'
        if run_url
        else '<p style="margin:0 0 8px 0;">実行ログ: (GitHub Actions の環境変数が取得できませんでした)</p>'
    )
    return (
        "<!DOCTYPE html>"
        '<html lang="ja"><head><meta charset="utf-8">'
        f"<title>{html.escape(FAILURE_SUBJECT)}</title></head>"
        '<body style="margin:0;padding:0;">'
        '<div style="max-width:640px;margin:0 auto;padding:20px 16px;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,"
        "'Hiragino Kaku Gothic ProN','Noto Sans JP',sans-serif;color:#3a3a3a;\">"
        '<h1 style="font-size:16px;margin:0 0 14px 0;">音楽論文ウィークリーダイジェストの実行に失敗しました</h1>'
        f'<p style="margin:0 0 8px 0;">実行日時: {html.escape(now_str)} (JST)</p>'
        f"{run_line}"
        '<p style="margin:16px 0 0 0;font-size:12px;color:#767676;">'
        "このメールは GitHub Actions のワークフローから自動送信されています。</p>"
        "</div></body></html>"
    )


def main() -> int:
    try:
        config = load_config()
        recipient = config["recipient"]
    except Exception as exc:  # noqa: BLE001
        print(
            f"[notify_failure] config.yaml の読み込みに失敗しました: {exc}",
            file=sys.stderr,
        )
        return 1

    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    run_url = _build_run_url()
    html_body = _build_failure_html(now_str, run_url)

    try:
        _deliver(
            subject=FAILURE_SUBJECT,
            html_body=html_body,
            to_addr=recipient,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"[notify_failure] 失敗通知メールの送信に失敗しました: {exc}",
            file=sys.stderr,
        )
        return 1

    print("[notify_failure] 失敗通知メールを送信しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
