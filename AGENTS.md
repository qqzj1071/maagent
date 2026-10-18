# maagent

Windows-only desktop app orchestrating **MAA**, **MaaEnd**, and **BetterGI**
dailies, with scheduled workflows, email reports, and a built-in phone PWA.
Python 3.12 + PySide6, packaged with PyInstaller.

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
- Offline regression tests: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
- Phone web logic tests (Node.js, no npm dependencies): `node --test tests/phone_web.test.cjs`
- Review and handoff notes: `docs/development-review-2026-10-17.md`

## Rules

- The `maagent` package is installed editable into `.venv`; call the venv python directly.
- User-facing text (GUI, report, CLI help) is Chinese — keep it that way.
- NEVER commit `config/config.yaml`, `config/config.test.yaml`, or `*.export.json`
  — they contain the QQ email authorization code. They are gitignored; keep it so.
- Push through the local proxy (direct github.com:443 is blocked):
  `git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push origin main`
- Run offline regression tests and module imports after runtime changes. Tests use
  temporary files without launching games or sending email. GUI/exe and real-game
  checks are separate; do not treat imports alone as end-to-end validation.
- Use `maagent.release.spec` for release builds: it bundles clean installer config.
  The older skill's inline build command embeds personal config and is unsuitable
  for distributable releases.
