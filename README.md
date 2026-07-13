# green_light

**[English](README.en.md)** | 日本語

**Chrome DevTools のコンソール出力（ログ・警告・エラー・例外）を、テキストファイルへ自動記録する
ツール。記録対象は2つ — この PC の Chrome と、USB 接続した Android 端末の Chrome。**

DevTools を開いていなくても、専用の Chrome を立ち上げている間（または対象端末を USB で繋いでいる間）は、
コンソールの内容がファイルに書き出され続ける。Chrome DevTools Protocol (CDP) でブラウザにアタッチして
コンソールイベントを受け取る方式なので、対象ページのソースには一切手を入れない。

### 📱 USB 接続した Android 端末の Chrome も記録できる

これは PC 向けだけのロガーではない。**USB 接続したスマホ実機の Chrome コンソールを、そのまま
PC 側のテキストファイルに連続記録できる**のが大きな特徴。`chrome://inspect` を開いて DevTools の
出力を手でコピペする必要はなく、モバイルのコンソール（ログ・例外・ネットワークエラー等）が、
実機の画面遷移・リロードをまたいでファイルへ流れ続ける。結果として **スマホ実機のデバッグログを、
コピペ無しでそのまま AI に渡せる**。手順は「[Android 端末の Chrome を記録する](#android-端末の-chrome-を記録するusb--cdp-over-adb)」節を参照。

> 現状の実装（エントリポイント）は `chrome_console_logger.py`。

## 位置づけ（なぜこれを使うか）

AI に直接ブラウザを操作させる方式（MCP など）の代替ではなく、**「人が手で再現し、
その結果を AI に読ませる」**ためのパッシブなレコーダーです。

- **ツール非依存**: 出力はただのテキスト。どの AI にも貼る/渡すだけで、連携設定が要らない。
- **取りこぼさない**: リロードや遷移をまたいでセッション全体を連続記録（都度クエリ方式のような欠落が無い）。
- **PC をまたげる**: dev と test を別 PC にしても、出力フォルダを同期しておけばファイルが自動で渡る。
- **安全（信頼境界が小さい）**: AI に操作権を渡さず、読み取り専用の成果物だけ渡せる。
- **モバイルも対象**: USB 接続した Android 端末の Chrome コンソールも同じ仕組みで記録でき、スマホ実機のデバッグログを手コピペ無しで取り出せる。

逆に、AI が自律的にクリック→リロード→確認…と反復デバッグする用途はライブ制御向き。
本ツールはコンソール中心（ネットワーク本文などは対象外）。ログに機微情報が出る場合があるため、
クラウドの AI へ渡す前に中身を確認してください（削除クリア＋フィルタが「最小限だけ渡す」に役立ちます）。

## 動作環境

- **OS: Windows / macOS / Linux**
  - Chrome の自動検出・コンソールのコードページ設定・画面クリアは OS ごとに出し分け済み。
  - 起動は Windows が `glog.bat`、macOS / Linux は `glog.sh`（または `python chrome_console_logger.py ...` を直接実行）。
- **Python 3.8 以上**
  - Windows: `python` か `py` がパスにあること。
  - macOS / Linux: `python3` がパスにあること（macOS 標準の 3.9 でも動く）。
- **Google Chrome**（一般的な場所にインストールされていれば自動検出。
  見つからなければ `config.json` の `chrome_exe` でフルパス指定）
  - 自動検出する既定パス: Windows は `Program Files` 等の `chrome.exe`、
    macOS は `/Applications/Google Chrome.app`（`~/Applications/...` も）、
    Linux は `PATH` 上の `google-chrome` / `chromium` 等。
- Python パッケージ: **`websocket-client`**
- （Android 端末の記録を使う場合のみ）**adb（Android SDK Platform-Tools）**。
  インストール方法は「Android 端末の Chrome を記録する」節を参照
- （iPhone / iPad の記録を使う場合のみ）**`pymobiledevice3`**（`pip install -r requirements-ios.txt`）。
  root / sudo は不要。詳細は「iPhone / iPad の Safari を記録する」節を参照

## セットアップ

Windows（コマンドプロンプト）:

```bat
:: 1) 依存パッケージを入れる
pip install -r requirements.txt

:: 2) 設定ファイルを用意（サンプルをコピーして編集）
copy config.example.json config.json
```

macOS / Linux（ターミナル）:

```sh
# 1) 依存パッケージを入れる（venv 推奨）
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) 設定ファイルを用意（サンプルをコピーして編集）
cp config.example.json config.json
```

`config.json` は**環境依存のため Git 管理対象外**（`.gitignore` 済み）。
必ず `config.example.json` をコピーして作成し、自分の環境に合わせて書き換えること。
最低限 `output_dir`（ログの出力先）を確認すればよい。

## 使い方

1. 起動する（**Windows は `glog.bat`**、**macOS / Linux は `./glog.sh`**）。
   Windows はダブルクリックでも起動でき、ターミナルからなら **URL や `--config` を引数で渡せる**（下記「起動例」）。
   macOS / Linux は `./glog.sh ...` か `python chrome_console_logger.py ...` を直接実行する。
2. 専用プロファイルの Chrome が立ち上がる
3. 記録したいページのアドレスを **自分で入力**して開く
4. **すべてのページ**のコンソール出力が出力先ファイルに記録される
5. 記録を止めるときは、このターミナルで `Ctrl+C`（Chrome は開いたまま）

> ℹ️ **`Ctrl+C` 後も debug Chrome は意図的に開いたまま**にする（作業を続けられるし、次回起動時は
> その Chrome にアタッチする）。**macOS では全ウィンドウを閉じてもプロセスが終了せず**、
> リモートデバッグポート（既定 9222）を掴んだまま常駐する（Windows は最後のウィンドウを
> 閉じれば終了する）。ウィンドウが見えないのにポートが塞がっているときはこれ。使い終わったら
> その debug Chrome を **Cmd+Q** で終了させること（デバッグ可能な Chrome を常駐させ続けないため）。

既定ではフィルタを掛けず全ページを記録する（`filter_enabled: false`）。
起動時メニューも出ず、特定 URL も開かない。

起動例（Windows はダブルクリックでも、ターミナルから引数付きでも起動できる）：

```bat
glog.bat
glog.bat https://example.com/
glog.bat --config myapp
glog.bat --config myapp https://example.com/
```

macOS / Linux は `glog.sh` に同じ引数を渡す：

```sh
./glog.sh
./glog.sh https://example.com/
./glog.sh --config myapp
./glog.sh --config myapp https://example.com/
```

- 引数なし … 既定の `config.json` で記録（ダブルクリックと同じ）
- `<URL>` … 起動時にその URL を開く（`config.json` の `start_url` より優先）
- `--config <名前>` … 設定セット `config.<名前>.json` を使う
- URL と `--config` は**併用**できる（順不同）

各引数の詳細は下の「コマンドライン引数」を参照。

> 同時に2つ以上ロガーを起動すると同じログファイルに二重書き込みになるため、
> 起動し直すときは前のロガーのターミナルを `Ctrl+C` で止めてから。

### コマンドライン引数

`glog.bat` に渡せる引数：

| 引数 | 説明 |
|------|------|
| `<URL>`（位置引数） | 起動時に開く URL。`config.json` の `start_url` より優先 |
| `--config <名前>` / `-c <名前>` | 使う設定セットを指定。名前（`myapp` → `config.myapp.json`）でもパスでも可。`default` は `config.json`。詳細は下の「プロジェクト毎に設定を切り替える」参照 |

- `--config=<名前>` / `-c=<名前>` の **等号付き**表記も可。
- URL と `--config` は**併用**できる（順不同）。
- 引数を何も付けなければ、設定セットは対話選択（ダブルクリック時）または `config.json`、URL は `start_url` の値が使われる。

```bat
:: myapp 用の設定で、起動時に指定 URL を開く
glog.bat --config myapp https://example.com/
```

### 記録中にログファイルを消したら

記録中でも出力ファイルを削除できる（ロガーはファイルを開きっぱなしにしない）。
削除すると、次の出力時に自動で作り直し、先頭に `# === log file (re)created ... ===`
の印を入れて記録を続ける。**このときロガーのターミナル画面も同時にクリアされる。**

ログが溜まりすぎたら、ファイルを消すだけで「ファイルも画面もまっさら」になる。
開始前に出る不要なログ（ログイン画面など）も、目的のページに着いてから削除すれば消える。

### フィルタを使いたいとき（任意）

特定ドメインだけ記録したい場合のメインスイッチが `filter_enabled`。まずここを切り替える。

- **`filter_enabled: false`（既定）** … フィルタ無効。**全ページ**を記録する。起動メニューも
  出さず、URL は `start_url` / コマンドライン引数で開く。
- **`filter_enabled: true`** … フィルタ有効。メインフレームの URL に、設定した文字列を含む
  ページ**だけ**が記録対象になる（ログイン画面など別ドメインは除外）。

> ⚠ **`filter_enabled: true` にしたら、記録したいサイトを必ず `url_filter_presets`
> （単一サイトなら `url_filter`）に指定すること。** 指定が空のままだと「フィルタ設定なし」の
> 警告が出て **全ページ記録にフォールバック**し、絞り込みの意味がなくなる（＝意図せぬフィルタ漏れ）。
> 記録対象は各 preset の `filter`（メインフレーム URL に含む文字列）で判定され、
> **`url_filter_presets` が優先・`url_filter` はフォールバック**。ログイン/認証フローが複数
> ドメインにまたがる場合は、必要なドメインを preset に並べておく。

`filter_enabled: true` のとき、複数のフィルタ候補を**どう適用するか**を決めるのが `filter_menu`
（`filter_enabled: false` のときは無視され、メニューも出ない）:

- **`filter_menu: false`（既定）** … `url_filter_presets` の全 `filter` が同時に有効
- **`filter_menu: true`** … 起動時メニューで1つだけ選ぶ（その候補に `url` があれば自動で開く）

> フィルタ有効時の安全策として、対象外のページを開くとターミナルに
> `[info] Not recording (no filter match; ...)` と表示される（画面表示は英語）。
> フィルタの設定忘れで無言のまま記録できていない、という事態を防ぐため。
> 既定でフィルタを切ってあるのも、この「気づけないデータ欠落」を避けるため。

フィルタ選択メニューの表示例（`filter_enabled: true` かつ `filter_menu: true` のとき。画面表示は英語）:

```
==================================================
Select which pages to record:
  1. Production  [example.com]
  2. Local dev  [localhost]
  3. All pages (no filter)  [(all pages)]  <- default
==================================================
Enter a number (Enter = 3):
```

（`<- default` は `url_filter` に一致するプリセット。上は `url_filter: ""` なので「All pages」が既定）

## 設定（config.json）

| キー | 説明 | 既定 |
|------|------|------|
| `output_dir` | **ログの出力先フォルダ**。相対パスはこのスクリプトのフォルダ基準。先頭の `~` はホームに展開（macOS / Linux） | `logs` |
| `log_filename` | ログファイル名 | `console.log` |
| `overwrite` | `true`=起動ごとに上書き / `false`=追記 | `true` |
| `port` | リモートデバッグポート | `9222` |
| `chrome_exe` | Chrome の実行ファイルパス（空なら自動検出） | 空（自動検出） |
| `profile_dir` | デバッグ用 Chrome のプロファイル保存先（空ならこのフォルダ内 `.chrome-debug-profile`） | 空 |
| `source` | 記録対象。`desktop`=この PC の Chrome を起動して記録 / `android`=USB 接続端末の Chrome を記録（後述） / `safari`=この Mac の Safari を記録（後述・macOS 専用） / `ios`=USB 接続した iPhone・iPad の Safari を記録（後述） | `desktop` |
| `adb_path` | `source: android` 時の adb のパス（空なら PATH と一般的な SDK の場所から自動検出） | 空 |
| `device_serial` | 対象端末の識別子。`source: android` は adb の serial、`source: ios` は端末の UDID（空なら唯一接続されている端末） | 空 |
| `safaridriver_path` | `source: safari` 時の safaridriver のパス（空なら PATH から自動検出。通常 macOS 同梱で指定不要） | 空 |
| `start_url` | 起動時に開く URL（コマンドライン引数が優先） | 空 |
| `filter_enabled` | `false`=フィルタ無効（全ページ記録） / `true`=フィルタで絞り込み | `false` |
| `filter_menu` | （`filter_enabled: true` のときのみ）`false`=メニュー無し（全プリセットのフィルタを同時有効・URLは開かない） / `true`=起動時にフィルタを1つ選ぶ（その `url` を開く） | `false` |
| `url_filter` | プリセットが空のときの絞り込み文字列（メインフレーム URL に含むページのみ記録） | 空 |
| `url_filter_presets` | 記録対象の候補。`[{ "label": 表示名, "filter": 絞り込み文字列, "url": 開くURL }, ...]`。`url` は `filter_menu: true` で選択時に開く（任意） | 例: Production / Local dev / All |
| `redact_patterns` | **機微情報のマスキング（任意）**。正規表現のリスト。一致部分を `***` に置換して記録する。**ベストエフォートであり保証ではない**（後述） | `[]`（無効） |
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

    :: myapp 用の設定を作る（テンプレからコピーして編集）
    copy config.example.json config.myapp.json

切り替え方は2通り:

- **コマンドラインで指定**: `glog.bat --config myapp`（デフォルトを明示するなら `glog.bat --config default`）
- **ダブルクリックで対話選択**: `glog.bat` をそのまま実行すると、開いたコンソールで
  設定セットの一覧が出る。番号か名前を入力（`ENTER` だけならデフォルトの `config.json`）。

補足:
- `--config` を付けたときは対話を出さない（バッチ/自動化向け）。`--config default` は `config.json`。
- 値は名前（`myapp` → `config.myapp.json`）でもパス（`C:\path\my.json`）でもよい。
- `--config` で指定したファイルが無いときは、誤った場所への記録を防ぐためエラー終了する
  （`default` は例外で、`config.json` が無くても従来どおり既定値で起動）。
- `config.<名前>.json` は Git 管理対象外（`config.example.json` だけ追跡される）。
- 各設定で `port` と `profile_dir` を別にすれば、複数プロジェクトのロガーを同時に動かせる。

## Android 端末の Chrome を記録する（USB / CDP over ADB）

`chrome://inspect` を開いて手でコピペする代わりに、USB 接続した Android 端末の
Chrome コンソールを PC 版と同じ仕組みでファイルに連続記録できる。仕組みは
`adb forward` で端末の DevTools を localhost に橋渡しし、あとは通常どおり CDP で
アタッチするだけ（端末側のソースには手を入れない）。実機の遷移・リロードを
またいで連続記録でき、AI には出力ファイルを渡すだけ＝**スマホ実機のデバッグログを
手コピペ無しで取り出せる**。

### 1. adb（Android SDK Platform-Tools）を用意する

Android の記録には **adb**（Android Debug Bridge）が必要。adb は Android SDK の
**Platform-Tools** に含まれる。

- **まず入っているか確認**: PowerShell かコマンドプロンプトで `adb version`。
  バージョンが表示されれば導入済み（Android Studio・Flutter・React Native などの
  モバイル開発環境を入れていれば、たいてい既に入っている）。
- **入っていなければインストール**（いずれか）:
  - 公式の「**SDK Platform-Tools**」を Google からダウンロードして展開し、`adb.exe`
    のあるフォルダを **PATH に追加**する（developer.android.com の Platform-Tools 配布物。
    Android Studio 全体を入れなくても、この zip 単体で使える）。
  - パッケージマネージャでも可（例: `scoop install adb` / `choco install adb`）。
  - Android Studio を入れる場合は通常 `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`。
- **PATH に通さない場合**は、Android 用 config の `adb_path` に `adb.exe` のフルパスを
  指定すればよい（空なら PATH と一般的な SDK の場所から自動検出）。

`adb version` でバージョンが出れば準備の第一段階は完了。

### 2. 端末側の準備（USB デバッグ）

- 端末で **開発者オプション → USB デバッグを ON**。初回接続時に端末へ出る
  **認証ダイアログを「許可」**（`adb devices` で `device`＝OK ／ `unauthorized`＝未許可）。
- 記録したい **Chrome を端末で開いておく**。

### 3. 設定（`config.android.json` を作る例）

```jsonc
{
  "source": "android",
  "port": 9333,                 // ← デスクトップ版の 9222 と必ず分ける（後述）
  "adb_path": "",               // 空 = 自動検出
  "device_serial": "",          // 空 = 唯一の端末 / 複数なら adb devices の serial
  "output_dir": "logs-android",
  "log_filename": "console.log",
  "filter_enabled": true,       // ← Android では ON を強く推奨（下記）
  "url_filter_presets": [       // ← 記録したいサイトをここに必ず列挙（filter = URL に含む文字列）
    { "label": "my app", "filter": "example.com",          "url": "https://example.com/" },
    { "label": "auth",   "filter": "accounts.example.com", "url": "" }
  ]
}
```

> ⚠ **Android では `filter_enabled: true` を強く推奨**。デスクトップは自分専用の
> プロファイルを起動するが、Android は**自分の実機 Chrome**に繋ぐため、個人利用のタブ
> （ネット銀行・通販・SNS）や**各種サービスへのログイン（認証セッション）**が同じ
> Chrome に同居していることが多い。フィルタ無効（全ページ記録）のままだと、それらの
> console まで記録され、特に `output_dir` を同期フォルダにするとクラウドへ流出しかねない。
>
> **`filter_enabled: true` にしたら、記録したいサイトを必ず `url_filter_presets`
> （単一サイトなら `url_filter`）に指定する**こと。指定が空のままだと「フィルタ設定なし」
> の警告が出て**全ページ記録にフォールバック**し、絞り込みの意味がなくなる。記録対象は
> 各 preset の `filter`（メインフレーム URL に含む文字列）で判定され、`url_filter_presets`
> が優先・`url_filter` はフォールバック。ログイン/認証フローが**複数ドメインにまたがる**
> 場合は、必要なドメインを preset に並べておく。クラウドの AI へ渡す前にも中身を確認する。

### 4. 実行

```bat
glog.bat --config android
```

→ adb で端末の DevTools を `localhost:<port>` に転送し、Android Chrome に
アタッチして記録を開始する。停止は `Ctrl+C`（終了時に転送も自動で解除）。

> ℹ️ `Ctrl+C` 以外（ターミナルを閉じる等）で落ちると `adb forward` が残ることがある（localhost のみで
> 実害は小さく、次回は "port in use" で気づける）。気になれば `adb forward --remove-all` で消せる。

> ℹ️ 端末は**ロック中や設定アプリを開いている間は `offline` 扱い**になる。起動時に端末が
> オンラインでなければ「画面ロックを解除してください」と促して**自動で待機**し、
> オンラインになり次第そのまま記録を開始する（`adb devices` で `device` になる状態）。

> ℹ️ アタッチした瞬間、各タブが**それまで溜めていた console をまとめて再送**するため、
> 接続直後はログが一気に出る（Chrome の仕様。DevTools を後から開くと過去ログが見えるのと同じ）。
> 重複やノイズが気になるときは、端末で**不要なタブを閉じる**、または接続後に
> **ログファイルを削除**すれば「今から」だけにできる。

> ⚠ **ポートはデスクトップ版と分けること**。`port` が既に使われていると
> `adb forward` は失敗する。本ツールは**失敗を握りつぶさずエラー終了**し、さらに
> 接続先が本当に端末か（Android Chrome か）を検証する。これは、別の Chrome が
> 同じポートを使っているときに**誤って PC の Chrome へ繋いでしまう事故**を防ぐため。

## Mac の Safari を記録する（macOS / WebDriver BiDi）【実験的】

> ⚠️ **この機能は実験段階です。** Safari の WebDriver BiDi は本稿執筆時点で**実験扱い**で、
> 内部で未公開のキャパビリティ `safari:experimentalWebSocketUrl` を要求している（これが無いと
> BiDi の WebSocket URL が返らない）。**Safari 26.2 で動作確認済み**だが、この解錠方法は Apple の
> 公式ドキュメントに記載が無く、Safari のバージョン更新で名称・挙動が変わる／使えなくなる可能性が
> ある。うまく繋がらない場合はまず Safari のバージョンと「リモートオートメーション」設定を確認すること。

この Mac の **Safari** のコンソールも記録できる（`source: safari`）。Safari は CDP を
話さないため、Chrome 系とは別経路（macOS 同梱の `safaridriver` + **WebDriver BiDi**）で
コンソール／未捕捉例外を受け取り、同じテキストファイルに追記する。

### 1. 一度だけ「リモートオートメーション」を許可する

Safari を自動化から制御するには、**一度だけ**次のいずれかを実施する（管理者権限が要る）：

```sh
sudo safaridriver --enable
```

または **Safari > 設定 > 詳細 で「メニューバーに"開発"メニューを表示」を有効化 →
Safari > 開発 > 「リモートオートメーションを許可」にチェック**。

> ⚠ 本ツールは `safaridriver --enable`（要 sudo）を**自分では実行しない**。上記は利用者が
> 一度だけ手で行う前提。これはセキュリティ上の昇格操作なので、意図せず有効化しないため。

### 2. 設定して起動する

`config.safari.json` の例（`start_url` は**必須**。理由は下記「最大の制限」）：

```json
{
  "output_dir": "logs",
  "source": "safari",
  "start_url": "https://example.com/",
  "timestamp": true
}
```

```sh
./glog.sh --config safari https://example.com/   # 記録したいページを指定して起動
```

自動化用の Safari ウィンドウが開き（「Safari は自動テストによって制御されています」の表示）、
指定した URL が読み込まれ、そのページのコンソール出力を記録する。停止は `Ctrl+C`（他ソースと同じ）。

### ⚠ 最大の制限：自動化ウィンドウは手動操作できない

Safari は自動化ウィンドウの上に **「グラスペイン」** と呼ばれる透明な膜をかぶせ、
**マウス・キーボード操作を遮断する**（WebKit 公式の設計：
[WebDriver Support in Safari 10](https://webkit.org/blog/6900/webdriver-support-in-safari-10/) —
*"Safari installs a 'glass pane' over the Automation window while the test is running. This blocks
any stray interactions (mouse, keyboard, resizing, and so on)"*）。
無理に操作するとダイアログが出て、そこで「セッションを停止」を選ぶと
**WebDriver セッションが切断され、記録も終了する**。

したがって `source: safari` では、Chrome 版のように
**「人間がブラウザを手で操作して不具合を再現し、そのログを記録する」ことはできない**。
記録できるのは `start_url` で開いたページの読み込み時以降に出るログ（自動で発生する
コンソール出力・未捕捉例外など）に限られる。

> 手動操作しながらの記録は Chrome（`source: desktop` / `android`）を使うこと。
> Safari でも手動操作を可能にするには、WebDriver 以外の経路（Safari 拡張による
> コンソールのフック等）が必要で、これは**将来対応の課題**。

### Safari 版のその他の制限（Chrome 版との差）

- **URL フィルタは無効**：Safari の BiDi ログにはページの識別情報が乗らないため、
  `url_filter` / プリセットは `source: safari` では無視され、**全ページ**を記録する。
- **行番号プレフィックスが付かない**：Safari はコンソール行にソース位置を付けないため、
  Chrome 版のような `file.js:12` の接頭辞は出ない（メッセージ本文はそのまま記録される）。
- **ログイン状態を引き継がない**：自動化ウィンドウは通常の Safari とは別プロファイル。
- **macOS 専用**：`safaridriver` は macOS 同梱。Windows / Linux では使えない。
- WebDriver BiDi は現状 Safari では実験扱いのため、内部で
  `safari:experimentalWebSocketUrl` を要求している。

## iPhone / iPad の Safari を記録する（USB / pymobiledevice3）

USB 接続した **iOS 実機の Safari** のコンソールも記録できる（`source: ios`）。Android 版と同じ発想で、
**端末を手に持って操作しながら、そのコンソール出力が PC 側のテキストファイルに流れ続ける**。
Mac の Safari と違い**手動操作の制約は無い**（自動化ではなく Web Inspector に接続するため）。

**root / sudo も tunnel も不要**。macOS / Windows どちらのホストでも同じコードで動く（開発は macOS で検証）。

### 1. 依存を入れる

```sh
pip install -r requirements-ios.txt      # pymobiledevice3（ios ソース専用。他の用途では不要）
```

### 2. 端末側の準備

1. **USB 接続**し、端末で「このコンピュータを信頼しますか？」→ **信頼**（要ロック解除）
2. **設定 → アプリ → Safari → 詳細 → 「Web インスペクタ」を ON**
   （iOS 17 以前は 設定 → Safari → 詳細 → Web インスペクタ）
3. ⚠️ **`console.*` をラップする Safari 拡張は OFF にする**。例えば App Store アプリ
   **「Web Inspector」**は各ページに `console.js` を注入して `console.*` を包むため、記録される
   位置プレフィックスが**常にその拡張のファイル**（`console.js:53` など）になり、ページ本来の
   `file.js:12` が分からなくなる。設定 → アプリ → Safari → 拡張機能 で OFF にすること。
   **OFF にしても、既に開いているページはリロードするまで注入が残る**点に注意。
4. 記録したいページを **端末の Safari で開いておく**（端末はロックしない）

### 3. 設定して起動する

`config.ios.json` の例：

```json
{
  "output_dir": "logs-ios",
  "source": "ios",
  "port": 9223,
  "device_serial": "",
  "timestamp": true
}
```

```sh
./glog.sh --config ios
```

端末の Safari で開いているページに自動でアタッチし、以後そのページのコンソール出力・未捕捉例外を
記録し続ける。端末側で**普通に操作すればよい**。停止は `Ctrl+C`。

### iOS 版の制限（Chrome 版との差）

- **`start_url` は無視される**：PC 側から端末の Safari にページを開かせない（端末で自分で開く）。
- **端末の Safari で開いているページだけが対象**。端末がロックされるとページが止まる。
- **URL フィルタは使える**（`url_filter` / プリセット）。
- タブごとに接続する方式のため、ページのリロード直後は数秒アタッチが遅れることがある。

## 機微情報のマスキング（任意 / `redact_patterns`）

ログには**トークン・API キー・個人情報**が混じり得る（実際に、実機ログに Supabase の `apikey=…` が
出た）。`redact_patterns` に正規表現のリストを書くと、一致部分を `***` に置換して記録する。

```json
"redact_patterns": [
  "apikey=[A-Za-z0-9_\\-]+",
  "eyJ[A-Za-z0-9_\\-]{10,}\\.[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]+"
]
```

```
WebSocket connection to 'wss://…/websocket?***&vsn=2.0.0' failed
```

> ⚠️ **これはベストエフォートであり、安全の保証ではない。** 正規表現では必ず取りこぼす
> （独自形式のトークン、文中に紛れた ID、日本語の個人情報など）。**「マスク済みだから安全」と考えず、
> クラウド AI に渡す前・出力フォルダを同期する前に、これまでどおり中身を確認すること。**
> 自分のアプリのトークン形式を知っている人が、それを機械的に落とすための機能である。
> 既定は無効（`[]`）で、指定しない限り挙動は一切変わらない。

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
- **Android 記録も localhost 限定**。`adb forward` はホスト側 `127.0.0.1` にのみバインドするため、
  端末を記録する場合も CDP が LAN/外部に出ることはありません。
- **Safari 記録も localhost 限定**。`safaridriver` の WebDriver サーバと BiDi WebSocket は
  `127.0.0.1` にバインドされ、本ツールもそこへのみ接続します。`safaridriver --enable`（要 sudo）は
  **利用者が一度だけ手で行う前提で、本ツールは実行しません**。
- **iOS 記録も localhost 限定・非特権**。端末の Web Inspector を CDP に橋渡しするサーバは
  `127.0.0.1` にバインドします（LAN には出しません）。**root / sudo も tunnel も使いません**し、
  デベロッパーディスクイメージのマウントもしません。

運用側で気をつけること:

- 記録中、デバッグ用 Chrome は**同一PC上のローカルプロセスからは操作可能**な状態です。
  共有PCや信頼できない環境では、使い終わったらウィンドウを閉じてください。
- `.chrome-debug-profile/` には**ログインセッション（Cookie/トークン）**が保存されます。
  クラウド同期フォルダに置かない・他者と共有しないこと。
- 出力ログ自体に機微情報（トークン・個人情報など）が混じる場合があります。クラウドの AI へ
  渡す前・出力フォルダを同期する前に中身を確認してください。`redact_patterns`（任意）で既知の
  パターンを `***` に落とせますが、**ベストエフォートであり確認の代わりにはなりません**。
- `config.json` の `chrome_exe` / `adb_path` / `safaridriver_path`（いずれも起動する実行ファイル）と
  URL は信頼できる値に保つこと。**他者から受け取った／同期されてきた config をそのまま使わない**
  （実行ファイルや出力先が差し替えられ、任意プログラム実行・任意の場所への書き込みになり得るため）。

## ライセンス

[MIT License](LICENSE) で公開。自由に利用・改変・再配布できます（無保証）。
