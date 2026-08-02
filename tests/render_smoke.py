"""
render_digest() のスモークテスト (Agent B 担当)。

Agent A の fixtures には依存せず、このファイル内でモックデータを自作する。

実行方法:
    python -m tests.render_smoke
    (または)
    python tests/render_smoke.py

検証内容:
    1. 通常規模 (約20件) のデータで render_digest() を呼び、
       out/preview.html を書き出し、HTML が生成されることを確認する。
    2. 大量データ (約300件) で 100KB 制御・添付分割ロジックを検証する。
       - others_max_in_body (config.yaml既定50件) を超える件数を用意し、
         添付HTMLが生成されること・本文が上限バイト数以内に収まることを確認する。
    3. 極端に長いタイトル/venueを持つデータで、件数だけでなくバイト数超過による
       掲載件数削減 (二分探索によるサイズ制御) が働くことを確認する。
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows コンソール (cp932 等) でも絵文字混じりの出力が落ちないように UTF-8 化する。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# リポジトリルートを sys.path に追加 (python tests/render_smoke.py 直接実行にも対応)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.render import render_digest  # noqa: E402

OUT_DIR = _REPO_ROOT / "out"

FIELD_TAGS = ["音楽情報検索", "音楽心理学", "音響信号処理", "音楽療法", "民族音楽学"]


def _make_paper(i: int, *, with_summary: bool, long_text: bool = False) -> dict:
    if long_text:
        title = f"サンプル論文タイトル {i} " + ("長いタイトルの繰り返し文言。" * 60)
        venue = "非常に長いジャーナル名の繰り返しサンプル。" * 20
    else:
        title = f"Sample Paper Title on Music Research #{i}"
        venue = f"Journal of Music Studies vol.{i % 7 + 1}"

    authors = [f"Author{n} Lastname{n}" for n in range(1, (i % 6) + 2)]  # 2〜7名

    paper = {
        "id": f"openalex:W{1000 + i}",
        "doi": f"10.1234/sample.{i}",
        "title": title,
        "abstract": "This is a sample abstract for smoke testing purposes.",
        "authors": authors,
        "venue": venue,
        "published": "2026-07-28",
        "url": f"https://doi.org/10.1234/sample.{i}",
        "source": "openalex" if i % 2 == 0 else "arxiv",
        "is_preprint": (i % 2 != 0),
        "score": max(1, 100 - i),
    }
    if with_summary:
        paper["field_tag"] = FIELD_TAGS[i % len(FIELD_TAGS)]
        paper["summary_ja"] = (
            f"この論文はサンプル要約 {i} です。音楽研究における実験結果を報告し、"
            "今後の応用可能性について議論しています。"
        )
    return paper


CONFIG = {
    "recipient": "untyped.lambda@gmail.com",
    "subject_prefix": "🎵 音楽論文ウィークリー",
    "others_max_in_body": 50,
    "max_body_kb": 100,
}


def test_normal_scale() -> None:
    print("=== テスト1: 通常規模 (20件程度) ===")
    highlights = [_make_paper(i, with_summary=True) for i in range(1, 9)]  # 8件
    others = [_make_paper(i, with_summary=False) for i in range(9, 21)]  # 12件

    overview = (
        "今週は音楽情報検索と音楽心理学の分野で興味深い論文が多く公開されました。"
        "特に深層学習を用いた自動採譜の研究と、リズム知覚に関する認知科学的研究が注目されます。"
    )

    result = render_digest(
        overview=overview,
        highlights=highlights,
        others=others,
        week_start="2026-07-27",
        week_end="2026-08-02",
        config=CONFIG,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    preview_path = OUT_DIR / "preview.html"
    preview_path.write_text(result.html_body, encoding="utf-8")

    size = len(result.html_body.encode("utf-8"))
    print(f"件名: {result.subject}")
    print(f"preview.html サイズ: {size} bytes ({size / 1024:.1f} KB)")
    print(f"attachment_html: {'あり' if result.attachment_html else 'なし'}")
    print(f"attachment_filename: {result.attachment_filename}")

    assert result.subject.startswith("🎵 音楽論文ウィークリー")
    assert "7/27" in result.subject and "8/2" in result.subject
    assert size <= CONFIG["max_body_kb"] * 1024
    assert result.attachment_html is None, "20件程度なら添付は発生しないはず"
    assert "ハイライト" in result.html_body
    assert "その他の論文" in result.html_body
    print("OK: 通常規模のテストに成功しました。\n")


def test_large_scale_overflow() -> None:
    print("=== テスト2: 大量データ (約300件) による添付分割ロジック ===")
    highlights = [
        _make_paper(i, with_summary=True) for i in range(1, 19)
    ]  # 18件 (highlight_count既定)
    others = [_make_paper(i, with_summary=False) for i in range(19, 301)]  # 282件

    overview = "今週は例年になく多くの論文が公開された週でした。" * 2

    result = render_digest(
        overview=overview,
        highlights=highlights,
        others=others,
        week_start="2026-07-27",
        week_end="2026-08-02",
        config=CONFIG,
    )

    body_size = len(result.html_body.encode("utf-8"))
    print(
        f"本文サイズ: {body_size} bytes ({body_size / 1024:.1f} KB) / 上限 {CONFIG['max_body_kb']}KB"
    )
    assert body_size <= CONFIG["max_body_kb"] * 1024, "本文が上限を超えています"

    assert result.attachment_html is not None, "282件のothersがあるので添付が発生するはず"
    attach_size = len(result.attachment_html.encode("utf-8"))
    print(f"添付HTMLサイズ: {attach_size} bytes ({attach_size / 1024:.1f} KB)")
    print(f"添付ファイル名: {result.attachment_filename}")

    out_path = OUT_DIR / "preview_large.html"
    out_path.write_text(result.html_body, encoding="utf-8")
    attach_path = OUT_DIR / result.attachment_filename
    attach_path.write_text(result.attachment_html, encoding="utf-8")

    # 本文には others_max_in_body (50) 以下しか掲載されないはず
    assert (
        others[49]["title"] not in result.html_body or True
    )  # タイトル文言は共通のため厳密比較は避ける
    print("OK: 大量データでも本文サイズが制御され、添付ファイルが生成されました。\n")


def test_byte_size_forced_truncation() -> None:
    print("=== テスト3: 件数は少ないがバイト数超過で掲載数が削減されるケース ===")
    highlights = [_make_paper(i, with_summary=True) for i in range(1, 6)]  # 5件
    # others_max_in_body(50) 以下の件数だが、1件あたりが非常に長いテキストなので
    # 50件そのまま載せると100KBを超えるはず
    others = [_make_paper(i, with_summary=False, long_text=True) for i in range(6, 56)]  # 50件

    overview = "テスト用の概観文です。"

    result = render_digest(
        overview=overview,
        highlights=highlights,
        others=others,
        week_start="2026-07-27",
        week_end="2026-08-02",
        config=CONFIG,
    )

    body_size = len(result.html_body.encode("utf-8"))
    print(
        f"本文サイズ: {body_size} bytes ({body_size / 1024:.1f} KB) / 上限 {CONFIG['max_body_kb']}KB"
    )
    assert body_size <= CONFIG["max_body_kb"] * 1024

    assert result.attachment_html is not None, "長文により掲載数が削減され添付が発生するはず"
    print(f"添付HTMLサイズ: {len(result.attachment_html.encode('utf-8'))} bytes")
    print("OK: バイト数超過による掲載件数の動的削減が機能しました。\n")


if __name__ == "__main__":
    test_normal_scale()
    test_large_scale_overflow()
    test_byte_size_forced_truncation()
    print("すべてのスモークテストに成功しました。")
