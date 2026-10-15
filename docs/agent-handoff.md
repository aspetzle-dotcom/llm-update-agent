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
