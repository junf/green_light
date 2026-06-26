# green_light

> 現状の実装（エントリポイント）は `chrome_console_logger.py` で、いまは Chrome の
> コンソール出力の記録に対応しています。

Chrome DevTools のコンソール出力（ログ・警告・エラー・例外）を、テキストファイルへ
自動記録するツール。DevTools を開いていなくても、専用の Chrome を立ち上げている間は
コンソールの内容がファイルに書き出され続ける。

Chrome DevTools Protocol (CDP) を使い、ブラウザにアタッチしてコンソールイベントを
受け取る方式。対象ページのソースに手を入れる必要はない。

## 位置づけ（なぜこれを使うか）

AI に直接ブラウザを操作させる方式（MCP など）の代替ではなく、**「人が手で再現し、
その結果を AI に読ませる」**ためのパッシブなレコーダーです。

- **ツール非依存**: 出力はただのテキスト。どの AI にも貼る/渡すだけで、連携設定が要らない。
- **取りこぼさない**: リロードや遷移をまたいでセッション全体を連続記録（都度クエリ方式のような欠落が無い）。
- **PC をまたげる**: dev と test を別 PC にしても、出力フォルダを同期しておけばファイルが自動で渡る。
- **安全（信頼境界が小さい）**: AI に操作権を渡さず、読み取り専用の成果物だけ渡せる。

逆に、AI が自律的にクリック→リロード→確認…と反復デバッグする用途はライブ制御向き。
本ツールはコンソール中心（ネットワーク本文などは対象外）。ログに機微情報が出る場合があるため、
クラウドの AI へ渡す前に中身を確認してください（削除クリア＋フィルタが「最小限だけ渡す」に役立ちます）。

## 動作環境

- **OS: Windows 専用**（現状）
  - `chrome.exe` の自動検出、コンソールのコードページ設定、画面クリア（`cls`）が
    Windows 前提。macOS / Linux への対応は未実施。
- **Python 3.8 以上**（`python` か `py` がパスにあること）
- **Google Chrome**（一般的な場所にインストールされていれば自動検出。
  見つからなければ `config.json` でフルパス指定）
- Python パッケージ: **`websocket-client`**

## セットアップ

```bat
:: 1) 依存パッケージを入れる
pip install -r requirements.txt

:: 2) 設定ファイルを用意（サンプルをコピーして編集）
copy config.example.json config.json
```

`config.json` は**環境依存のため Git 管理対象外**（`.gitignore` 済み）。
必ず `config.example.json` をコピーして作成し、自分の環境に合わせて書き換えること。
最低限 `output_dir`（ログの出力先）を確認すればよい。

## 使い方

1. `gl.bat` をダブルクリック（または `python chrome_console_logger.py`）
2. 専用プロファイルの Chrome が立ち上がる
3. 記録したいページのアドレスを **自分で入力**して開く
4. **すべてのページ**のコンソール出力が出力先ファイルに記録される
5. 記録を止めるときは、このターミナルで `Ctrl+C`（Chrome は開いたまま）

既定ではフィルタを掛けず全ページを記録する（`filter_enabled: false`）。
起動時メニューも出ず、特定 URL も開かない。

起動時に URL を渡すと、その URL を開いた状態で立ち上がる：

```bat
gl.bat https://example.com/
```

> 同時に2つ以上ロガーを起動すると同じログファイルに二重書き込みになるため、
> 起動し直すときは前のロガーのターミナルを `Ctrl+C` で止めてから。

### コマンドライン引数

`gl.bat`（および `python chrome_console_logger.py`）に渡せる引数：

| 引数 | 説明 |
|------|------|
| `<URL>`（位置引数） | 起動時に開く URL。`config.json` の `start_url` より優先 |
| `--config <名前>` / `-c <名前>` | 使う設定セットを指定。名前（`rose` → `config.rose.json`）でもパスでも可。`default` は `config.json`。詳細は下の「プロジェクト毎に設定を切り替える」参照 |

- `--config=<名前>` / `-c=<名前>` の **等号付き**表記も可。
- URL と `--config` は**併用**できる（順不同）。
- 引数を何も付けなければ、設定セットは対話選択（ダブルクリック時）または `config.json`、URL は `start_url` の値が使われる。

```bat
:: rose 用の設定で、起動時に指定 URL を開く
gl.bat --config rose https://example.com/
```

### 記録中にログファイルを消したら

記録中でも出力ファイルを削除できる（ロガーはファイルを開きっぱなしにしない）。
削除すると、次の出力時に自動で作り直し、先頭に `# === log file (re)created ... ===`
の印を入れて記録を続ける。**このときロガーのターミナル画面も同時にクリアされる。**

ログが溜まりすぎたら、ファイルを消すだけで「ファイルも画面もまっさら」になる。
開始前に出る不要なログ（ログイン画面など）も、目的のページに着いてから削除すれば消える。

### フィルタを使いたいとき（任意）

特定ドメインだけ記録したい場合は `config.json` の `filter_enabled` を `true` にする。
メインフレームの URL に、設定した文字列を含むページだけが記録対象になる
（ログイン画面など別ドメインは除外）。

- `startup_menu: false` … `url_filter_presets` の全 `filter` が同時に有効
- `startup_menu: true` … 起動時メニューで1つだけ選ぶ（候補に `url` があれば自動で開く）

> フィルタ有効時の安全策として、対象外のページを開くとターミナルに
> 「フィルタ対象外なので記録しません: <URL>」と表示される。フィルタの設定忘れで
> 無言のまま記録できていない、という事態を防ぐため。
> 既定でフィルタを切ってあるのも、この「気づけないデータ欠落」を避けるため。

起動時メニューの表示例（`startup_menu: true` のとき）:

```
==================================================
記録するページを選んでください:
  1. Production  [example.com]
  2. Local dev  [localhost]  ←既定
  3. All pages (no filter)  [(全ページ)]
==================================================
番号を入力 (Enter で 2):
```

## 設定（config.json）

| キー | 説明 | 既定 |
|------|------|------|
| `output_dir` | **ログの出力先フォルダ**。相対パスはこのスクリプトのフォルダ基準 | `logs` |
| `log_filename` | ログファイル名 | `console.log` |
| `overwrite` | `true`=起動ごとに上書き / `false`=追記 | `true` |
| `port` | リモートデバッグポート | `9222` |
| `chrome_exe` | Chrome の実行ファイルパス（空なら自動検出） | 空（自動検出） |
| `profile_dir` | デバッグ用 Chrome のプロファイル保存先（空ならこのフォルダ内 `.chrome-debug-profile`） | 空 |
| `start_url` | 起動時に開く URL（コマンドライン引数が優先） | 空 |
| `filter_enabled` | `false`=フィルタ無効（全ページ記録） / `true`=フィルタで絞り込み | `false` |
| `startup_menu` | `false`=メニュー無し（全プリセットのフィルタを同時有効・URLは開かない） / `true`=起動時に1つ選ぶ | `false` |
| `url_filter` | プリセットが空のときの絞り込み文字列（メインフレーム URL に含むページのみ記録） | 空 |
| `url_filter_presets` | 記録対象の候補。`[{ "label": 表示名, "filter": 絞り込み文字列, "url": 開くURL }, ...]`。`url` は `startup_menu: true` で選択時に開く（任意） | 例: Production / Local dev / All |
| `timestamp` | `true` で各行頭に `[HH:MM:SS]` | `false` |
| `stack_for_trace` | `console.trace` のスタックも出す | `true` |

> **Windows のパス指定について**：`config.json` は JSON のため、`\`（円記号 / バックスラッシュ）は
> エスケープ文字として扱われる。Windows の絶対パスを書くときは区切りを **`\\`（2つ重ね）** にすること。
> 対象は `output_dir` / `chrome_exe` / `profile_dir` などパスを取るキー全部。
>
> ```json
> "output_dir":  "C:\\Users\\you\\logs",
> "chrome_exe":  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
> ```
>
> `\\` の代わりに **`/`（スラッシュ）**でも可（`"C:/Users/you/logs"`）。
> `\` を1つだけ書くと JSON として不正になり、起動時に読み込みエラーになる。

### プロジェクト毎に設定を切り替える（任意）

出力先やファイル名などをプロジェクト別に分けたいときは、設定ファイルを
`config.<名前>.json` として複数用意して切り替える。

    :: rose 用の設定を作る（テンプレからコピーして編集）
    copy config.example.json config.rose.json

切り替え方は2通り:

- **コマンドラインで指定**: `gl.bat --config rose`（デフォルトを明示するなら `gl.bat --config default`）
- **ダブルクリックで対話選択**: `gl.bat` をそのまま実行すると、開いたコンソールで
  設定セットの一覧が出る。番号か名前を入力（`ENTER` だけならデフォルトの `config.json`）。

補足:
- `--config` を付けたときは対話を出さない（バッチ/自動化向け）。`--config default` は `config.json`。
- 値は名前（`rose` → `config.rose.json`）でもパス（`C:\path\my.json`）でもよい。
- `--config` で指定したファイルが無いときは、誤った場所への記録を防ぐためエラー終了する
  （`default` は例外で、`config.json` が無くても従来どおり既定値で起動）。
- `config.<名前>.json` は Git 管理対象外（`config.example.json` だけ追跡される）。
- 各設定で `port` と `profile_dir` を別にすれば、複数プロジェクトのロガーを同時に動かせる。

## 仕組み（メモ）

- Chrome を `--remote-debugging-port` + 専用 `--user-data-dir` で起動する
  （Chrome 136 以降、既定プロファイルではリモートデバッグが無効化されるため専用プロファイルを使用）
- 接続側で Origin ヘッダを抑制して CDP に接続（`--remote-allow-origins=*` は付けず、403 origin 拒否を回避）
- CDP にブラウザレベルで1接続し、`Target.setAutoAttach`（flatten）で全ページに自動アタッチ
- `Runtime.consoleAPICalled` / `Runtime.exceptionThrown` / `Log.entryAdded` を受け取り、
  ファイルへ追記する（書き込みのたびに開閉するのでハンドルを保持せず、記録中でも削除可能）

## 注意

- 専用プロファイルのため、普段使いの Chrome のログイン情報・拡張機能は引き継がれない。
  必要なサイトには初回ログインが要る（プロファイルは保存されるので2回目以降は不要）。
- `profile_dir` を同期フォルダ（Drive/Dropbox 等）の中に置くと肥大化・競合の恐れが
  あるため、既定どおりこのプロジェクトフォルダ内に置くのを推奨。
- `config.json` と `.chrome-debug-profile/`、`logs/` は `.gitignore` 済み（コミットされない）。

## セキュリティ

開発者が自分のマシンで使う前提のツールです。設計上のポイント:

- **デバッグポートは localhost のみ**。`--remote-debugging-port` は `127.0.0.1` にバインドされ、
  LAN/外部には公開されません（`--remote-debugging-address` は付けていません）。
- **全オリジン許可はしない**。本ツールは Origin ヘッダ無しで接続するため `--remote-allow-origins=*`
  は不要で、付けていません。これにより Chrome 既定の Origin チェックが有効なまま＝**悪意ある
  Web ページがデバッグポートへ CDP 接続して当ブラウザを操作することを防ぎます**。
- **Chrome のサンドボックスを弱めない**。`--no-sandbox` や `--disable-web-security` は使いません。

運用側で気をつけること:

- 記録中、デバッグ用 Chrome は**同一PC上のローカルプロセスからは操作可能**な状態です。
  共有PCや信頼できない環境では、使い終わったらウィンドウを閉じてください。
- `.chrome-debug-profile/` には**ログインセッション（Cookie/トークン）**が保存されます。
  クラウド同期フォルダに置かない・他者と共有しないこと。
- 出力ログ自体に機微情報（トークン・個人情報など）が混じる場合があります。クラウドの AI へ
  渡す前・出力フォルダを同期する前に中身を確認してください。
- `config.json` の `chrome_exe`（起動する実行ファイル）と URL は信頼できる値に保つこと。

## ライセンス

[MIT License](LICENSE) で公開。自由に利用・改変・再配布できます（無保証）。
