# green_light

English | **[日本語](README.md)**

**A tool that automatically records Chrome DevTools console output (logs, warnings, errors, exceptions)
to a text file. It records from two targets — the Chrome on this PC, and the Chrome on a USB-connected
Android device.**

You don't need to have DevTools open: as long as the dedicated Chrome is running (or the target device is
connected over USB), console output keeps streaming into the file. It works by attaching to the browser via
the Chrome DevTools Protocol (CDP) to receive console events, so it never touches the source of the page
being recorded.

### 📱 It can also record the Chrome on a USB-connected Android device

This is not just a PC-only logger. A key feature is that it can **continuously record the Chrome console of a
real USB-connected phone straight into a text file on the PC side.** Instead of opening
`chrome://inspect` and manually copy-pasting the DevTools output, the mobile console (logs, exceptions,
network errors, etc.) keeps flowing into the file across the device's page navigations and reloads. As a
result, you can **hand a real device's debug logs to an AI as-is, without any copy-paste.** See the
"[Recording the Chrome on an Android device](#recording-the-chrome-on-an-android-device-usb--cdp-over-adb)"
section for the steps.

> The current implementation (entry point) is `chrome_console_logger.py`.

## What it is for (why use it)

This is not a replacement for letting an AI drive the browser directly (MCP, etc.). It is a **passive
recorder for the "a human reproduces it, then has the AI read the result" workflow.**

- **Tool-agnostic**: the output is just text. Paste it into / hand it to any AI — no integration setup needed.
- **No gaps**: it records the whole session continuously across reloads and navigations (no missing data
  like a poll-each-time approach can have).
- **Spans machines**: even if dev and test are on separate PCs, syncing the output folder hands the file
  over automatically.
- **Safe (small trust boundary)**: you never give the AI control of the browser — you only hand over a
  read-only artifact.
- **Mobile too**: the Chrome console of a USB-connected Android device is recorded by the same mechanism,
  so you can extract a real phone's debug logs without manual copy-paste.

Conversely, having an AI iteratively debug by clicking → reloading → checking on its own is better suited to
live control. This tool is console-centric (things like network response bodies are out of scope). Because
sensitive information can appear in the logs, check the contents before handing them to a cloud AI (deleting
to clear + filtering both help with "hand over the minimum only").

## Requirements

- **OS: Windows / macOS / Linux**
  - Chrome auto-detection, console code-page setup, and screen clearing are all selected per OS.
  - Launch with `glog.bat` on Windows, and `glog.sh` on macOS / Linux (or run
    `python chrome_console_logger.py ...` directly).
- **Python 3.8 or later**
  - Windows: `python` or `py` must be on PATH.
  - macOS / Linux: `python3` must be on PATH (the macOS system 3.9 works).
- **Google Chrome** (auto-detected if installed in a common location; otherwise specify the full path in
  `config.json`'s `chrome_exe`)
  - Default paths tried: `chrome.exe` under `Program Files` etc. on Windows,
    `/Applications/Google Chrome.app` (and `~/Applications/...`) on macOS,
    `google-chrome` / `chromium` etc. on PATH on Linux.
- Python package: **`websocket-client`**
- (Only if you record an iPhone / iPad) **`pymobiledevice3`** (`pip install -r requirements-ios.txt`).
  No root/sudo needed. See "Recording an iPhone / iPad's Safari" below.
- (Only if you use Android device recording) **adb (Android SDK Platform-Tools)**. See the
  "Recording the Chrome on an Android device" section for installation.

## Setup

Windows (Command Prompt):

```bat
:: 1) Install the dependency
pip install -r requirements.txt

:: 2) Prepare the config file (copy the sample and edit it)
copy config.example.json config.json
```

macOS / Linux (terminal):

```sh
# 1) Install the dependency (a venv is recommended)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) Prepare the config file (copy the sample and edit it)
cp config.example.json config.json
```

`config.json` is **environment-specific and not tracked by Git** (it's in `.gitignore`). Always create it by
copying `config.example.json` and editing it for your environment. At minimum, check `output_dir` (the log
output destination).

## Usage

1. Launch it (**`glog.bat` on Windows**, **`./glog.sh` on macOS / Linux**).
   On Windows you can also double-click it; from a terminal you can **pass a URL or `--config` as arguments**
   (see "Launch examples"). On macOS / Linux, run `./glog.sh ...` or `python chrome_console_logger.py ...`
   directly.
2. A Chrome with a dedicated profile starts up.
3. **Type in the address** of the page you want to record and open it.
4. The console output of **every page** is recorded to the output file.
5. To stop recording, press `Ctrl+C` in that terminal (Chrome stays open).

> ℹ️ **The debug Chrome is intentionally left open after `Ctrl+C`** (you can keep working, and the next run
> attaches to it). Note that **on macOS, closing all its windows does not quit the process**: it stays
> resident and keeps holding the remote-debugging port (9222 by default), whereas on Windows closing the
> last window quits Chrome. That is why the port can be busy with no window in sight. When you are done,
> quit that debug Chrome with **Cmd+Q** — don't leave a debuggable Chrome resident.

By default it records all pages without filtering (`filter_enabled: false`). No startup menu appears, and it
doesn't open any specific URL.

Launch examples (on Windows, works by double-click or from a terminal with arguments):

```bat
glog.bat
glog.bat https://example.com/
glog.bat --config myapp
glog.bat --config myapp https://example.com/
```

On macOS / Linux, pass the same arguments to `glog.sh`:

```sh
./glog.sh
./glog.sh https://example.com/
./glog.sh --config myapp
./glog.sh --config myapp https://example.com/
```

- No arguments … record with the default `config.json` (same as double-clicking)
- `<URL>` … open that URL on launch (takes precedence over `start_url` in `config.json`)
- `--config <name>` … use the config set `config.<name>.json`
- A URL and `--config` can be **combined** (in any order)

See "Command-line arguments" below for details on each argument.

> Launching two or more loggers at once causes double-writes to the same log file, so when restarting, stop
> the previous logger's terminal with `Ctrl+C` first.

### Command-line arguments

Arguments you can pass to `glog.bat`:

| Argument | Description |
|----------|-------------|
| `<URL>` (positional) | URL to open on launch. Takes precedence over `start_url` in `config.json` |
| `--config <name>` / `-c <name>` | Select the config set to use. A name (`myapp` → `config.myapp.json`) or a path. `default` means `config.json`. See "Switching configs per project" below |

- The `--config=<name>` / `-c=<name>` **equals-sign** form is also accepted.
- A URL and `--config` can be **combined** (in any order).
- With no arguments, the config set is chosen interactively (when double-clicked) or defaults to
  `config.json`, and the URL is taken from `start_url`.

```bat
:: Use the myapp config and open the given URL on launch
glog.bat --config myapp https://example.com/
```

### If you delete the log file while recording

You can delete the output file even while recording (the logger does not keep the file open). After deletion,
it is automatically recreated on the next write, with a `# === log file (re)created ... ===` marker at the
top, and recording continues. **At that moment the logger's terminal screen is also cleared.**

When the log grows too large, just delete the file and you get "both file and screen wiped clean." Unwanted
logs that appear before you start (such as a login screen) can also be removed by deleting the file once you
reach the target page.

### When you want to use filters (optional)

The main switch for recording only specific domains is `filter_enabled`. Toggle this first.

- **`filter_enabled: false` (default)** … filtering off. Records **all pages**. No startup menu is shown, and
  the URL is opened from `start_url` / a command-line argument.
- **`filter_enabled: true`** … filtering on. **Only** pages whose main-frame URL contains the configured
  string are recorded (other domains, such as a login screen, are excluded).

> ⚠ **Once you set `filter_enabled: true`, be sure to specify the sites you want to record in
> `url_filter_presets` (or `url_filter` for a single site).** If left empty, a "no filter configured" warning
> is shown and it **falls back to recording all pages**, defeating the purpose of filtering (an unintended
> filter gap). The recording target is decided by each preset's `filter` (a substring matched in the
> main-frame URL); **`url_filter_presets` takes precedence and `url_filter` is the fallback.** If a
> login/auth flow spans multiple domains, list the necessary domains in the presets.

When `filter_enabled: true`, `filter_menu` decides **how** the multiple filter candidates are applied (it is
ignored when `filter_enabled: false`, and no menu appears):

- **`filter_menu: false` (default)** … all `filter`s in `url_filter_presets` are active at once
- **`filter_menu: true`** … pick exactly one from a menu at startup (if that candidate has a `url`, it is
  opened automatically)

> As a safeguard when filtering is on, opening an out-of-scope page prints
> `[info] Not recording (no filter match; ...)` to the terminal. This prevents the situation where a
> forgotten filter setting silently records nothing. Filtering being off by default is also to avoid this
> kind of "data loss you can't notice."

Example of the filter selection menu (when `filter_enabled: true` and `filter_menu: true`):

```
==================================================
Select which pages to record:
  1. Production  [example.com]
  2. Local dev  [localhost]
  3. All pages (no filter)  [(all pages)]  <- default
==================================================
Enter a number (Enter = 3):
```

(`<- default` marks the preset matching `url_filter`. Above, `url_filter: ""`, so "All pages" is the default.)

## Configuration (config.json)

| Key | Description | Default |
|-----|-------------|---------|
| `output_dir` | **Log output folder.** Relative paths are resolved against this script's folder; a leading `~` expands to your home directory (macOS / Linux) | `logs` |
| `log_filename` | Log file name | `console.log` |
| `overwrite` | `true` = overwrite on each launch / `false` = append | `true` |
| `port` | Remote debugging port | `9222` |
| `chrome_exe` | Path to the Chrome executable (auto-detect if empty) | empty (auto-detect) |
| `profile_dir` | Profile location for the debug Chrome (if empty, `.chrome-debug-profile` inside this folder) | empty |
| `source` | Recording target. `desktop` = launch and record this PC's Chrome / `android` = record a USB-connected device's Chrome (see below) / `safari` = record this Mac's Safari (see below; macOS-only) / `ios` = record a USB-connected iPhone/iPad's Safari (see below) | `desktop` |
| `adb_path` | Path to adb for `source: android` (auto-detect from PATH and common SDK locations if empty) | empty |
| `device_serial` | Target device id: the adb serial for `source: android`, the device UDID for `source: ios` (empty = the only connected device) | empty |
| `safaridriver_path` | Path to safaridriver for `source: safari` (auto-detect from PATH if empty; normally macOS built-in, so no need to set) | empty |
| `start_url` | URL to open on launch (a command-line argument takes precedence) | empty |
| `filter_enabled` | `false` = filtering off (record all pages) / `true` = narrow down with filters | `false` |
| `filter_menu` | (only when `filter_enabled: true`) `false` = no menu (all preset filters active at once; doesn't open a URL) / `true` = pick one filter at startup (and open its `url`) | `false` |
| `url_filter` | Fallback narrowing string used when presets are empty (record only pages whose main-frame URL contains it) | empty |
| `url_filter_presets` | Candidates for recording targets. `[{ "label": display name, "filter": narrowing string, "url": URL to open }, ...]`. `url` is opened when selected with `filter_menu: true` (optional) | e.g. Production / Local dev / All |
| `timestamp` | `true` prefixes each line with `[HH:MM:SS]` | `false` |
| `stack_for_trace` | Also print the stack for `console.trace` | `true` |

> **About Windows paths**: because `config.json` is JSON, `\` (backslash) is treated as an escape character.
> When writing a Windows absolute path, use **`\\` (doubled)** for the separators. This applies to all keys
> that take a path, such as `output_dir` / `chrome_exe` / `profile_dir`.
>
> ```json
> "output_dir":  "C:\\Users\\you\\logs",
> "chrome_exe":  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
> ```
>
> You may also use **`/` (forward slash)** instead of `\\` (`"C:/Users/you/logs"`). Writing a single `\`
> makes the JSON invalid and causes a load error on startup.

### Switching configs per project (optional)

When you want to separate the output destination, file name, etc. per project, prepare multiple config files
as `config.<name>.json` and switch between them.

    :: Create a config for myapp (copy from the template and edit)
    copy config.example.json config.myapp.json

There are two ways to switch:

- **Specify on the command line**: `glog.bat --config myapp` (to be explicit about the default,
  `glog.bat --config default`)
- **Choose interactively by double-clicking**: just run `glog.bat` and the opened console lists the config
  sets. Type a number or name (`ENTER` alone uses the default `config.json`).

Notes:

- When `--config` is given, no prompt is shown (good for batch/automation). `--config default` is
  `config.json`.
- The value can be a name (`myapp` → `config.myapp.json`) or a path (`C:\path\my.json`).
- If the file specified with `--config` does not exist, it exits with an error to avoid recording to the
  wrong place (`default` is the exception: it starts with the defaults as before even without `config.json`).
- `config.<name>.json` is not tracked by Git (only `config.example.json` is tracked).
- If you give each config a different `port` and `profile_dir`, you can run loggers for multiple projects at
  the same time.

## Recording the Chrome on an Android device (USB / CDP over ADB)

Instead of opening `chrome://inspect` and copy-pasting by hand, you can continuously record the Chrome
console of a USB-connected Android device to a file by the same mechanism as the PC version. It works by
bridging the device's DevTools to localhost with `adb forward`, then attaching via CDP as usual (the device's
source is left untouched). It records continuously across the real device's navigations and reloads, and you
just hand the output file to an AI — i.e. **you extract a real phone's debug logs without manual copy-paste.**

### 1. Get adb (Android SDK Platform-Tools)

Android recording requires **adb** (Android Debug Bridge). adb is included in the Android SDK's
**Platform-Tools**.

- **First, check whether it's installed**: run `adb version` in PowerShell or Command Prompt. If a version is
  shown, it's already installed (if you've set up a mobile dev environment such as Android Studio, Flutter, or
  React Native, you usually already have it).
- **If not installed** (any of the following):
  - Download the official "**SDK Platform-Tools**" from Google, extract it, and **add the folder containing
    `adb.exe` to PATH** (the Platform-Tools package on developer.android.com; you can use this zip alone
    without installing all of Android Studio).
  - A package manager also works (e.g. `scoop install adb` / `choco install adb`).
  - If you install Android Studio, it's usually at `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`.
- **If you don't add it to PATH**, specify the full path to `adb.exe` in the Android config's `adb_path`
  (empty = auto-detect from PATH and common SDK locations).

Once `adb version` shows a version, the first stage of preparation is done.

### 2. Device-side preparation (USB debugging)

- On the device, turn **Developer options → USB debugging ON.** On first connection, **"Allow" the
  authorization dialog** that appears on the device (`adb devices` shows `device` = OK / `unauthorized` = not
  yet allowed).
- **Open the Chrome you want to record on the device.**

### 3. Configuration (example of creating `config.android.json`)

```jsonc
{
  "source": "android",
  "port": 9333,                 // ← must differ from the desktop version's 9222 (see below)
  "adb_path": "",               // empty = auto-detect
  "device_serial": "",          // empty = the only device / for multiple, the serial from adb devices
  "output_dir": "logs-android",
  "log_filename": "console.log",
  "filter_enabled": true,       // ← strongly recommended ON for Android (see below)
  "url_filter_presets": [       // ← be sure to list the sites to record here (filter = substring in the URL)
    { "label": "my app", "filter": "example.com",          "url": "https://example.com/" },
    { "label": "auth",   "filter": "accounts.example.com", "url": "" }
  ]
}
```

> ⚠ **`filter_enabled: true` is strongly recommended for Android.** The desktop version launches its own
> dedicated profile, but Android connects to **your real device's Chrome**, where personal tabs (online
> banking, shopping, SNS) and **logins to various services (auth sessions)** often coexist in the same
> Chrome. If left with filtering off (record all pages), those consoles get recorded too, and especially if
> you point `output_dir` at a sync folder, they could leak to the cloud.
>
> **Once you set `filter_enabled: true`, be sure to specify the sites you want to record in
> `url_filter_presets` (or `url_filter` for a single site).** If left empty, a "no filter configured" warning
> appears and it **falls back to recording all pages**, defeating the filtering. The recording target is
> decided by each preset's `filter` (a substring matched in the main-frame URL); `url_filter_presets` takes
> precedence and `url_filter` is the fallback. If the login/auth flow **spans multiple domains**, list the
> needed domains in the presets. Check the contents before handing them to a cloud AI, too.

### 4. Run

```bat
glog.bat --config android
```

→ It forwards the device's DevTools to `localhost:<port>` via adb, attaches to the Android Chrome, and starts
recording. Stop with `Ctrl+C` (the forward is also removed automatically on exit).

> ℹ️ If it dies by something other than `Ctrl+C` (e.g. closing the terminal), the `adb forward` may remain
> (it's localhost-only so the impact is small, and you'll notice next time via "port in use"). If it bothers
> you, remove it with `adb forward --remove-all`.

> ℹ️ The device counts as **`offline` while the screen is locked or the Settings app is in front.** If the
> device isn't online at startup, it prompts "please unlock the screen" and **waits automatically**, then
> starts recording as soon as it comes online (the state where `adb devices` shows `device`).

> ℹ️ The moment it attaches, each tab **re-sends the console it had buffered**, so a burst of logs appears
> right after connecting (this is Chrome's behavior — the same as seeing past logs when you open DevTools
> later). If duplicates or noise bother you, **close unneeded tabs** on the device, or **delete the log file**
> after connecting to keep only "from now on."

> ⚠ **Use a different port from the desktop version.** If `port` is already in use, `adb forward` fails. This
> tool **does not swallow the failure — it exits with an error**, and further **verifies the endpoint is
> really a device (Android Chrome).** This prevents the accident of **mistakenly connecting to the PC's
> Chrome** when another Chrome is using the same port.

## Recording this Mac's Safari (macOS / WebDriver BiDi) [Experimental]

> ⚠️ **This feature is experimental.** Safari's WebDriver BiDi is, as of this writing, **experimental**: we
> internally request the undocumented capability `safari:experimentalWebSocketUrl` (without it, Safari does
> not return a BiDi WebSocket URL). **Verified working on Safari 26.2**, but this unlock is not in Apple's
> official docs and may be renamed / changed / removed by a Safari update. If it fails to connect, first check
> your Safari version and the "Allow Remote Automation" setting.

You can also record the console of this Mac's **Safari** (`source: safari`). Safari does not speak CDP, so it
uses a separate path from the Chrome-family sources (macOS's built-in `safaridriver` + **WebDriver BiDi**) to
receive console / uncaught-exception entries and append them to the same text file.

### 1. Allow Remote Automation once

To control Safari from automation, do one of the following **once** (requires admin):

```sh
sudo safaridriver --enable
```

Or **Safari > Settings > Advanced > enable "Show features for web developers" → Safari > Develop > check
"Allow Remote Automation"**.

> ⚠ This tool **never runs `safaridriver --enable` (needs sudo) itself**. The step above is a one-time manual
> action by the user, since it is a privileged change we don't want to make implicitly.

### 2. Configure and launch

Example `config.safari.json` (`start_url` is **required** — see "The big limitation" below):

```json
{
  "output_dir": "logs",
  "source": "safari",
  "start_url": "https://example.com/",
  "timestamp": true
}
```

```sh
./glog.sh --config safari https://example.com/   # launch with the page you want to record
```

An automation Safari window opens (with a "Safari is under automation" banner), loads that URL, and its console
output is recorded. Stop with `Ctrl+C` (same as other sources).

### ⚠ The big limitation: you cannot interact with the automation window

Safari covers the automation window with a transparent **"glass pane"** that **blocks mouse and keyboard
input** — this is by design (WebKit:
[WebDriver Support in Safari 10](https://webkit.org/blog/6900/webdriver-support-in-safari-10/) —
*"Safari installs a 'glass pane' over the Automation window while the test is running. This blocks any stray
interactions (mouse, keyboard, resizing, and so on)"*). Trying to interact pops up a dialog; choosing
**"Stop Session"** there **severs the WebDriver session and ends the recording**.

So with `source: safari` you **cannot** do what the Chrome version allows — *drive the browser by hand to
reproduce a bug and record its console*. You only get the log output of the page opened via `start_url`
(console output and uncaught exceptions that occur on their own from load time onward).

> To record while driving the browser by hand, use Chrome (`source: desktop` / `android`).
> Making Safari hand-drivable requires a non-WebDriver path (e.g. hooking `console.*` from a Safari
> extension); that is **future work**.

### Other Safari limitations (vs the Chrome version)

- **URL filtering is disabled**: Safari's BiDi log entries carry no page identity, so `url_filter` / presets
  are ignored for `source: safari` and **all pages** are recorded.
- **No line-number prefix**: Safari does not attach a source location to console lines, so there is no
  `file.js:12` prefix like the Chrome version (the message body itself is recorded as-is).
- **No login state**: the automation window uses a separate profile from your normal Safari.
- **macOS only**: `safaridriver` ships with macOS; it is not available on Windows / Linux.
- WebDriver BiDi is currently experimental in Safari, so internally we request
  `safari:experimentalWebSocketUrl`.

## Recording an iPhone / iPad's Safari (USB / pymobiledevice3)

You can record the console of a USB-connected **iOS device's Safari** (`source: ios`). Like the Android source,
**you hold the device and use it by hand while its console output keeps streaming into a text file on the PC.**
Unlike the macOS Safari source there is **no restriction on interacting with it** (this attaches the Web
Inspector, it does not automate the browser).

**No root/sudo and no tunnel are needed.** The same code runs on a macOS or a Windows host (developed on macOS).

### 1. Install the dependency

```sh
pip install -r requirements-ios.txt      # pymobiledevice3 (only for the ios source)
```

### 2. Prepare the device

1. **Connect over USB**, unlock the device and tap **Trust** on "Trust This Computer?".
2. **Settings > Apps > Safari > Advanced > "Web Inspector" ON**
   (iOS 17 and earlier: Settings > Safari > Advanced > Web Inspector).
3. ⚠️ **Turn OFF any Safari extension that wraps `console.*`.** For example the App Store app
   **"Web Inspector"** injects a `console.js` into every page and wraps `console.*`, so every recorded line
   gets the *extension's* location (`console.js:53`) instead of the page's own `file.js:12`.
   Turn it off in Settings > Apps > Safari > Extensions. **Note that a page already open keeps the injected
   script until you reload it.**
4. **Open the page you want to record in Safari on the device** (and keep the device unlocked).

### 3. Configure and launch

Example `config.ios.json`:

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

It attaches to the pages open in the device's Safari and records their console output and uncaught exceptions
from then on. Just **use the device normally**. Stop with `Ctrl+C`.

### iOS limitations (vs the Chrome version)

- **`start_url` is ignored**: the tool does not open pages on the device; you open them yourself.
- **Only pages currently open in the device's Safari** are recorded, and the device must stay unlocked.
- **URL filtering works** (`url_filter` / presets).
- Because it attaches per tab, re-attaching can take a few seconds right after a page reload.

## How it works (notes)

- Launches Chrome with `--remote-debugging-port` + a dedicated `--user-data-dir` (since Chrome 136, remote
  debugging is disabled on the default profile, so a dedicated profile is used)
- Connects to CDP with the Origin header suppressed (does not add `--remote-allow-origins=*`, avoiding the
  403 origin rejection)
- Makes a single browser-level CDP connection and auto-attaches to all pages with
  `Target.setAutoAttach` (flatten)
- Receives `Runtime.consoleAPICalled` / `Runtime.exceptionThrown` / `Log.entryAdded` and appends them to the
  file (it opens/closes on each write, so it holds no handle and the file can be deleted even while recording)

## Notes

- Because of the dedicated profile, your everyday Chrome's login info and extensions are not carried over.
  You need to log in the first time on sites that require it (the profile is saved, so it's not needed from
  the second time on).
- Placing `profile_dir` inside a sync folder (Drive/Dropbox, etc.) risks bloat and conflicts, so it's
  recommended to keep it inside this project folder as by default.
- `config.json`, `.chrome-debug-profile/`, and `logs/` are in `.gitignore` (not committed).

## Security

This is a tool intended for a developer to use on their own machine. Design points:

- **The debug port is localhost-only.** `--remote-debugging-port` binds to `127.0.0.1` and is not exposed to
  the LAN/outside (we do not add `--remote-debugging-address`).
- **No allow-all origins.** Because this tool connects without an Origin header, `--remote-allow-origins=*`
  is unnecessary and not added. This keeps Chrome's default origin check active = it **prevents a malicious
  web page from making a CDP connection to the debug port and driving this browser.**
- **Does not weaken Chrome's sandbox.** It does not use `--no-sandbox` or `--disable-web-security`.
- **Android recording is localhost-only too.** `adb forward` binds only to the host's `127.0.0.1`, so even
  when recording a device, CDP never goes out to the LAN/outside.
- **Safari recording is localhost-only too.** safaridriver's WebDriver server and the BiDi WebSocket bind to
  `127.0.0.1`, and this tool connects only there. `safaridriver --enable` (needs sudo) is **a one-time manual
  step by the user; this tool does not run it.**
- **iOS recording is localhost-only and unprivileged too.** The server that bridges the device's Web Inspector
  to CDP binds `127.0.0.1` (never the LAN). It uses **no root/sudo and no tunnel**, and mounts no developer
  disk image.

Things to watch out for in operation:

- While recording, the debug Chrome is in a state **operable by local processes on the same PC.** On a shared
  PC or an untrusted environment, close the window when you're done.
- `.chrome-debug-profile/` stores **login sessions (cookies/tokens).** Don't put it in a cloud sync folder
  and don't share it with others.
- The output logs themselves may contain sensitive information (tokens, personal data, etc.). Check the
  contents before handing them to a cloud AI or before syncing the output folder.
- Keep `config.json`'s `chrome_exe` / `adb_path` / `safaridriver_path` (all executables that get launched) and
  the URL set to trusted values. **Don't use a config received from / synced by someone else as-is** (the
  executable or output destination could be swapped, leading to arbitrary program execution or writes to
  arbitrary locations).

## License

Released under the [MIT License](LICENSE). Free to use, modify, and redistribute (no warranty).
