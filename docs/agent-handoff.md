# Shared app handoff

Both agents should start from current GitHub `main` and work on the same application:
`llm-updates serve`. The earlier standalone Fieldnotes/Sites prototype is superseded
by this repository integration; do not add a second frontend server or cloud runtime.

## Integrated work

- Retained Claude's database-backed news feed, history, feedback and three interactive guides.
- Applied Fieldnotes cream/sage/terracotta styling and shared responsive navigation.
- Mounted the Fieldnotes dataset UI at `datasets.html`; source is in `frontend/`.
- Added guarded dataset APIs and a local SQLite sidecar for durable uploads.
- Static exports show a local-app explanation, never private dataset contents.

## Forecasting tab (Claude, branch `claude/forecasting`, PR #2)

- `forecasting.html` sits next to Datasets & tools. It is a separate time-series dashboard:
  seven models ranked by holdout backtest, 80%/95% ranges, scenario sliders, CSV export, print.
- Engine and UI are plain JS in `web/static/forecast.js` and `dashboard.js` (no build step).
  They run in the browser, so the page also works in static exports, minus saving.
- Saved forecast projects use `/api/projects` and the `forecast_projects` table (main
  database, migration 004). This is separate from the Datasets sidecar store.
- Merged with the Fieldnotes integration: `server.py` routes both `/api/datasets` and
  `/api/projects` through the shared write guards (`_write_allowed`).
- Validation at merge: 82 Python tests; Ruff lint/format; browser checks of Forecasting
  and Datasets pages. Frontend `npm test` not run (no Node; `frontend/` unchanged).
- Open question for both agents: the Datasets tab and the Forecasting tab both take CSV
  uploads and forecast. Agree whether to keep both or link them (for example, “Forecast this
  dataset” from Datasets) before extending either.

## Front-page feed (Claude, branch `claude/front-page-feed`, stacked on PR #2)

- Updates shows the latest digest in full, then up to 30 earlier digests as compact story
  lists (3 visible, "Show older digests" reveals more). Single-digest pages are unchanged.
- Digests hold up to 10 stories (`MAX_ENTRIES` in `models.py`; CLI `--top` default 10).
  Migration 005 rebuilds `digest_entries` for the new position CHECK, with foreign keys
  off during the rebuild and checked afterwards.
- Live collection on 15 Oct 2026: arXiv works but hits the 100-candidate cap. GitHub
  sources exhaust the unauthenticated rate limit after one run, OpenAI News returns
  HTTP 403, and Anthropic Engineering article links fail the destination allowlist.
  These are collector issues, not front-page issues.

## Working together

Read this file and the latest commits before editing. Use separate branches for
concurrent changes, agree ownership of overlapping files, and update this document
with scope and validation at handoff. A shared file does not automatically notify
another agent; the user should ask that agent to pull and read it.

Frontend edits require rebuilding `web/static/datasets.js`; instructions are in README.
Prediction and forecasting are intentionally simple linear baselines, not a general
AutoML system. Results are recomputed rather than stored as trained models.

Validation for this integration: 72 Python tests and four frontend test groups passed;
Ruff lint/format passed. Browser checks confirmed guide rendering, dataset loading,
and the expected linear forecast through the Python app.
