# モジュール間契約(両エージェント必読・変更禁止)

変更が必要だと判断した場合は変更せず、最終報告にその旨を書くこと。

## Paper レコード(全ステージ共通の dict)

```python
{
  "id": str,          # "openalex:W123..." or "arxiv:2401.01234" — 既読DBのキー
  "doi": str | None,  # 正規化: 小文字、"https://doi.org/" プレフィックスなし
  "title": str,
  "abstract": str,          # 事前フィルタ後は必ず非空
  "authors": list[str],     # 表示用。最大5名+"ほか"はrender側で処理
  "venue": str,             # ジャーナル/会議名。不明なら "" 
  "published": str,         # "YYYY-MM-DD"
  "url": str,               # ランディングページ(DOI URL 優先)
  "source": str,            # "openalex" | "arxiv"
  "is_preprint": bool,
  # rank.py 通過後に追加:
  "score": int,             # 0-100 関連度
  # summarize.py 通過後(ハイライトのみ)に追加:
  "field_tag": str,         # 日本語の分野タグ(例: "音楽情報検索", "音楽心理学")
  "summary_ja": str,        # 2〜3文の日本語要約
}
```

## Agent A → Agent B の境界(main.py が呼ぶ関数署名)

Agent B は以下の署名を実装する。Agent A の main.py はこの署名を前提に書く。

```python
# src/render.py
def render_digest(
    overview: str,                 # エディターズノート(日本語、プレーンテキスト)
    highlights: list[dict],        # summary_ja / field_tag 付き Paper、score降順
    others: list[dict],            # タイトルのみ掲載する Paper、score降順
    week_start: str,               # "YYYY-MM-DD"
    week_end: str,                 # "YYYY-MM-DD"
    config: dict,                  # config.yaml をパースした dict
) -> "RenderResult": ...

# RenderResult は dataclass:
#   subject: str                  # メール件名
#   html_body: str                # 本文HTML(100KB以内に収める責務は render 側)
#   attachment_html: str | None   # 溢れた分の完全リスト。不要なら None
#   attachment_filename: str      # 例 "digest-full-2026-08-03.html"

# src/send.py
def send_email(result: "RenderResult", config: dict) -> None:
    # 環境変数 GMAIL_ADDRESS / GMAIL_APP_PASSWORD を使用。
    # 宛先は config["recipient"]。
    # dry-run 時は呼ばれない(main.py 側で分岐)。

# src/notify_failure.py
# `python -m src.notify_failure` で単体実行可能にする。
# 件名「【音楽論文ダイジェスト】実行失敗」+ 実行日時・GitHub Actions の
# run URL(環境変数 GITHUB_SERVER_URL/GITHUB_REPOSITORY/GITHUB_RUN_ID から組立)を本文に。
```

## Agent A 内部の関数署名(参考)

```python
# src/fetch.py
def fetch_papers(config: dict, since: str, until: str) -> list[dict]  # 正規化済み・重複排除済み

# src/prefilter.py
def prefilter(papers: list[dict], config: dict) -> list[dict]

# src/rank.py
def rank_papers(papers: list[dict], config: dict, client) -> list[dict]  # score付与、降順

# src/summarize.py
def summarize_highlights(papers: list[dict], config: dict, client) -> list[dict]
def write_overview(highlights: list[dict], total_count: int, config: dict, client) -> str

# src/sent_db.py
def load_sent_ids(path: str) -> set[str]
def mark_sent(path: str, ids: list[str]) -> None   # 追記して保存(直近26週分のみ保持)
```

## CLI 仕様(main.py)

- `python -m src.main` — 本番実行(要 ANTHROPIC_API_KEY / GMAIL_*)
- `python -m src.main --dry-run` — fixtures 使用、Claude はモック、`out/preview.html` に出力
- 終了コード: 成功 0 / 失敗 非0(失敗通知はワークフロー側の責務)

## 共通ルール

- Python 3.14、依存は requirements.txt にあるもののみ(stdlib は自由)
- 文字コードは UTF-8。Windows 上でも動くこと(パス結合は pathlib)
- 秘密情報をコードに書かない。コミットは行わない(リード側で行う)
- config.yaml のキーを増やすのは可(自分の担当範囲のみ、既存キーの変更は不可)

## 確定事項(v2 — 2026-08-02)

- `rank.rank_papers` / `summarize.summarize_highlights` / `summarize.write_overview`
  の `client=None` は「モックモード」を表す既定の契約であり、`--dry-run` 時に
  main.py が意図的に `None` を渡すことで決定的なモック応答に切り替える
  (Claude を一切呼び出さない)。`client` が渡された場合は必ず実際に呼び出す。
- `render.RenderResult.attachment_filename` は `attachment_html` が `None` の
  ときは無視してよい(添付が無いのでファイル名も使われない)。両者は常に
  セットで扱われ、`attachment_html is None` の時に `attachment_filename` を
  参照するコードがあってはならない。
- fetch → prefilter の間でのみ使われる内部キー `_type` / `_retracted` は
  prefilter が必ず取り除く。prefilter を通過した後の Paper レコードには
  この2キーは存在せず、下流(rank / summarize / render / send)は契約どおりの
  Paper レコード(このファイル冒頭の定義)のみを受け取る。
- `--dry-run` は `data/sent_ids.json` への `mark_sent` を行わない(既読DBを
  更新しない)。dry-run は何度実行しても同じ fixtures に対して同じ結果を
  再現できることを優先し、既読除外や既読DBの更新は本番実行のみの責務とする。
