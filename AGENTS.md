# Shared repository workflow

Read `docs/agent-handoff.md` and current `main` before changing the app.
The shared application is `llm-updates serve`, implemented in `src/llm_updates/web`.
Keep news tied to the agent database. Do not revive the standalone Sites prototype.

When editing the dataset UI, change `frontend/` sources and rebuild the committed
`src/llm_updates/web/static/datasets.js` using `npm ci && npm run build` in `frontend/`.
The Python application must remain runnable without Node or cloud credentials.
Run `pytest`, `ruff check src tests`, and `ruff format --check src tests`; run
`npm test` in `frontend/` when changing the dataset parser or numerical tools.

Preserve loopback Host/Origin guards, keep uploaded datasets out of static exports,
and never commit runtime databases or user data. Check for concurrent branch changes
before pushing. Update the handoff document when changing architecture or integration.
