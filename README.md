# Personal LLM update agent

A working local Python CLI that collects a configured set of sources, stores immutable evidence, and writes an auditable digest of up to five stories. The deterministic baseline works without an API key. The OpenAI research layer is opt-in and experimental.

## Try the offline demo

Requires Python 3.11 or later. The implementation was tested with Python 3.12 on Windows. From this directory, using PowerShell:

If you already use `uv`, run `powershell -ExecutionPolicy Bypass -File .\run-demo.ps1`. It creates the project environment and runs the offline demo. The initial dependency installation needs network access; the fixture run itself is offline. Otherwise use the explicit setup below:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
llm-updates init --config demo.toml --demo
llm-updates --config demo.toml run --fixture fixtures/demo.jsonl --since 2026-10-07 --until 2026-10-14
```

On macOS or Linux, use `python3 -m venv .venv` and `source .venv/bin/activate` for the first two steps. For a smaller baseline-only install, use `python -m pip install -e .` instead of the two installation commands above.

The demo has **20 original synthetic observations, 18 logical items, and five selected entries**. It makes no network or model calls. Run the same command again: it returns the same committed digest and adds no versions or entries. An example is saved in [examples/demo.md](examples/demo.md), with machine-readable output in [examples/demo.json](examples/demo.json).

Demo data uses `data/demo.sqlite3`. Live data uses `data/updates.sqlite3`. Keep these separate: the fixture stories are fictional. The repository's example files contain only synthetic content.

## Collect live sources

```powershell
llm-updates init
llm-updates run
```

Edit `config.toml` to change sources, topic weights, relevance thresholds, or limits. Relative database/output paths resolve against the configuration file, not the shell's current directory. No scheduler is installed.

The initial eight sources are arXiv cs.CL and cs.LG; releases from vllm-project/vllm, huggingface/transformers, ggml-org/llama.cpp, and langchain-ai/langgraph; OpenAI News; and Anthropic Engineering. arXiv uses its documented API for the requested bounded lookback. The parser also supports RSS snapshots, which cannot establish complete historical coverage.

Dates are UTC and windows are **start inclusive, end exclusive**. The default lookback is seven days. A subsequent collection can overlap a successfully covered window by 48 hours when that coverage includes the requested start, and retains the existing corpus. Coverage watermarks use the collected window's end, rather than the time the command ran. Each source's effective query window is saved before fetching so resumed page offsets use the same query. Publication dates determine recency. Unknown dates use first-seen time with an explicit label. Publisher update timestamps do not make old articles new stories.

Database schema 3 records the latest observed version separately from immutable content versions, so a publisher reverting content also restores that version as current. Upgrades preserve evidence and digests, clear legacy clock-based coverage watermarks, and restart unfinished legacy source pagination because its effective query window was not saved. Previously completed sources remain complete.

The program reports `partial` when a source fails, a candidate cap truncates collection, an official index cannot establish full window coverage, or the model layer cannot finish. Official indexes deliberately report their limited coverage. Empty successful collections and outages are distinguishable in JSON and the Markdown footer. GitHub prereleases and draft releases are skipped. Source snapshots and evidence stay local in the database; release assets and PDFs are not downloaded.

Exit codes: **0** succeeded, **2** usable partial result, **1** failed, **130** interrupted. A partial result is still useful: open the reported digest to see source status and limitations.

## Browse in a web app

```powershell
llm-updates serve
```

Open http://127.0.0.1:8765/. The default **Updates** page shows the latest digest, source coverage, topics so far, and earlier digests. Useful / Already knew / Skip buttons record the same append-only feedback as `llm-updates feedback`. Three beginner guides with interactive charts sit alongside: **Classical ML** (data types, scaling, regression, k-nearest neighbours, k-means, overfitting, classification metrics), **Deep learning** (activations, gradient descent, a neural network you can train in the browser, convolutions), and **LLMs** (tokens, embeddings, attention, sampling, training, glossary). Use `--config demo.toml` before `serve` to browse the demo database, and `--port` to change the port.

The server binds only to 127.0.0.1, rejects other Host headers and cross-site writes, and takes the database lock only while saving feedback. It needs no extra dependencies, and the charts make no network requests.

To publish a read-only copy, for example on GitHub Pages or any static host:

```powershell
llm-updates site --out data/site
```

The export contains the guides and every committed digest (source excerpts, coverage and scores). It leaves out feedback buttons, feedback notes and model transcripts. Remember that digests quote third-party sources; check that you are comfortable republishing those excerpts before making the site public.

## Commands

```text
llm-updates collect --since 2026-10-07 --until 2026-10-14
llm-updates run --top 5 --profile profiles/default.toml
llm-updates run --key intentional-second-invocation
llm-updates run --refresh-of RUN_ID --key refresh-1
llm-updates resume RUN_ID
llm-updates replay RUN_ID --offline
llm-updates digest show DIGEST_ID --format markdown
llm-updates digest show DIGEST_ID --format json
llm-updates inspect ITEM_ID --versions --evidence
llm-updates feedback ENTRY_ID useful --note "Clear context example"
llm-updates feedback ENTRY_ID already_knew
llm-updates feedback ENTRY_ID skip
llm-updates status
llm-updates serve --port 8765
llm-updates site --out data/site
llm-updates evaluate --dataset eval/synthetic.jsonl --mode baseline
llm-updates evaluate --dataset eval/synthetic.jsonl --mode newest
llm-updates evaluate --dataset eval/synthetic.jsonl --mode keyword-recency
```

`--config PATH` goes before the subcommand, except `init --config PATH` is also supported. Use `llm-updates --help` for the full interface. `collect` commits observations without publishing a digest. `run` collects and publishes. An invocation key is combined with the time window, fixture content hash, source configuration, profile, mode, and parent run. Specify the same window and key to request the same run; the default rolling window changes with the clock.

`resume` uses the stored configuration, committed page cursors, frozen shortlist, and successful model responses. It never silently retries an uncertain paid call. Once a digest is committed, resume/replay export that same digest. To retry missing sources after a published partial digest, create an intentional refresh using `--refresh-of`. Recovery from an export failure needs no collection or model call.

Feedback is append-only. Only the latest label on an entry governs personalization. Useful feedback modestly increases matched topic weights, with a bounded total adjustment. Skip only changes a broad preference after repeated dismissals. Already-knew is recorded as exposure and available through the scoped history tool; it is never treated as mastery. Silence changes nothing.

## Enable the optional OpenAI layer

Install `python -m pip install -e '.[openai]'` if you did not install the full development lock. Set `OPENAI_API_KEY` in your environment, outside configuration files and the repository.

In `[provider]`, explicitly set a model available to your account, its context window, the USD-per-million rates for normal input, cached input, and output, and an ISO date/time in `checked_at`. Verify those values against current official pricing; no guessed model or pricing is shipped. The verification date must be within 31 days.

```powershell
llm-updates run --mode research
```

This command can incur API charges after configuration. The implementation uses the official OpenAI SDK's Responses API with a strict JSON schema, `store=false`, and SDK retries disabled. It sends only shortlisted public source material and a small topic/depth profile. Personal feedback notes are kept local. Provider billing and data handling remain subject to the provider's policies.

The controller permits four named read tools: candidate listing, passage retrieval, fetching an already-stored registered item, and scoped prior coverage. **Registered-item fetching is currently cache-only; no model-directed network enrichment is enabled.** There is no shell, arbitrary URL, filesystem, SQL, or write tool. The ceiling of four network enrichments is therefore satisfied by zero enrichments. Deterministic collectors own all live source fetching.

Application limits cover model calls, tool calls, input/output tokens, context size, active wall time, per-run cost, and monthly cost. The monthly ledger uses UTC calendar months in this database; use one database for all runs sharing that budget. Costs use configured rates and returned usage. Reasoning tokens are included in returned output usage, not charged twice. A conservative UTF-8-byte estimate plus overhead reserves input and worst-case output before dispatch. Ambiguous timeouts retain reservations. There is no automatic paid retry. If a cap is reached, the result is a labeled baseline fallback. Provider-side billing can differ; these controls are not an account-wide billing guarantee.

Factual model claims must be exact excerpts from cited versions. Numeric claims require quoted number, unit, baseline, and test conditions. Other explanation text must be labeled, evidence-linked interpretation. These checks establish provenance and constrain unsupported output; they do not independently prove source claims or the correctness of interpretations. Invalid output, a refusal, missing configuration, or a provider failure produces the deterministic baseline.

## Verify the implementation

```powershell
python -m pytest -q
ruff check .
ruff format --check .
```

The test suite uses synthetic sources and mocked HTTP/SDK responses. It covers ingestion idempotency, changed versions, unknown dates, forward story links, transactional rollback, foreign keys, immutable evidence, pagination/resume, source selectors, redirects, private DNS, connection pinning, rate limits, model refusals, context overflow, cost reservations, tool constraints, saved-response replay, feedback, and held-out split integrity.

Implementation verification on 14 October 2026: **58 tests passed**; Ruff lint and formatting checks passed. No paid model calls were made.

The live smoke check performed on 14 October 2026 fetched arXiv, Anthropic, and the configured GitHub APIs using the pinned HTTPS transport. It used a three-record cap, so coverage was intentionally partial. Some latest llama.cpp records were excluded by the prerelease/empty-notes policy. OpenAI's index and two articles were readable; another article returned HTTP 403, which was reported without bypassing access controls. **No paid model calls were made.**

The OpenAI adapter is tested through the actual installed SDK with a mocked HTTP response. Live model behavior and real-world usefulness are not yet validated. `eval/synthetic.jsonl` has 60 fictional labeled records to exercise metrics and split checks; its results are not a real benchmark or proof that the agent improves on the baseline.

See [docs/architecture.md](docs/architecture.md) for storage and recovery decisions, and [docs/remaining-work.md](docs/remaining-work.md) for the remaining specification work and evaluation protocol.

## Fieldnotes: one shared app

The existing `llm-updates serve` command now serves the Fieldnotes interface: the
news feed reads the agent database directly, the three interactive ML guides remain
available, and **Datasets & tools** provides CSV uploads, quality dashboards,
single-feature regression, and linear-trend forecasts. No separate React dev server,
Sites account, cloud storage, or Node installation is needed to run the app.

Upload a CSV with unique headers (up to 2 MB, 10,000 rows and 50 columns). Files are
stored in `<configured database>.workspace.sqlite3`, separate from collector state.
They survive server restarts and are never included in `llm-updates site` exports.
The app remains loopback-only and preserves its Host/Origin request guards.

Prediction uses a reproducible random 80/20 split and compares MAE with a training-mean
baseline. Forecasting requires unique, evenly spaced ISO dates; it holds out the last
20%, compares with the last training value, then refits to produce future estimates.
Rows with missing selected values are excluded. These are exploratory linear models,
without seasonal modeling or uncertainty intervals. Predictions can be exported as
CSV; models are recomputed when a dataset is reopened.

### Editing the shared interface

- Python pages/server: `src/llm_updates/web/`
- Shared design: `src/llm_updates/web/static/fieldnotes.css`
- Dataset React/TypeScript source: `frontend/`
- Bundled browser asset: `src/llm_updates/web/static/datasets.js` (committed for Python-only installs)

To rebuild the dataset UI, use Node 22.13+ and run `npm ci` then `npm run build` in
`frontend/`. Run `npm test` there for numerical/CSV regression checks, and `pytest`
from the repository root for server, persistence, security-guard and collector tests.
Commit the rebuilt asset with its source. See `docs/agent-handoff.md` for coordination.
