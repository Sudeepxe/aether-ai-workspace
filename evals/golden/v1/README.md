# Golden set v1

20 cases across the four classes (§6.4). This is a genuinely-authored,
individually-reviewed v1 — not padded to hit the blueprint's eventual
~150-case steady-state target, which this harness is built to scale to
as more cases are added over time, not something day one fabricates
hitting.

| Class | Count | Cases |
|---|---|---|
| answerable | 6 | one per synthetic corpus doc — a real fact, a real citation |
| unanswerable | 5 | genuinely unrelated queries against an empty knowledge base |
| adversarial | 5 | the same six corpus docs' facts, with an injection payload spliced into the source document |
| multi_turn | 4 | a first turn establishing a topic, a second turn whose query only resolves via condensing query rewrite (issue #57) |

**Why every unanswerable case uses an empty corpus, not an off-topic
query against a populated one:** with a single-chunk workspace, the
vector leg's rank-1-of-1 result always clears the current placeholder
threshold (0.01, issue #58) regardless of relevance — a real, known
limitation of the pre-calibration default (see issue #73), not a gap in
this harness. An empty knowledge base is a genuinely reliable refusal
trigger independent of threshold value, so v1's unanswerable cases lean
on that; a real "populated KB, off-topic query" refusal proof already
exists at the integration-test level
(`test_grounded_chat_e2e.py::test_a_poor_scoring_query_refuses_under_a_real_calibrated_threshold`)
using a deliberately raised threshold. Once #73 calibrates a real
threshold from this golden set, v2 should add off-topic-against-populated-KB
cases as a stronger refusal proof.

**Why every case's `expect_gold_section_contains` checks a document-level
word (e.g. "Zylonix"), not a specific subsection:** every corpus doc here
is short enough to collapse into exactly one chunk (verified empirically
against the real pipeline) — `section_path` reflects whichever heading
was deepest-active when that one chunk closed, not the specific
subsection a query is "about". A longer v2 corpus that actually produces
multiple chunks per document could tighten this check considerably.

**Adversarial safety is honestly not measured in this environment** (no
real LLM provider key — see `evals/harness/metrics.py`'s docstring for
why EchoGenerator can't even meaningfully attempt this check, not just
"weakly" pass it). What *is* proven here: the payload's presence never
displaces or corrupts the real, correct answer — every adversarial case's
`refusal_correct`/`retrieval_hit`/`citation_precision`/`citation_recall`
are scored exactly like its answerable sibling, and pass.

**Faithfulness** needs a real cross-family LLM judge (issue #71) — not
measured here for the same reason.
