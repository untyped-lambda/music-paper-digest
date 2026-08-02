"""
音楽論文ウィークリーダイジェスト - メール送信モジュール (Agent B 担当)

INTERFACES.md の契約:
    def send_email(result: RenderResult, config: dict) -> None

Gmail SMTP (smtp.gmail.com:465, SSL) を使用。
環境変数 GMAIL_ADDRESS / GMAIL_APP_PASSWORD で認証する。
宛先は config["recipient"]。
stdlib の smtplib / email のみを使用する(サードパーティ不使用)。
"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .render import RenderResult

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

_PLAIN_FALLBACK_TEXT = "このメールはHTML形式です。HTML対応のメールクライアントでご覧ください。"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"環境変数 {name} が設定されていません。")
    return value


def _build_message(
    *,
    subject: str,
    from_addr: str,
    to_addr: str,
    html_body: str,
    attachment_html: str | None,
    attachment_filename: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(_PLAIN_FALLBACK_TEXT, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    if attachment_html is not None:
        part = MIMEText(attachment_html, "html", "utf-8")
        part.add_header("Content-Disposition", "attachment", filename=attachment_filename)
        msg.attach(part)

    return msg


def _deliver(
    *,
    subject: str,
    html_body: str,
    to_addr: str,
    attachment_html: str | None = None,
    attachment_filename: str = "",
) -> None:
    """SMTP 送信の共通処理。send_email / notify_failure から再利用する。"""
    gmail_address = _require_env("GMAIL_ADDRESS")
    gmail_app_password = _require_env("GMAIL_APP_PASSWORD")

    msg = _build_message(
        subject=subject,
        from_addr=gmail_address,
        to_addr=to_addr,
        html_body=html_body,
        attachment_html=attachment_html,
        attachment_filename=attachment_filename,
    )

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(gmail_address, gmail_app_password)
        server.send_message(msg)


def send_email(result: RenderResult, config: dict) -> None:
    recipient = config["recipient"]
    _deliver(
        subject=result.subject,
        html_body=result.html_body,
        to_addr=recipient,
        attachment_html=result.attachment_html,
        attachment_filename=result.attachment_filename,
    )
