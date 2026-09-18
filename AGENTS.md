# maagent

Windows-only desktop app that drives **MAA** (MaaAssistantArknights) to run
Arknights dailies, auto-dismisses MAA popups via OCR, OCRs MAA's right-side log,
and emails a concise daily report. Python 3.12 + PySide6, packaged with PyInstaller.

Full development workflow (layout, gotchas, exe build, proxy push) lives in the
project skill: `.opencode/skills/maagent-dev/SKILL.md`.

## Commands

- Full workflow: `.venv\Scripts\python.exe -m maagent.main --daily`
- Quick test run: `.venv\Scripts\python.exe -m maagent.main --config config/config.test.yaml --daily`
- Daily workflow chain: `.venv\Scripts\python.exe -m maagent.main --workflow`
- Start MAA + Link Start only: `.venv\Scripts\python.exe -m maagent.main --run`
- Close MAA + emulator: `.venv\Scripts\python.exe -m maagent.main --close`
- Account / phone-web service (headless): `.venv\Scripts\python.exe -m maagent.main --config config/config.yaml --serve`
- Verify imports: `.venv\Scripts\python.exe -c "import maagent.main, maagent.core.orchestrator, maagent.core.controller, maagent.core.scheduler, maagent.control.popup, maagent.control.logmonitor, maagent.server.app, maagent.server.accounts, maagent.gui.app; print('OK')"`

## Rules

- The `maagent` package is installed editable into `.venv`; call the venv python directly.
- User-facing text (GUI, report, CLI help) is Chinese — keep it that way.
- NEVER commit `config/config.yaml`, `config/config.test.yaml`, or `*.export.json`
  — they contain the QQ email authorization code. They are gitignored; keep it so.
- Push through the local proxy (direct github.com:443 is blocked):
  `git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push origin main`
- No test suite yet (`tests/` is empty); verify by importing modules and a GUI/exe smoke test.
