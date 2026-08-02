# 音楽論文ウィークリーダイジェスト

毎週月曜 8:00 (JST) に、過去7日間に公開された音楽関連の学術論文を OpenAlex API と
arXiv API (cs.SD, eess.AS) から分野横断的に収集し、Claude (Haiku) でスコアリング・
日本語要約したうえで、ダイジェストメールを Gmail 経由で送信する GitHub Actions
ワークフローです。

## 全体構成

- `src/fetch.py` `src/prefilter.py` `src/rank.py` `src/summarize.py` `src/sent_db.py`
  `src/main.py` — 収集・選別・要約・パイプライン統括
- `src/config.py` — 設定の一元化(config.yaml → user.yaml → 環境変数の順に解決)
- `src/render.py` — メール本文 (HTML) の生成
- `src/send.py` — Gmail SMTP によるメール送信
- `src/notify_failure.py` — ワークフロー失敗時の通知メール
- `.github/workflows/digest.yml` — 定期実行・手動実行・既読DBのコミット・失敗通知
- `config.yaml` — アプリの既定値設定(個人情報を含まない)
- `user.yaml.example` — 個人の好みを上書きするための任意設定ファイルの雛形
- `data/sent_ids.json` — 既読(送信済み)論文IDのデータベース (ワークフローが自動更新)
- `.devcontainer/devcontainer.json` `requirements-dev.txt` `pyproject.toml` — 開発環境(ruff)

詳細な設計方針は [PLAN.md](./PLAN.md)、モジュール間の契約は
[INTERFACES.md](./INTERFACES.md) を参照してください。

## セットアップ手順

### 1. リポジトリの作成と push

GitHub 上で新しいリポジトリ (例: `music-paper-digest`) を作成し、このディレクトリの
内容を push します。

```bash
cd music-paper-digest
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/<your-account>/music-paper-digest.git
git push -u origin main
```

`data/sent_ids.json` はワークフローが自動でコミット・push するため、リポジトリの
`GITHUB_TOKEN` に書き込み権限が必要です (後述のワークフロー権限設定は済んでいます)。
`Settings → Actions → General → Workflow permissions` で
**"Read and write permissions"** が選択されていることを確認してください
(組織/リポジトリの設定によっては既定が read-only になっている場合があります)。

### 2. GitHub Secrets の登録

このリポジトリの `Settings → Secrets and variables → Actions → New repository secret`
から、以下の Secrets を登録します。個人情報(宛先メールアドレス等)はコード
(`config.yaml` 等)には一切書かず、すべて Secrets 経由の環境変数で注入する構成に
なっているため、**fork してこの4つ(うち1つは任意)を登録するだけ**で誰でも
自分専用のダイジェストとして使えます。

| Secret名 | 必須 | 内容 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 必須 | Claude API キー ([Anthropic Console](https://console.anthropic.com/) で発行) |
| `GMAIL_ADDRESS` | 必須 | 送信元 Gmail アドレス (例: `you@gmail.com`) |
| `GMAIL_APP_PASSWORD` | 必須 | Gmail アプリパスワード (発行手順は次項) |
| `DIGEST_RECIPIENT` | 任意 | ダイジェストの宛先メールアドレス。**未登録の場合は `GMAIL_ADDRESS` 宛て(自分宛て送信)になる** |

手順:
1. リポジトリの `Settings` タブを開く
2. 左メニューの `Secrets and variables` → `Actions` を選択
3. `New repository secret` をクリックし、Name と Secret の値を入力して `Add secret`
4. 上記の必須3つ(+必要なら `DIGEST_RECIPIENT`)分繰り返す

### 3. Gmail アプリパスワードの発行手順

Gmail 側で2段階認証を有効にしたうえで、アプリパスワードを発行します。

1. 送信元にしたい Google アカウントで [Google アカウント設定](https://myaccount.google.com/security)
   を開く
2. 「2段階認証プロセス」が **有効** になっていることを確認する (無効なら先に有効化する)
3. 2段階認証設定ページの下部にある「アプリ パスワード」を開く
   (直接 https://myaccount.google.com/apppasswords にアクセスしても良い)
4. アプリ名 (例: `music-digest`) を入力して生成する
5. 表示された16桁のパスワード (スペースは除いても可) をコピーし、
   `GMAIL_APP_PASSWORD` シークレットに登録する
6. 通常のログインパスワードではなく、必ずこのアプリパスワードを使用すること

### 4. workflow_dispatch による手動テスト

Secrets の登録が終わったら、実際のスケジュールを待たずに動作確認できます。

1. GitHub リポジトリの `Actions` タブを開く
2. 左メニューから `weekly-digest` ワークフローを選択する
3. 右上の `Run workflow` ボタン → ブランチを選んで `Run workflow` を実行する
4. 実行後、ジョブのログで各ステップ (fetch → rank → summarize → render → send) が
   成功しているか確認する
5. 成功すると宛先(`DIGEST_RECIPIENT` Secret、未登録なら `GMAIL_ADDRESS`)宛に
   ダイジェストメールが届く
6. `data/sent_ids.json` に差分があれば `github-actions[bot]` によるコミットが
   リポジトリに追加される
7. 失敗した場合は自動的に失敗通知メールが送信される

### 5. ローカルでの --dry-run 方法

API キーや Gmail 認証情報が無くても、モックデータと固定応答で一連の流れを
ローカル確認できます。

```bash
# 依存関係のインストール (Python 3.14 推奨)
pip install -r requirements.txt

# ドライラン実行 (実際の送信は行わず out/preview.html に出力する)
python -m src.main --dry-run
```

dry-run は環境変数(`ANTHROPIC_API_KEY` / `GMAIL_ADDRESS` 等)が一切無くても
動きます。宛先が未解決の場合は内部的に `dry-run@example.com` を使うだけで、
エラーにはなりません。また **dry-run は `data/sent_ids.json` を更新しません**
(既読DBは本番実行時のみ更新されます)。そのため同じ fixtures に対して
`--dry-run` を何度実行しても、毎回同じ内容のプレビューが再現されます
(2回目の実行が「既読扱いで0件」にならないことを確認済みです)。

実行後の出力レイアウトは次のとおりです。ブラウザで `out/preview.html` を開いて
表示を確認してください。

```
out/
├── preview.html            # 本文プレビュー(最新版。毎回上書き)
├── subject.txt             # メール件名(最新版。毎回上書き)
└── 2026/
    └── 08/
        └── digest-full-2026-08-02.html   # 添付HTML(週末日ごとに蓄積)
```

添付HTML(`digest-full-<週末日>.html`)は本文が 100KB を超えた場合にのみ生成され、
実行のたびに増えていくため **`out/<年>/<月>/` へ自動で振り分け**られます。
振り分けに使う日付は対象週の週末日(`--dry-run` では実行日)です。
`preview.html` と `subject.txt` は「常に同じパスで最新版を開ける」ほうが便利なため、
振り分けの対象外として `out/` 直下を上書きします。

なお `out/` は `.gitignore` 済みで、GitHub Actions のランナーは実行ごとに
使い捨てられるため、蓄積が問題になるのはローカル実行時のみです。

`src/render.py` 単体の動作確認には以下のスモークテストも利用できます。

```bash
python -m tests.render_smoke
```

## 設定の優先順位

設定は3層構造で解決されます(`src/config.py` の `load_config()`)。

```
環境変数 (GitHub Secrets / ローカルの環境変数)
    > user.yaml (任意・コミット可・非機密の個人好み)
        > config.yaml (アプリの既定値・個人情報を含まない)
```

- **config.yaml** — 誰にとっても共通のアプリ既定値のみを置く。宛先メール
  アドレスなど個人情報は含まない(v1 からの変更点: `recipient` /
  `sources.openalex.mailto` キーは削除しました)
- **user.yaml**(任意)— `subject_prefix` / `highlight_count` /
  `others_max_in_body` / `lookback_days` など、config.yaml の好きなキーを
  ディープマージで上書きできる、非機密の個人好み設定。`user.yaml.example`
  をコピーして `user.yaml` を作成してください。秘密情報は書かないこと
  (このファイルはコミットして構いません)
- **環境変数**(GitHub Secrets)— 個人情報や認証情報のみ。宛先は
  `DIGEST_RECIPIENT` を明示的に指定できますが、**未指定なら送信元の
  `GMAIL_ADDRESS` 宛て(自分宛て送信)になります**。OpenAlex API に渡す
  `mailto`(polite pool 用の連絡先)も、同じ解決結果を自動的に流用します
  (専用の設定キーはありません)

この構成のおかげで、**他ユーザーはこのリポジトリを fork して
`ANTHROPIC_API_KEY` / `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` の3つの
Secrets(+任意で `DIGEST_RECIPIENT`)を登録するだけ**で、コードを一切
変更せずに自分専用のダイジェストとして動かせます。

## config.yaml の各キーの説明

```yaml
subject_prefix: "🎵 音楽論文ウィークリー"  # 件名の先頭に付与する文字列

sources:
  openalex:
    enabled: true            # OpenAlex API を収集ソースとして使うか
    # mailto は load_config() が recipient (環境変数解決結果) から自動注入する
  arxiv:
    enabled: true            # arXiv API を収集ソースとして使うか
    categories: [cs.SD, eess.AS]  # 収集対象の arXiv カテゴリ
lookback_days: 7              # 何日分さかのぼって収集するか

min_score: 40            # この関連度スコア未満の論文は「その他」にも載せず捨てる
highlight_count: 18      # 日本語要約付きの「ハイライト」に採用する上位件数
others_max_in_body: 50   # 本文の「その他タイトル一覧」に載せる上限件数
                         # (超過分は添付HTMLの完全版に回る)

model: claude-haiku-4-5-20251001  # スコアリング・要約・概観生成に使う Claude モデル
rank_batch_size: 40                # 一括スコアリング時の1バッチあたりの件数

sent_db_path: data/sent_ids.json   # 既読(送信済み)論文IDを記録するJSONファイルのパス

max_body_kb: 100   # メール本文HTMLの上限サイズ(KB)。超過分は添付HTMLへ回す
                   # (Gmail の本文クリップ対策)
```

`recipient` と `sources.openalex.mailto` は上記の「設定の優先順位」に従い
実行時に自動解決されるため、config.yaml には存在しません。

## 開発環境

VS Code の Dev Containers 拡張を使う場合、`.devcontainer/devcontainer.json` を
開くだけで Python 3.14 + `ms-python.python` + `charliermarsh.ruff`(保存時に
自動フォーマット)の環境が立ち上がり、`requirements.txt` /
`requirements-dev.txt` が自動インストールされます。ベースイメージ
`mcr.microsoft.com/devcontainers/python:3.14` は 2026-08-02 時点で
mcr.microsoft.com に公開されていることを確認済みです。万一このタグが
将来 deprecated になっている場合は、`.devcontainer/devcontainer.json` の
`image` を `mcr.microsoft.com/devcontainers/python:3.14-bookworm` に
差し替えてください。

devcontainer を使わない場合も、通常の venv +
`pip install -r requirements.txt -r requirements-dev.txt` で同じ Lint
環境を用意できます。

```bash
ruff check src tests
ruff format src tests
```

### Git フック(lefthook)

commit のたびに ruff を手で流す必要がないよう、[lefthook](https://github.com/evilmartians/lefthook)
で pre-commit フックを設定しています(`lefthook.yml`)。devcontainer では
`postCreateCommand` が `lefthook install` まで実行するので**追加の操作は不要**です。

devcontainer を使わない場合は、依存インストール後に一度だけ実行してください:

```bash
lefthook install
```

commit 時に、**ステージされた `.py` ファイル**に対して次の順で実行されます:

| 順 | コマンド | 挙動 |
|---|---|---|
| 1 | `ruff check` | 問題があれば commit を中断(自動修正はしない) |
| 2 | `ruff format` | 整形し、変更されたファイルを自動で `git add` し直す(`stage_fixed`) |

対象を「ステージされたファイル」に限っているのは、**今回触っていないファイルの
既存の指摘で commit がブロックされるのを避ける**ためです。リポジトリ全体を
検査したいときは上記の `ruff check src tests` を手で実行してください。

`ruff check` で止められた場合、機械的に直せるものは一括修正できます:

```bash
ruff check --fix src tests
```

フックを一時的に飛ばしたいときは `git commit --no-verify` を使います。

## セキュリティ上の注意

このプロジェクトは公開リポジトリでの運用を想定しています。以下の点に
注意してください。

- **アプリパスワードは最小権限**: Gmail アプリパスワードは通常のログイン
  パスワードとは別物で、そのアプリ(このワークフロー)からの SMTP 送信のみに
  使われます。万一漏洩した場合は
  [Google アカウントのセキュリティ設定](https://myaccount.google.com/security)
  →「アプリ パスワード」から**該当のアプリパスワードを即座に削除(失効)**し、
  新しいものを発行して `GMAIL_APP_PASSWORD` Secret を更新してください
  (Google アカウント自体のパスワードを変える必要はありません)
- **Secrets をログに出さない**: `print()` やデバッグ出力で API キー・
  アプリパスワードなどの Secrets の値をそのまま出力しないでください。
  GitHub Actions は Secrets の値をログ上でマスクしますが、値を加工・分割
  してから出力すると検出をすり抜けてマスクされない場合があります
- **fork からの Pull Request には Secrets が渡らない**: GitHub Actions の
  仕様上、外部からの fork PR では repository Secrets は利用できません
  (意図しない第三者への Secrets 露出を防ぐ既定の安全策です)。自分の
  fork リポジトリ側で Secrets を登録して初めて動作します
- **user.yaml やコードに秘密情報を書かない**: `user.yaml` はコミットされる
  前提のファイルです。宛先メールアドレスや API キーなどはここにも
  `config.yaml` にも書かず、必ず環境変数(GitHub Secrets)側で管理してください
- **ローカルで `.env` を使う場合は `.gitignore` を確認**: `.env` /
  `.env.*` は既に `.gitignore` に含まれていますが、別名のファイルで秘密情報を
  管理する場合は誤コミットしないよう `.gitignore` に追加してください
- **キーの定期ローテーションを推奨**: `ANTHROPIC_API_KEY` と
  `GMAIL_APP_PASSWORD` は、数か月に一度など定期的に再発行し、古いものを
  失効させることを推奨します

## Claude API の料金

本プロジェクトはスコアリング・要約・エディターズノート生成すべてに
`claude-haiku-4-5-20251001` を使用しています。

- **料金の目安(2026年8月時点の確認値)**: 入力 $1 / 100万トークン、
  出力 $5 / 100万トークン。料金は変更される可能性があるため、最新の値は
  必ず [Anthropic の公式 pricing ページ](https://www.anthropic.com/pricing)
  を参照してください
- **想定消費量**: 週1回の実行で、スコアリング(タイトル+アブスト冒頭)と
  要約(上位 `highlight_count` 件)を合わせても数万トークン程度に収まる
  想定です。上記の単価で計算すると、**月あたり数十円規模**のごく小さな
  コストになります
- **利用上限の設定を推奨**: 従量課金であるため、
  [Anthropic Console](https://console.anthropic.com/) の
  Billing 設定で spend limit(利用上限額)を設定しておくことを推奨します。
  想定外の大量実行(例: cron の誤設定や無限リトライ)があっても、
  上限額で自動的に止められます
- **バッチAPI(将来のコスト最適化の余地)**: Claude の Batch API は
  リアルタイム応答が不要な用途向けに約50%の割引を提供しています。
  本プロジェクトは週1回のバッチ的な実行であり Batch API との相性が
  良いため、コストをさらに切り詰めたい場合の選択肢になります。
  v2 時点ではリアルタイム(同期)API のままで、Batch API へは未対応です

## 注意事項

- `data/sent_ids.json` はワークフローが自動更新するため、通常は手動編集しません
  (`--dry-run` では更新されません)
- Gmail のアプリパスワードは Secrets 以外の場所 (コード・ログ・Issue等) に
  絶対に貼り付けないでください
- ローカルの `.venv` や `out/` は `.gitignore` で除外されています
