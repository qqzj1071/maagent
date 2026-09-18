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
    popup.py             # window find / PrintWindow capture / OCR / dismiss (+ find_text/ensure_visible)
    logmonitor.py        # OCR the MAA log panel, parse times/sanity/errors
    weekly.py            # 周常: 使用药剂 toggle + 剿灭刷取 scheduling / progress
    monthly.py           # 月常: 绿票/黄票商店 via 小工具→牛杂 OCR navigation
    maaend.py            # MaaEnd window/process helpers + F10/F11 + 开始任务 click
    maaend_api.py        # MaaEnd HTTP API client + MXU pipeline_override computation
  core/orchestrator.py   # the MAA --daily workflow
  core/maaend.py         # the MaaEnd (终末地) --maaend workflow
  core/scheduler.py      # 日常工作流 chain schedule (sequential / per-task, persisted state)
  report/generator.py    # concise report (start/end, errors, sanity, next deadline)
  notify/email.py        # SMTP (QQ) ; notify/wechat.py (stub)
  gui/app.py             # PySide6 GUI (entry for the exe) + ChainWorker
  gui/widgets.py         # SoftwareCard / ToggleSwitch / CollapsibleSection / WeekdayPicker / WorkflowTaskRow
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
.venv\Scripts\python.exe -m maagent.main --workflow         # run the configured chain now
.venv\Scripts\python.exe -m maagent.main --launch           # only start MAA
.venv\Scripts\python.exe -m maagent.main --run              # start MAA + Link Start
.venv\Scripts\python.exe -m maagent.main --monitor --seconds 3600
.venv\Scripts\python.exe -m maagent.main --close            # close MAA + emulator
.venv\Scripts\python.exe -m maagent.main --config config/config.test.yaml --daily
```

`--daily` flow: clean start (close MAA+emulator) → start MAA (MAA starts the
emulator itself) → dismiss popups → 周常 (药剂/剿灭) → 月常 (绿票/黄票商店) →
click Link Start / ensure daily started → monitor log + popups → build report →
send email.

## 周常 / 月常 (MAA GUI automation)

- 周常 (`weekly.py`) drives the **一键长草** tab: toggles 理智作战→使用药剂 and the
  剿灭刷取 row's checkbox via OCR + screen clicks; progress in `logs/weekly_state.json`.
- 剿灭 completion is the **`剿灭模式: X / 1800`** line in the panel log (parsed by
  `parse_annihilation`, cap `weekly.annihilation.cap`, default 1800), not run count.
  On the chosen day, if it isn't done the report warns with `⚠️` (red in HTML).
- 月常 (`monthly.py`) drives **小工具 → 牛杂 → 绿票商店/黄票商店 → Link Start!**
  (OCR-located labels). The 牛杂 tools run as `(自定任务)` and do **not** change the
  bottom button state, so completion is detected from the panel log
  (`开始任务` / `完成任务`), not `button_state()`. Progress in `logs/monthly_state.json`
  (one purchase per calendar month).
- OCR quirks: the button reads `LinkStart!` (no space); the tab reads `键长草`
  (drops `一`). Match with candidate tuples, not exact strings.

## 日常工作流 (GUI card + `core/scheduler.py`)

- The first card opens a panel that orders the daily tasks and schedules them.
  Config lives at `workflow.chain`: `enabled`, `mode` (`sequential` |
  `scheduled`), `start_slots` (list of `{time, days}` — each time has its own run
  days; legacy single `start_time` / `start_times` + chain `days` still read),
  `grace_minutes`, `state_file`, plus **two independent task lists**:
  `sequential_tasks` (`id`/`software`/`enabled`, one per software) and
  `scheduled_tasks` (`id`/`software`/`enabled`/`time`/`days`, duplicable). The old
  shared `tasks` list is migrated by `_legacy_split` / `Scheduler._task_lists`.
- **Both timing modes**: `sequential` = every `start_slots` entry runs the whole
  enabled chain in `sequential_tasks` order on its own days (deduped per time per
  day); `scheduled` = each `scheduled_tasks` entry fires at its own time/days.
  `TimeListEditor` edits the start slots (time + a **large** `WeekdayPicker`).
- **The two lists are independent** and both live in `TaskListEditor` widgets
  (`MaAgentWindow.seq_list` / `.sched_list`). Only the scheduled list allows
  copy/delete/add (each software keeps ≥1 entry, last delete button disabled);
  the sequential list is one-per-software and reorder-only. `按顺序立即执行` runs
  `seq_list.enabled_softwares()` via `ChainWorker`.
- Drag the `≡` grip (`DragHandle`) to reorder. This is a **manual drag, not Qt
  QDrag/drop** (a child label never gives the parent row the implicit mouse grab,
  so DnD was unreliable): the grip emits `drag_started/moved/ended(payload,
  globalPos)`, and `TaskListEditor` computes the insert index from the cursor Y
  (`_compute_drop_index`), shows a `DropIndicator` line before/after the hovered
  row (including after the last row — the layout has 6px top/bottom margins so
  the line isn't clipped), then reorders on release.
- On load, `_chain_needs_save()` triggers a save when the stored chain lacks
  `start_times` or stable task `id`s, so per-day dedup keys stay stable across
  restarts.
- `Scheduler` is pure logic (no Qt): the GUI `QTimer` polls every 20s, calls
  `poll()` → `mark(event)`, and only starts a run when no `ChainWorker` is busy.
  Fired events are persisted per-day in `logs/schedule_state.json` so they never
  repeat. `grace_minutes` (default 30) allows a late run if the app was busy.
- `enabled` is read from the config dict, so `on_workflow_changed` calls
  `_sync_chain_config()` to push UI state into config before `next_run()`/`poll()`
  (otherwise the 5s-debounced autosave makes the status lag).
- An **empty `days` list means "never"** (only `None`/missing means every day) —
  see `Scheduler._days`.
- Only MAA/MaaEnd are in the chain (BetterGI is still a stub); `--workflow` runs
  the configured chain headlessly for testing.

## MaaEnd (终末地) — `--maaend`

- MaaEnd is a Tauri/WebView2 app (`MaaEnd.exe`, window class `Tauri Window`).
  **PrintWindow does NOT work** → grab the screen with `ImageGrab.grab(window_rect)`
  while the window is foreground (`control/maaend.py:capture_window_screen`).
- `SetForegroundWindow` is denied by the foreground lock; tap ALT
  (`keybd_event(VK_MENU)`) first — see `MaaEndUI.foreground`.
- A minimized MaaEnd ignores `SW_RESTORE`; fall back to
  `WM_SYSCOMMAND / SC_RESTORE` (added to `popup.ensure_visible`).
- **MaaEnd and Endfield both run elevated** (`Endfield.exe` has
  `requireAdministrator`; WinError 740 otherwise). Windows UIPI then blocks a
  non-elevated maagent from injecting mouse/keyboard into MaaEnd. So the exe is
  built with `--uac-admin` and maagent runs elevated — do NOT try to de-elevate
  MaaEnd (it could no longer drive the elevated game).
- **HTTP API** on `127.0.0.1:12701/api` (config `settings.webServerPort`):
  `GET /config`, `POST /config`, `GET /interface`, `GET /maa/state`,
  `GET /maa/windows?class_regex=&window_regex=`, `GET /logs`,
  `POST /maa/instances/{id}/connect`, `POST .../tasks/start`, `POST .../tasks/stop`,
  `GET /system/is-elevated`. `control/maaend_api.py` wraps these and ports MXU's
  `pipeline_override` computation (`Ea/ns/$s/Qh/jn` + the `__MXU_*` special tasks).
  Note `tasks/start` needs the resource loaded + controller connected first
  (else HTTP 500 "Resource not loaded"), so we don't use it to start.
- Workflow: open MaaEnd → enable `settings.hotkeys.globalEnabled` via `POST /config`
  → send **F10** (MaaEnd's startTasks hotkey) → MaaEnd runs its preActions
  (launching Endfield itself) + tasks → poll `is_running` → build the report from
  `/logs`. F11 stops. A click on 开始任务 is the fallback if the hotkey misses.
- Logs: `debug/YYYY-MM-DD-N.log` (app log) and `debug/maafw.log`; the API `/logs`
  gives the same structured entries. `autoClearLogsOnLaunch` clears them.
- Report values: task start/end from `/api/logs` (filtered to the current run by
  the last entry timestamp before starting); 武库配额/嵌晶玉 purchases from
  `debug/go-service.log` `AddItemData` (`item_gachabyproducts_weapongold` /
  `item_diamond`); 未来可期 from `EssenceFilter` `matched_total`; 理智 from the
  framework log's `当前理智 X/Y`. Remaining sanity = current − (160 if ≥160 else
  80 if >80); next latest start = end + (max−remaining) × 7分12秒.

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
  `detect_started()` ignores `自定任务` lines so a 牛杂 run isn't mistaken for the daily.
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
.venv\Scripts\pyinstaller.exe --name maagent --windowed --onefile --uac-admin --noconfirm `
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
.venv\Scripts\python.exe -c "import maagent.main, maagent.core.orchestrator, maagent.core.maaend, maagent.control.popup, maagent.control.logmonitor, maagent.control.weekly, maagent.control.monthly, maagent.control.maaend, maagent.gui.app; print('OK')"
```

GUI smoke test: launch `dist\maagent.exe`, wait for a window titled
`maagent - 二游日常助手` (match by process `maagent.exe`), screenshot the title
bar to confirm the icon, then `taskkill /IM maagent.exe /F`.
