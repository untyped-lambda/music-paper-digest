# 最終受け入れテスト手順書

実環境で本システムが動作することを、段階的に確認するための手順です。
**テスト1 → 5 の順に実施してください。** 各段階は前の段階が通っていることを前提に、
確認範囲を1つずつ広げる構成になっています(問題が起きたとき、どの層が原因か切り分けやすくするため)。

> **実行環境は2通り。以下のどちらで実施するかで、Python の起動コマンドと準備手順が変わります。**
>
> **A. devcontainer 内(VS Code の「Reopen in Container」— 推奨)**
> - コマンドは **`python`**。`py` は Windows 専用ランチャーなのでコンテナには存在しません(`bash: py: command not found` が出たらこれが原因)
> - **venv は作らないでください。** コンテナ起動時に `postCreateCommand` が依存パッケージを
>   コンテナの Python へインストール済みなので、そのまま `python -m src.main ...` を実行できます
> - 最初に `python --version` が `Python 3.14.x` を返すことだけ確認してください
>
> **B. Windows のホスト側で直接実行する場合**
> - **`py` を使ってください。** この PC では `python` コマンドが Microsoft Store のスタブに
>   向いているため動きません(`py --version` が `Python 3.14.6` を返すことを確認)
> - venv を作成し、有効化後のシェル内では `python` がその venv を指すので `python` で構いません
>
> 以下の手順では **A(devcontainer)を基準**に書いています。B の場合は各コマンドの
> `python` を `py` に読み替え、venv 作成手順を追加してください(テスト1に併記)。

---

## テスト1: ローカル dry-run(モックデータ・APIキー不要)

### このテストが確認すること
- パイプライン全体(収集→フィルタ→スコアリング→要約→HTML生成)が、**外部サービスに一切依存せず**最後まで通ること
- 生成されるメールHTMLの見た目(3層構造・日本語要約・リンク)が期待どおりか
- dry-run の冪等性(何度実行しても既読DBを汚さず同じ結果になること)

ここで失敗する場合、問題は**コード自体か Python 環境**にあります。ネットワーク・APIキー・Gmail は無関係です。

### 手順

**A. devcontainer 内(推奨)** — 依存はインストール済みなので、そのまま実行するだけです。

```bash
python --version
python -m src.main --dry-run
```

**B. Windows ホストで実行する場合** — venv の作成が必要です。

```powershell
cd C:\Users\Ygg\Documents\music-paper-digest
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m src.main --dry-run
```

どちらの場合も、続けて **もう一度** `python -m src.main --dry-run` を実行してください(冪等性の確認)。

### 期待結果
- 2回とも exit 0(エラーなく終了)
- `out/preview.html` が生成される → ブラウザで開き、①概観 ②分野別ハイライト(要約付き) ③その他タイトル一覧 の3層が表示されること
- 2回目も1回目と**同じ件数**が処理される(「既読のためスキップ」で0件にならない)
- `data/sent_ids.json` の中身が `{"sent": {}}` のまま変わっていない

### エラー時に見る場所・疑うところ
| 症状 | 疑わしい箇所 |
|---|---|
| `py: command not found`(コンテナ内) | `py` は Windows 専用ランチャー。コンテナでは `python` を使う |
| `SyntaxError` | Python 3.13 以前で実行している。本コードは 3.14 専用の構文(PEP 758)を含む。`python --version`(ホストなら `py --version`)を確認 |
| `ModuleNotFoundError` | コンテナ内なら `postCreateCommand` が失敗した可能性 → VS Code の出力パネル「Dev Containers」ログを確認し、手動で `pip install -r requirements.txt -r requirements-dev.txt`。ホストなら venv 未有効化か `pip install` 忘れ(プロンプト先頭に `(.venv)` が出ているか確認) |
| `FileNotFoundError`(fixtures) | カレントディレクトリがリポジトリ直下でない。`cd` し直す |
| 2回目が0件になる | dry-run の冪等化(main.py の mark_sent スキップ)が壊れている。`data/sent_ids.json` にIDが書き込まれていないか確認 |
| HTMLの表示崩れ | `src/render.py` の問題。`out/preview.html` をエディタで開き、壊れているセクション(概観/ハイライト/その他)からどの生成部か特定 |

---

## テスト2: ローカル本番実行(実API・実メール送信)

### このテストが確認すること
- **OpenAlex / arXiv の実APIから今週の論文を取得できること**(API仕様変更・ネットワークの検証)
- **Claude API の認証と実際のスコアリング・日本語要約**(APIキー有効性・課金設定の検証)
- **Gmail SMTP の認証と送信**(アプリパスワードの検証)
- 既読DB(`data/sent_ids.json`)への書き込み

GitHub Actions に載せる前に、同じ処理を手元で実行して外部サービスとの接続を1つずつ検証するのが目的です。
**注意: 実際に自分宛てにメールが1通届き、Claude API に数円程度の課金が発生します。**

### 手順

> ⚠️ **環境変数の設定構文はシェルによって違います。**
> `$env:VAR = "..."` は **PowerShell 専用**で、devcontainer 内の bash では使えません
> (`bash: :VAR: command not found` になります)。bash では `export` を使ってください。
>
> ⚠️ **APIキー・アプリパスワードの取り扱い**
> - **キーをチャット・Issue・スクリーンショット等に貼り付けないこと。** 貼ってしまった場合は
>   漏洩として扱い、直ちに失効・再発行してください(Anthropic: console.anthropic.com の
>   API Keys で Revoke / Google: アカウントのアプリパスワード一覧で削除)
> - 下記の `read -rs` を使う方法なら**画面にもシェル履歴にも残りません**。コマンド行に直接
>   キーを書く方法(`export KEY="sk-ant-..."`)は履歴ファイルに平文で残るため非推奨です

**A. devcontainer 内(bash)**

```bash
read -rs -p "ANTHROPIC_API_KEY: " ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY
export GMAIL_ADDRESS="あなたのGmailアドレス"
read -rs -p "GMAIL_APP_PASSWORD: " GMAIL_APP_PASSWORD && export GMAIL_APP_PASSWORD
python -m src.main --verbose
```

設定できたかは、**値ではなく文字数**で確認すると安全です(3つとも 0 以外、アプリパスワードは 16):

```bash
echo "${#ANTHROPIC_API_KEY} ${#GMAIL_ADDRESS} ${#GMAIL_APP_PASSWORD}"
```

**B. Windows ホスト(PowerShell、venv 有効化済みのシェルで)**

```powershell
$env:ANTHROPIC_API_KEY = (Read-Host "ANTHROPIC_API_KEY" -MaskInput)
$env:GMAIL_ADDRESS = "あなたのGmailアドレス"
$env:GMAIL_APP_PASSWORD = (Read-Host "GMAIL_APP_PASSWORD" -MaskInput)
python -m src.main --verbose
```

環境変数はシェルを閉じると消えます(意図した挙動です)。テストをやり直すたびに再設定してください。

### 期待結果
- exit 0 で終了し、ログに「取得件数 → フィルタ後 → ハイライトN件」の流れが出る
- **untyped.lambda@gmail.com(= GMAIL_ADDRESS)にダイジェストメールが届く**
- `data/sent_ids.json` に送信した論文のIDが記録される

### エラー時に見る場所・疑うところ
エラーのスタックトレースに出る**モジュール名で層を切り分け**られます:

| エラー発生箇所 | 疑わしいもの |
|---|---|
| `src/fetch.py`(HTTPエラー・タイムアウト) | ネットワーク、OpenAlex/arXiv 側の一時障害または仕様変更。時間を置いて再試行。恒常的なら `config.yaml` の `sources` 設定と API レスポンス形式を疑う |
| `src/rank.py` / `src/summarize.py`(401/403) | `ANTHROPIC_API_KEY` の値ミス・失効。console.anthropic.com でキーの状態とクレジット残高を確認 |
| 同上(429) | Claude API のレート制限。通常は自動リトライされるが、続くなら時間を置く |
| `src/send.py` の `SMTPAuthenticationError` | アプリパスワードの誤り。**表示時の4文字区切りスペースを除いた16文字**か、Googleアカウントの2段階認証が有効か、アプリパスワードが失効していないかを確認 |
| メールは「送信成功」ログが出るのに届かない | **迷惑メールフォルダを確認**(初回はここに入りやすい)。それでも無ければ GMAIL_ADDRESS のタイポ |
| 「新規論文がありません」で終わる | `data/sent_ids.json` に前回実行分が入っている(正常動作)。全件取り直したい場合は中身を `{"sent": {}}` に戻す |
| 環境変数エラー(RuntimeError)で即終了 | 意図した動作(フェイルファスト)。メッセージに出ている環境変数名を設定する |

### テスト後の注意
このテストで `data/sent_ids.json` に既読が書き込まれます。**そのまま GitHub に push すれば、
本番初回に同じ論文が重複送信されるのを防げます**(推奨)。逆に「本番初回でフルのダイジェストを
もう一度受け取りたい」場合は push 前に `{"sent": {}}` に戻してください。

---

## テスト3: GitHub Actions 手動実行

### このテストが確認すること
- **本番と同一の環境**(GitHub のUbuntuランナー + Python 3.14 + Secrets注入)でパイプラインが動くこと
- Secrets の登録名・値が正しいこと
- 実行後に `data/sent_ids.json` が **github-actions[bot] によって自動コミット**されること(既読の永続化)

ローカル(テスト2)と本番(毎週月曜)の間の最後のギャップ — 「CI環境固有の問題」を検証します。

### 手順
1. GitHub にリポジトリを作成し、プロジェクト一式を push(READMEのセットアップ手順参照)
2. リポジトリの Settings → Secrets and variables → Actions で登録:
   `ANTHROPIC_API_KEY` / `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD`(`DIGEST_RECIPIENT` は宛先を変えたい場合のみ)
3. Actions タブ → 左の「**weekly-digest**」→ 「Run workflow」で手動実行
4. 実行ログを開き、各ステップの結果を確認

### 期待結果
- ワークフローが緑(成功)になる
- メールが届く(テスト2直後なら新着分のみ。0件なら「新規論文なし」で正常終了し、メールが来ないか空に近い内容になる — これも正常)
- リポジトリのコミット履歴に github-actions[bot] による `data/sent_ids.json` 更新コミットが積まれる(新規論文が0件だった場合はコミットされないこともある)

### エラー時に見る場所・疑うところ
**まず Actions → 失敗した run → 赤いステップのログ**を開くこと。そこに出るエラーはテスト2の表と同じ切り分けが使えます。CI固有の問題は次のとおり:

| 症状 | 疑わしいもの |
|---|---|
| Actions タブにワークフローが表示されない | `.github/workflows/digest.yml` が push されていない、またはデフォルトブランチ以外に push した |
| 環境変数の RuntimeError で失敗 | **Secrets の登録名のタイポ**(名前は完全一致が必要)。値の前後の空白混入も疑う |
| ローカルでは通ったのに CI でだけ失敗 | ステップ「Set up Python」のバージョン表示が 3.14 か確認。依存インストールのログにエラーがないか確認 |
| sent_ids.json のコミットステップで失敗 | ワークフローの `permissions: contents: write` が効いているか、ブランチ保護ルールで bot の push が弾かれていないか |

---

## テスト4: 失敗通知メール

### このテストが確認すること
- 本番実行が失敗したとき、**「【音楽論文ダイジェスト】実行失敗」メールが届く**こと(サイレント故障の防止機構)
- 通知メール内の GitHub Actions 実行ログへのリンクが正しいこと

この仕組みが動かないと、「毎週のメールが来ないことに数週間気づかない」事故を検知できません。**意図的に失敗させて**検証します。

### 手順
1. GitHub の Secrets で `ANTHROPIC_API_KEY` を一時的に無効な値(例: `invalid`)に変更
2. Actions から weekly-digest を手動実行 → 失敗するはず
3. **終わったら必ず正しいAPIキーに戻す**
4. 戻した後にもう一度手動実行し、成功に戻ることを確認

### 期待結果
- run は失敗(赤)になるが、「Notify failure」ステップは実行され、失敗通知メールが届く
- メール内のリンクから該当 run のログに飛べる

### エラー時に見る場所・疑うところ
| 症状 | 疑わしいもの |
|---|---|
| 失敗通知メール自体が来ない | 通知も Gmail で送るため、**GMAIL 系 Secrets が壊れていると本体も通知も両方失敗**する(このテストでは ANTHROPIC_API_KEY だけを壊すのはそのため)。「Notify failure」ステップのログで SMTP エラーを確認 |
| 「Notify failure」ステップが実行されていない | ワークフローの `if: failure()` 条件と、そのステップへの env 渡しを確認 |

---

## テスト5: スケジュール実行(受動確認)

### このテストが確認すること
- cron スケジュール(`0 23 * * 0` UTC = **月曜 8:00 JST**)による自動起動 — 手動実行では検証できない最後の1点

### 手順
何もしません。**次の月曜の朝にメールが届くのを待ちます。**

### 期待結果
- 月曜 8:00 JST 頃にダイジェストが届く(GitHub Actions の cron は数分〜数十分遅延することがあるため、9時までは正常範囲)

### 届かなかった場合に見る場所・疑うところ
1. **Actions タブの実行履歴**を確認:
   - **run 自体が存在しない** → スケジュールが発火していない。パブリック/プライベート問わず、**リポジトリに約60日間コミット等の活動がないと GitHub が schedule を自動無効化**する仕様がある(Actionsタブに警告が出る。「Enable workflow」で再開)。作った直後なら、cron 記述とデフォルトブランチを確認
   - **run はあるが失敗(赤)** → 失敗通知メールが来ているはず。テスト3・4の切り分け表へ
   - **run は成功(緑)なのにメールが無い** → 迷惑メールフォルダ。次に「新規論文0件」だった可能性(runログで件数を確認)
2. それでも不明なら、ローカルでテスト2を再実行して切り分け(ローカルで通る → GitHub側の問題、ローカルでも落ちる → 外部API・キーの問題)

---

## 補足

- **チューニング**: ハイライト件数や件名などは `user.yaml`(`user.yaml.example` をコピー)で上書きできます。変更後は push すれば次回実行から反映されます
- **コスト監視**: console.anthropic.com の Usage で消費を確認できます。想定は月数十円規模。心配なら Spend limit を設定してください
- **完全に動作しなくなったときの最終切り分け**: ①ローカル dry-run(テスト1)→ コードの問題か / ②ローカル本番(テスト2)→ 外部サービスの問題か / ③Actions 手動(テスト3)→ CI固有の問題か、の順に戻って実行すると原因の層が特定できます
