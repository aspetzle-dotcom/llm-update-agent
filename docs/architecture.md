# Architecture and implementation decisions

The CLI coordinates a single writer. Source collectors normalize input; SQLite owns immutable versions and evidence; a deterministic ranker selects a shortlist; an optional model loop proposes entries; validation and rendering publish local artifacts. An operating-system file lock serializes writers and cost reservations for each database. SQLite foreign keys are enabled on every connection.

## Data flow

1. Parse and validate TOML configuration and an optional profile override. Record the complete effective configuration and its hashes in the run.
2. Derive an idempotency key from the requested operation, window, source set, profile, fixture hash, and explicit invocation key.
3. Fetch only registered public HTTPS hosts and approved paths. Validate all DNS answers, connect directly to an approved address, and retain the original hostname for certificate verification. Redirects are explicit and revalidated. A shared persisted arXiv limiter spaces requests by at least three seconds. Retry handling is bounded and respects server instructions.
4. Commit each page's aliases, versions, evidence, and collection cursor in one SQLite transaction. Conditional-response caches retain response bodies so HTTP 304 cannot discard input. Errors store concise codes rather than raw source bodies or headers in logs.
5. Resolve explicit story relationships after collection, including forward links. Identical arXiv IDs across categories merge identities. GitHub identities use repository plus release ID. Lab identities use canonical URLs with only known tracking parameters removed. General title similarity never merges stories automatically.
6. Score relevance, recency, and novelty. Use a minimum relevance threshold and at most one entry per story. Score components and exclusion reasons remain inspectable in `run_items`.
7. Freeze the shortlisted candidates in `run_snapshots`. A resumed research attempt uses this snapshot even if other runs later add versions to the corpus.
8. Build a deterministic excerpt digest or run the experimental research loop. The controller validates tool names and shortlist IDs, keeps context bounded, and reserves paid budgets before any SDK request.
9. Validate source spans and claims. Commit the digest and its claim/evidence relationships, then export JSON and Markdown through temporary files and atomic replacement.

## Reproducibility and provenance

Content hashes include normalized text and meaningful publication metadata; retrieval time and raw HTTP bytes do not create a new version. Raw hashes are recorded separately. Exact character offsets and passage hashes identify evidence. The extractor version is explicit and an extractor change requires a migration decision. Source observations may share an item and version while retaining distinct aliases.

Retrieval, publisher publication, and publisher update times are separate. Normalization removes terminal control and bidirectional formatting characters before passage offsets are assigned. Evidence is exact relative to this normalized text. Original HTML is private diagnostic cache material, not the basis for offset claims.

An old delivered story can reappear only for a substantive content update or a correction/retraction, using a publisher update timestamp inside the window. A substantive diff currently uses conservative text-change heuristics and is saved in version metadata. A change to publication metadata alone remains inspectable without qualifying as a substantive update.

Migration `001_initial.sql` creates the schema. Migration `002_immutable_snapshots.sql` adds frozen shortlist storage and triggers that reject version/evidence mutation or deletion. This is a real migration path tested from a fresh database. Ordinary cleanup does not delete cited evidence. A separate, explicit redaction/retention maintenance command is still future work.

Live generation is not reproducible. Request schemas, sanitized prompts, model ID, request hash, raw returned response, usage, finish reason, and request ID are recorded. Successful calls can be replayed without dispatch. An interrupted or uncertain paid call stops automatic research resume and retains its reservation. Committed digests are always replayable offline without the provider SDK.

## Limits and scope

The baseline renderer publishes excerpts instead of fabricating an explanation. It never executes an experiment. The model receives evidence as untrusted objects and has no write-capable tool. Source instructions do not grant permissions.

The transport intentionally uses Python's `http.client` plus explicit socket/TLS connection handling rather than a general HTTP client for source fetching. This makes the DNS validation/connection binding inspectable and testable. The official OpenAI SDK uses its own HTTPS transport for the fixed provider endpoint. System proxy environment variables are disabled for that adapter.

Collection caps, rate limits, official-index limitations, HTTP errors, and unknown dates are visible. A full scan of a lab's current index cannot establish historical completeness; the application reports that limitation. GitHub pagination is rebuilt from the configured repository and the next page number, avoiding expanded URL permissions for GitHub's numeric repository aliases. Small pages avoid downloading oversized release metadata responses; assets are never followed.

The monthly budget is local to one database and uses UTC. Active wall time accumulates across cleanly checkpointed attempts. A process killed without cleanup can lose its final wall-time event, while paid reservations remain durable. A strict crash-surviving wall-time watchdog and account-wide budget coordination are future hardening tasks.

## Verified primary documentation

The implementation consulted these sources on 14 October 2026:

- [arXiv API terms and request pacing](https://info.arxiv.org/help/api/tou.html)
- [arXiv RSS documentation](https://info.arxiv.org/help/rss.html)
- [GitHub release endpoints](https://docs.github.com/en/rest/releases/releases)
- [GitHub rate limits and retry headers](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
- [OpenAI News index](https://openai.com/news/)
- [Anthropic Engineering index](https://www.anthropic.com/engineering)
