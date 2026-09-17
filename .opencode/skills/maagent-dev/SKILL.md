---
name: maagent-dev
description: Use when developing, running, debugging, testing, or packaging the maagent repo (二游日常助手, successor of GameOps). Trigger on keywords such as maagent, MAA, MaaAssistantArknights, Link Start, 日常, 弹窗, popup, OCR, PySide6, PyInstaller, 打包 exe, or GitHub push in this project.
---

# maagent development

Windows-only desktop app that drives **MAA** (MaaAssistantArknights) to run
Arknights dailies, auto-dismisses MAA popups via OCR, OCRs MAA's right-side log,
then emails a concise daily report.

## Layout

```
maagent/                 # Python package
  main.py                # CLI entry
  adapters/              # maa.py (active), maaend.py / bgi.py (stubs)
  control/
    process.py           # close MAA + MuMu emulator (MuMuManager)
    popup.py             # window find / PrintWindow capture / OCR / dismiss
    logmonitor.py        # OCR the MAA log panel, parse times/sanity/errors
  core/orchestrator.py   # the --daily workflow
  report/generator.py    # concise report (start/end, errors, sanity, next deadline)
  notify/email.py        # SMTP (QQ) ; notify/wechat.py (stub)
  gui/app.py             # PySide6 GUI (entry for the exe)
  log/logger.py
config/                  # config.yaml (gitignored), config.example.yaml, config.test.yaml
scripts/                 # make_icon.py, monitor_daily.py
dist/maagent.exe         # built artifact (gitignored)
```

## Environment

- venv: `.venv\Scripts\python.exe` (Python 3.12, `maagent` installed editable).
- Prefer calling the venv python directly; if `pip.exe`/`pyinstaller.exe` ever
  break after moving the repo, use `python -m pip` / `python -m PyInstaller`.
- Reinstall editable if imports break: `.venv\Scripts\python.exe -m pip install -e .`
  (needs network; hatchling is only in the isolated build env).

## Run the CLI

```
.venv\Scripts\python.exe -m maagent.main --daily            # full workflow
.venv\Scripts\python.exe -m maagent.main --launch           # only start MAA
.venv\Scripts\python.exe -m maagent.main --run              # start MAA + Link Start
.venv\Scripts\python.exe -m maagent.main --monitor --seconds 3600
.venv\Scripts\python.exe -m maagent.main --close            # close MAA + emulator
.venv\Scripts\python.exe -m maagent.main --config config/config.test.yaml --daily
```

`--daily` flow: clean start (close MAA+emulator) → start MAA (MAA starts the
emulator itself) → dismiss popups → click Link Start / ensure daily started →
monitor log + popups → build report → send email.

## Config essentials (`config/config.yaml`)

- `adapters.maa.executable` — `D:/MAA/MAA.exe`
- `adapters.maa.emulator.manager_path` — `D:/tools/MuMuPlayer/nx_main/MuMuManager.exe`
- `workflow.*` — `start_timeout`, `daily_timeout_seconds`, `min_runtime_seconds`, `poll_interval`
- `notify.email.*` — QQ SMTP (`smtp.qq.com:465`) with the 16-char **authorization code** (not the login password)

## Windows-automation gotchas (learned the hard way)

- Find MAA windows by process `MAA.exe`; popups are owned WPF dialogs (class `#32770`).
- Screenshots MUST use `PrintWindow` + `PW_RENDERFULLCONTENT` (`popup.capture_window`).
  `mss`/screen grabs capture whatever is on top (wrong window) when MAA is occluded.
- Bring a window to front with `popup._force_foreground` (AttachThreadInput);
  plain `SetForegroundWindow` silently fails.
- OCR engine: `rapidocr-onnxruntime` (first call is slow).
- MuMu adb often needs an explicit `adb connect 127.0.0.1:5555` before MAA can
  see `emulator-5554`.
- Detect "daily actually started" from the log (`连接成功` / `开始任务`), not the
  button state — the button also shows `running` during MAA's emulator startup.
- When launching a GUI app to screenshot it, filter windows by **process name**,
  not just title (browser tabs can contain the same word).

## GUI

- Entry `maagent/gui/app.py` → `main()`, window class `MaAgentWindow`,
  title `maagent - 二游日常助手`.
- Icon assets `maagent/gui/assets/icon.{png,ico}`; path resolution must handle
  frozen builds via `asset_path()` (`sys._MEIPASS/maagent/gui/assets`).
- Workflow runs in a `DailyWorker(QThread)`; loguru is bridged to the UI.

## Build the exe

From the repo root:

```
.venv\Scripts\pyinstaller.exe --name maagent --windowed --onefile --noconfirm `
  --icon maagent/gui/assets/icon.ico `
  --add-data "maagent/gui/assets;maagent/gui/assets" `
  --collect-all rapidocr_onnxruntime --collect-all onnxruntime `
  --collect-all pyclipper --collect-all shapely `
  --add-data "config/config.yaml;config" maagent/gui/app.py
```

Output `dist\maagent.exe` (~140 MB onefile; first launch unpacks, ~10-30 s).

Regenerate the icon (transparent background + multi-size ico):

```
.venv\Scripts\python.exe scripts/make_icon.py   # source: D:\install\maagent图标.png
```

## Git / GitHub

- Remote: `https://github.com/qqzj1071/maagent` (branch `main`).
- Direct `github.com:443` is blocked; push through the local proxy:

```
git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push origin main
```

- NEVER commit `config/config.yaml`, `config/config.test.yaml`, or `*.export.json`
  (they contain the email authorization code). They are gitignored — keep it that way.

## Verify before calling it done

```
.venv\Scripts\python.exe -c "import maagent.main, maagent.core.orchestrator, maagent.control.popup, maagent.control.logmonitor, maagent.gui.app; print('OK')"
```

GUI smoke test: launch `dist\maagent.exe`, wait for a window titled
`maagent - 二游日常助手` (match by process `maagent.exe`), screenshot the title
bar to confirm the icon, then `taskkill /IM maagent.exe /F`.
