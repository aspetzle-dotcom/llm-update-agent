# Remaining work and evaluation protocol

The deterministic application is usable now. The experimental provider/controller code is implemented and covered by mocks. The complete six-milestone specification still requires the following work before claiming a validated research agent.

## Provider and research acceptance

- Choose an account-accessible OpenAI model and verify its current pricing, context size, and structured-output support. Configure rates and a key locally, then perform a small explicitly budgeted live trial.
- Evaluate whether model explanations improve usefulness over source excerpts. The application does not claim a measured agent advantage.
- Add coordinator-mediated network enrichment if evidence-gap evaluation justifies it. Currently `fetch_registered_item` returns the already-collected version; it cannot fetch additional URLs. Add immutable link registrations and crash-safe enrichment replay before enabling network reads from this tool.
- Refine semantic grounding review. Exact excerpt and numeric-context checks are mechanical; a human must review whether the excerpt supports the interpretation and whether numeric conditions are adequate.
- Consider a non-agent single-call explanation mode for comparison. The current modes are deterministic baseline and experimental research.

## Collection and operational hardening

- Review official index selectors as those sites change. Full historical completeness is not established by the current index adapters. OpenAI returned an article-level HTTP 403 during the smoke test; the tool reports it and preserves earlier pages.
- Add optional GitHub token support if unauthenticated rate limits constrain use. No credentials are presently sent to source APIs.
- Add automated pause/re-enable behavior after repeated source schema changes. Currently each source fails visibly and preserves its last committed checkpoint.
- Implement an explicit retention/redaction maintenance path. Normal execution never deletes evidence or versions.
- Make wall-time accounting durable even across an uncatchable process termination. Current paid-call reservations survive termination; active-time accounting is committed at the end of an attempt.
- Add explicit correction and withdrawal discovery beyond source-provided/normalized flags. The ranking exception exists, but automatic retraction classification is not implemented.
- Add a user-facing audited story-group correction command. Grouping currently uses exact identities and explicit normalized relationships; the repository operation preserves historical digest records.
- Add concept-specific exposure suppression only after a concept representation has been evaluated. Already-knew feedback is recorded and exposed through scoped prior coverage; it does not currently downweight a broad topic.

## Real evaluation

`eval/synthetic.jsonl` contains 60 original fictional records over four days. It exists to test evaluation mechanics, including story isolation between training and later test days. It is not independent evidence of real-world quality.

For the first genuine evaluation:

1. Capture 50–100 permitted public-source items over multiple days, including irrelevant stories, cross-posts, changed versions, contradictions, and incomplete evidence. Keep private raw material outside version control.
2. Label relevance before looking at rankings: irrelevant, useful background, or worth including. Record story group, known exposure, topic, and evidence sufficiency. Have another reader label a subset if available.
3. Keep all versions and linked members of a story in one split. Reserve later days for test data; freeze the profile, weights, thresholds, prompts, and budgets first.
4. Run newest-first, keyword-plus-recency, and the full baseline. The CLI evaluation harness currently reports per-day precision at five, returned-item precision, eligible relevant stories, selection coverage, and within-day duplicates.
5. Extend the harness to compare saved model outputs and compute cross-day duplicate rate, source-by-source ingestion coverage, factual grounding, disagreements, token/cost totals, and ablations. Those metrics are not yet claimed by the current harness.
6. Audit every model factual claim and every numerical claim. Record measured sample sizes, failures, and missing evidence. Do not count a model's self-check as independent review.
7. Enable the agent by default only if it improves selection or grounding within the same budgets. Otherwise retain the baseline and consider using a model only for explanation.

UI, scheduling, embeddings, additional providers, and multi-agent orchestration remain outside this first release.
