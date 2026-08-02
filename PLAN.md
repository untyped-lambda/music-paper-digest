# 音楽論文ウィークリーダイジェスト 実装プラン

## 目的
毎週月曜 8:00 JST に、過去7日間に公開された音楽関連の学術論文を分野横断的に収集し、
日本語要約付きのダイジェストメールを untyped.lambda@gmail.com に送信する。

## 確定済みの方針
- 実行基盤: GitHub Actions(cron: `0 23 * * 0` = 日曜23:00 UTC = 月曜8:00 JST)+ workflow_dispatch(手動実行)
- 収集ソース: OpenAlex API(音楽関連トピックで分野横断)+ arXiv API(cs.SD, eess.AS)
- 重複排除: DOI / arXiv ID ベース + 既読DB(data/sent_ids.json、ワークフローがコミットして永続化)
- 選別: ルールベース事前フィルタ → Claude Haiku による関連度一括スコアリング(タイトル+アブスト冒頭)
- 要約: 上位 N 件(config で制御、既定18件)を Claude Haiku で日本語2〜3文要約+分野タグ付け
- 概観: メール冒頭に週全体の「エディターズノート」(3〜4文)を Claude が生成
- メール構造: ①概観 ②ハイライト(分野別グルーピング、要約付き) ③その他タイトル一覧(本文は50件まで、
  超過分は添付HTML)。Gmail の本文クリップ対策として本文は100KB以内に収める
- 送信: Gmail SMTP(アプリパスワード)
- 失敗通知: ワークフロー失敗時に `if: failure()` ステップで通知メールを送信
- 使用モデル: `claude-haiku-4-5-20251001`(スコアリング・要約・概観すべて)

## リポジトリ構成と担当

| パス | 内容 | 担当 |
|---|---|---|
| PLAN.md / INTERFACES.md / config.yaml / requirements.txt / data/sent_ids.json | 設計・設定 | リード(作成済み) |
| src/fetch.py | OpenAlex + arXiv 収集、正規化、重複排除 | Agent A (Opus) |
| src/prefilter.py | ルールベース事前フィルタ | Agent A (Opus) |
| src/rank.py | Haiku 一括スコアリング | Agent A (Opus) |
| src/summarize.py | ハイライト要約+分野タグ+概観生成 | Agent A (Opus) |
| src/sent_db.py | 既読DBの読み書き | Agent A (Opus) |
| src/main.py | パイプライン統括(render/send は INTERFACES.md の署名で呼ぶ) | Agent A (Opus) |
| src/render.py | HTML メール生成(3層構造、添付生成、100KB制御) | Agent B (Sonnet) |
| src/send.py | Gmail SMTP 送信(本文+添付) | Agent B (Sonnet) |
| src/notify_failure.py | 失敗通知メール | Agent B (Sonnet) |
| .github/workflows/digest.yml | cron / 手動実行 / sent_ids.json のコミット / 失敗通知 | Agent B (Sonnet) |
| README.md | セットアップ手順(日本語) | Agent B (Sonnet) |
| tests/fixtures/*.json + ドライラン | モックデータでの動作確認 | 両者(各自の範囲)|

## 環境変数(GitHub Actions Secrets)
- `ANTHROPIC_API_KEY` — Claude API キー
- `GMAIL_ADDRESS` — 送信元 Gmail アドレス
- `GMAIL_APP_PASSWORD` — Gmail アプリパスワード
- 宛先は config.yaml の `recipient` で指定

## 追加要望プラン(v2 — 2026-08-02 確定)

### 1. 設定の一元化(個人情報は環境変数注入)
公開リポジトリでも安全に運用でき、他ユーザーは fork + Secrets 登録だけで使える構成にする。
重複設定を避けるため、宛先関連はすべて既存 Secrets から導出する。

- 優先順位: **環境変数 > user.yaml > config.yaml**
- `config.yaml` — アプリ既定値のみ(個人情報を含まない)。`recipient` / `sources.openalex.mailto` キーは削除
- `user.yaml`(任意、コミット可)— 非機密の個人好み(subject_prefix, highlight_count 等)。config.yaml の任意キーをディープマージで上書き。`user.yaml.example` を同梱
- 環境変数(GitHub Secrets):
  - `ANTHROPIC_API_KEY`(必須)
  - `GMAIL_ADDRESS`(必須)— 送信元
  - `GMAIL_APP_PASSWORD`(必須)
  - `DIGEST_RECIPIENT`(任意)— 宛先。**未指定なら GMAIL_ADDRESS を流用**(自分宛て送信)
  - OpenAlex の `mailto` は宛先(=上記で解決した recipient)を流用。専用設定は持たない
- 実装: `src/config.py` に `load_config()` を新設(config.yaml 読込 → user.yaml ディープマージ → 環境変数解決を dict に反映)。main.py / notify_failure.py はこれを使う。他モジュールのシグネチャは変更不要(config dict の中身が変わるだけ)

### 2. README 追記
- **セキュリティ注意点**(環境変数まわり): アプリパスワードの最小権限性と漏洩時の失効手順、Secrets はログに出さない(printしない/デバッグ出力に注意)、fork からの PR には Secrets が渡らない仕様、`user.yaml`・コードに秘密情報を書かないこと、ローカル実行時は `.env` を使うなら .gitignore 必須、キーのローテーション推奨
- **Claude API 料金**: Haiku 4.5 = 入力 $1/MTok・出力 $5/MTok(2026-08 時点、要最新確認の注記)。本プロジェクトの想定消費(週1回、スコアリング+要約で数万トークン程度 → 月数十円規模)。従量課金のため Console での利用上限(spend limit)設定を推奨。バッチAPI(50%引)はリアルタイム性不要なら選択肢だが v1 では未使用

### 3. 開発環境
- `.devcontainer/devcontainer.json` — ベースイメージ `mcr.microsoft.com/devcontainers/python:3.14`、拡張: ms-python.python(Python extension pack)+ charliermarsh.ruff、settings: Python の defaultFormatter を ruff に、formatOnSave 有効。postCreateCommand で `pip install -r requirements.txt -r requirements-dev.txt`
- `requirements-dev.txt` — ruff
- `pyproject.toml` — `[tool.ruff]` 設定(line-length 100, target-version py314 等)
- **Python 3.14 へ統一**: GitHub Actions の setup-python を 3.14 に、README の記載も更新(Windows 本体へのインストールはリード側で実施済み: 3.14.6)

## ドライラン
API キーなしで検証できるよう、`python -m src.main --dry-run` は
tests/fixtures のモックデータを使い、メール送信の代わりに
`out/preview.html`(+必要なら `out/attachment.html`)をローカル出力する。
Claude 呼び出しはモック応答に差し替える。
