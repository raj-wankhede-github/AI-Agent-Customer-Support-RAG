# RAG design

The pipeline is built so that the cheapest, most deterministic checks run first, the
language model only ever sees vetted evidence, and every exit that is not a validated answer
is an abstention or a handoff. Code: `backend/app/agents/support_agent.py` and `backend/app/rag/`.

## 1. Ingestion and chunking

**Extraction** (`ingestion/loaders/`) produces typed blocks (heading, paragraph, list,
table) with page numbers where the format has pages:

| Format | Structure preserved |
| --- | --- |
| PDF | Text layer per page; headings inferred (short lines in title case/caps/numbered, or ending a finished sentence); lists; page numbers |
| DOCX | Heading styles, list paragraphs, tables (rows as `cell | cell`), document order |
| HTML | `h1-h6`, paragraphs, lists, tables; scripts, styles, nav, header, footer, forms removed; canonical URL kept |
| Markdown | Front matter, ATX headings, lists, tables, code blocks |
| TXT | Blank-line paragraphs, uppercase/title-case headings, lists |

**Normalization:** NFKC, unified dashes/quotes, control characters removed, hyphenated line
breaks rejoined, whitespace collapsed. **Metadata:** title (front matter, first H1, PDF
metadata or filename), page count, character count, effective date ("Effective date:
2026-01-01" / "effective as of March 5, 2025"), canonical URL.

**Structure-aware chunking** (`ingestion/chunker.py`):

- A chunk never crosses a heading boundary; each chunk records its heading path
  (`Refund Policy > International Purchases`), section title and page range.
- Paragraphs, lists and tables are never split unless a single one exceeds the hard cap;
  then paragraphs split by sentence, lists by item, tables by row **with the header row
  repeated**.
- Defaults: `CHUNK_TARGET_TOKENS=350`, `CHUNK_MAX_TOKENS=500`, `CHUNK_OVERLAP_TOKENS=50`.
  350 tokens (~1,400 characters) holds one policy subsection or FAQ answer with its
  conditions; 500 is a hard ceiling so a chunk rarely mixes topics; ~50 tokens of trailing
  sentences carry context when a long section must be split. Token counts are estimated at
  four characters per token (budgeting only).
- Chunks are embedded with a contextual header (`document title > heading path`) while the
  stored content stays verbatim, so short chunks remain attributable.
- Instruction-like text is flagged per chunk (`metadata.injection_flags`).

## 2. Embeddings

`EmbeddingProvider` implementations: `hashing` (offline), `openai`, `bedrock` (Titan v2).
The model name and dimension are stored on every vector and version. Retrieval compares
only vectors of the configured model and dimension, and only versions indexed with that
model are eligible, so embeddings from incompatible models are never mixed. Changing
model: `reindex-all` builds new versions; each document keeps serving its current version
until the new one is ready.

The **hashing embedder** signs-hashes stemmed terms, adjacent-term bigrams (weight 0.5) and
character 4-grams (weight 0.2) into `VECTOR_DIMENSION` and L2-normalizes. It is
deterministic and offline, captures lexical overlap and some morphology, and does **not**
capture paraphrase. Use a hosted model in production.

## 3. Query understanding

`RuleBasedQueryAnalyzer` always runs; with an LLM, `LLMQueryAnalyzer` (prompt
`query_understanding`) adds a model reading. Safety flags merge with OR: the model can add a
handoff, sensitivity or frustration signal but never remove one the rules found, and
injection flags come only from the deterministic detector.

- **Standalone rewrite.** Elliptical follow-ups are resolved against the previous
  standalone question: "What about international purchases?" after "What is your refund
  policy?" becomes "What is your refund policy for international purchases?". Pronoun
  questions carry the previous topic. The retriever receives only the standalone query,
  never the whole conversation.
- **Signals:** intent (question, greeting, thanks, handoff request, account action),
  entities, question type, sensitivity (legal, financial, medical, security incident,
  identity verification, account ownership), frustration, dispute of the previous answer,
  clarification need (no searchable terms, or an unresolved pronoun with no context).
- **Key terms:** stemmed content words minus stopwords and asking-words ("information",
  "exactly", "long"). **Entity terms:** key terms of named entities ("Antarctica",
  "SmartHub").

## 4. Pre-retrieval policy

Deterministic (`agents/policy.py`), in order: injection → refusal; explicit human request →
handoff; security incident → urgent handoff; identity/ownership → handoff; account-specific
action ("where is my order #123") → handoff; dispute → handoff; strong frustration →
handoff; greeting/thanks → conversational reply; vague → clarifying question. Legal,
financial and medical questions continue, but retrieval is restricted to
`OFFICIAL_POLICY` / `OFFICIAL_DOCUMENTATION` and only a HIGH-confidence answer is returned;
otherwise handoff.

## 5. Hybrid retrieval

`PgHybridRetriever` runs two queries in one transaction, both restricted in SQL to the
caller's tenant, active documents, the active READY version, and the current embedding
model (plus optional product/category/locale/authority filters):

1. **Vector:** cosine distance on `embedding::vector(N)` using the partial HNSW index,
   `hnsw.ef_search = 100` (candidates are post-filtered, so a larger list than the default
   40), `RAG_TOP_K=20` results. Vector-only candidates below `RAG_MIN_SCORE=0.15` cosine
   similarity are dropped.
2. **Full-text:** `to_tsquery('english', t1 | t2 | ...)` over a weighted `tsvector`
   (heading path A, content B), ranked with `ts_rank_cd`, top `RAG_TOP_K`. Terms are
   `[a-z0-9]` tokens only, so no query-syntax injection.

Results are fused with **Reciprocal Rank Fusion** (k = 60): a chunk ranked well by both
retrievers beats one ranked first by only one. Vector similarity is computed for every
fused candidate for the reranker.

## 6. Reranking

`Reranker` protocol; `HeuristicReranker` by default, `LLMReranker` (prompt `reranker`,
blended 50/50 with the heuristic, falling back on failure) with `RERANKER_PROVIDER=llm`.

Heuristic score (weights configurable, must sum to 1):

| Signal | Weight | Why |
| --- | --- | --- |
| Query-term coverage (mean of plain and IDF-weighted over the candidate set), over content + title + heading path + product | 0.45 | It is what validation later checks; IDF keeps a term every candidate shares from dominating |
| Vector similarity | 0.35 | Rescues paraphrase (with a semantic embedder) |
| Title/section match | 0.10 | A heading naming the subject is a strong relevance signal |
| Source authority | 0.10 | Tiebreaker toward official sources |

Near-duplicates (same content hash, or token Jaccard ≥ `RAG_DEDUP_SIMILARITY=0.85`) are
removed, keeping the higher-ranked copy.

## 7. Evidence sufficiency

`select_evidence` decides **before generation** whether an answer is possible:

1. No candidates → `NO_RELEVANT_EVIDENCE`.
2. None with reranker score ≥ `RAG_MIN_RERANK_SCORE=0.35` → `LOW_RELEVANCE`.
3. Select up to `RAG_MAX_CONTEXT=5` passing chunks within `RAG_MAX_CONTEXT_TOKENS=3000`.
   A few good chunks beat a lot of noisy context.
4. Any named entity in the question not mentioned anywhere in the selected evidence →
   `MISSING_ENTITY` ("How long does shipping to Antarctica take?" must not be answered
   with the generic shipping time).
5. Share of key terms covered < `RAG_MIN_QUERY_COVERAGE=0.6` → `INSUFFICIENT_COVERAGE`.
   With a semantic embedder, `RAG_SEMANTIC_OVERRIDE_SIMILARITY` lets a very similar chunk
   satisfy coverage despite different wording.

Insufficient evidence → abstain (and hand off after `HANDOFF_FAILED_ANSWERS_THRESHOLD=2`
consecutive failures).

## 8. Contradiction handling

`rag/conflicts.py`. Two sentences from **different documents** conflict when they share
question terms, have similar surrounding wording (content-word Jaccard ≥
`CONFLICT_SENTENCE_SIMILARITY=0.4` after removing the quantities) and state different
quantities of the same kind (money, percent, days/weeks/months/years/hours/minutes).
Different facts that merely both contain numbers ("request a refund within 30 days" vs
"refunds are processed within 5 business days") do not match.

Resolution, in order: a source whose effective date is in the future loses to one already
in effect; a higher `SOURCE_AUTHORITY_RANKING` wins; for equal authority the more recent
effective date wins. The losing document is removed from the evidence and the resolution is
recorded in the trace. If nothing distinguishes the sources, the agent does not choose:
`KNOWLEDGE_CONFLICT` handoff. In provider mode the model can also flag a conflict, with the
same outcome.

## 9. Grounded generation

**LLM mode** (`LLMAnswerGenerator`, prompt `answer_generator`): the user content has three
labelled sections - conversation memory (low authority, never facts), evidence sources with
IDs and metadata (untrusted data, escaped), and the question. The model returns strict
JSON: `answer`, `claims[] {text, evidence_ids}`, `should_abstain`, `abstain_reason`,
`conflict_detected`, `handoff_required`, `handoff_reason`. The schema is generated from the
Pydantic model (closed objects, all fields required) and sent as native structured output
(Anthropic `output_config.format`, OpenAI `json_schema` strict). Malformed output gets one
corrective retry, then fails safe. Evidence is trimmed to `LLM_MAX_INPUT_TOKENS` by dropping
the lowest-ranked sources first.

**Offline mode** (`ExtractiveAnswerGenerator`): scores evidence sentences by key-term
coverage from the sentence itself plus its section heading (terms from the document title are
ignored because every chunk shares them), with bonuses for concrete values (+0.15) and for
headings that name the subject (+0.1). Instruction-like and boilerplate sentences ("Effective
date...", "This policy applies to...") are skipped; short fragments (list items) count only when
they name most of the question. A heading-matched section is returned whole (up to 4
sentences); otherwise sentences are added only when they cover new terms (up to 3). If the
best sentence covers less than a third of the question, it abstains.

## 10. Validation

Every answer, from either generator, passes `GroundingValidator`:

- at least one claim with citations, and only IDs of sources actually retrieved;
- each claim supported by its **own** cited sources (≥ `GROUNDING_MIN_CLAIM_SUPPORT=0.6` of
  its content words present);
- each answer sentence supported by the sources cited for the matching claim, and **every
  number** (normalized: "3–5" = "3-5", "$1,200.00" = "1200", "thirty" = "30"), **month**,
  **email/URL/phone** and **capitalized entity** in it present in those sources;
- no system-prompt canary in the output.

In provider mode an LLM judge (prompt `grounding_validator`) then checks every claim against
its cited evidence and fails closed if unavailable. On failure the generator is called once
more with the list of problems and stricter instructions; a second failure becomes a
`VALIDATION_FAILED` handoff. The failed answer is never returned.

## 11. Confidence

Deterministic (`rag/confidence.py`), never a model's self-assessment:

```
score = 0.35 × best reranker relevance + 0.25 × query coverage
      + 0.15 × authority of cited sources + 0.25 × minimum claim support
```

`HIGH` ≥ 0.75, `MEDIUM` ≥ 0.55 (configurable), `LOW` otherwise. Failed validation or an
unresolved conflict is `ABSTAIN` regardless of score. Only HIGH and MEDIUM validated answers
are returned; LOW abstains. MEDIUM answers show a gentle "check the details" note in the UI.

## 12. Citations

One citation per cited source, built from the retrieved chunk itself: document title,
version, source type and URI, authority, section and heading path, page number, chunk index,
effective date, and the excerpt (the chunk sentences that best support the claims citing it).
Citation metadata is never taken from model output, so it cannot be fabricated; the UI
renders only citations present in the response.

## 13. Abstention and handoff outcomes

| Situation | `answer_status` | Handoff reason |
| --- | --- | --- |
| Validated answer, HIGH/MEDIUM confidence | `ANSWERED` | - |
| No / weak / insufficient / entity-missing evidence, model abstained, LOW confidence | `ABSTAINED` | - (after repeated failures: `REPEATED_FAILED_ANSWERS`) |
| Customer prompt injection | `ABSTAINED` (refusal) | - |
| Human requested (message or button) | `HANDOFF_REQUIRED` | `USER_REQUESTED` |
| Frustration / dispute | `HANDOFF_REQUIRED` | `USER_FRUSTRATED` / `USER_DISPUTED_ANSWER` |
| Security incident, identity, account ownership | `HANDOFF_REQUIRED` | `SENSITIVE_REQUEST` |
| Account-specific action | `HANDOFF_REQUIRED` | `ACCOUNT_SPECIFIC` |
| Unresolvable contradiction | `HANDOFF_REQUIRED` | `KNOWLEDGE_CONFLICT` |
| Validation failed twice | `HANDOFF_REQUIRED` | `VALIDATION_FAILED` |
| LLM failure / refusal / timeout | `HANDOFF_REQUIRED` | `PROVIDER_FAILURE` |
| Retrieval failure | `HANDOFF_REQUIRED` | `RETRIEVAL_FAILURE` |
| Human owns the conversation | `AWAITING_HUMAN` | - |

## 14. Conversation memory

The last `CONVERSATION_HISTORY_MESSAGES=6` turns are sent verbatim (each truncated to 800
characters); older turns are folded into a rolling summary (LLM prompt
`conversation_summarizer` or an extractive "customer asked X (answered / not answered)"
list, capped at 1,500 characters). Memory is labelled low-authority and the summarizer is
told not to restate facts, so earlier assistant text can never become evidence.

## 15. Traceability

Each assistant message stores `retrieval_metadata` (kind, reason, retrieval count, top score,
selected sources, validation result, confidence, prompt versions, stage timings), provider,
model, prompt versions, latency and token usage, and links to a `rag_traces` row with the
original and standalone query, analysis, candidates with vector/lexical ranks and reranker
scores, selected evidence, sufficiency, conflicts, validation issues, confidence components
and timings. Staff see this in the conversation inspector and in the retrieval debugger.
Chain-of-thought is never requested, stored or shown.

## 16. Evaluation

`python -m app.cli eval` (and the `test_evals.py` integration test) - see the README. Metrics:

- **Retrieval:** Recall@K and Precision@K over expected source documents in the top
  `RAG_MAX_CONTEXT` candidates; MRR of the first relevant document.
- **Generation:** decision accuracy; answer rate and correctness on answerable questions
  (required facts present, forbidden strings absent); groundedness (validated answers /
  answers); citation correctness (cited documents within the expected sources); abstention
  accuracy on questions that must not be answered.
- **Safety:** unsupported-answer rate (answers to questions that should not be answered),
  hallucination rate (answers that failed grounding validation, contain forbidden content or
  cite sources outside the expected set), off-target answer rate (accurate, cited answers that
  miss the specific fact asked for), prompt-injection resistance, handoff accuracy.

Thresholds live in the dataset; the run fails if any is violated. Add cases whenever a
failure is found in production feedback (the dashboard lists recent unanswered questions and
not-helpful reasons).

## 17. Known limitations and tuning

- Offline mode is lexical: synonyms ("delivery" vs "shipping") reduce coverage and cause
  abstention. A hosted embedder plus `RAG_SEMANTIC_OVERRIDE_SIMILARITY` and LLM query
  understanding address this.
- The stemmer and stopwords are English-only; add a language-aware analyzer for other locales.
- Lexical claim support cannot detect every semantic distortion (e.g. a dropped condition);
  the LLM judge covers this in provider mode.
- Contradiction detection targets conflicting quantities; conflicting qualitative statements
  ("refundable" vs "non-refundable") rely on the model's `conflict_detected` flag.
- Tune with the retrieval debugger and the evaluation report: raise `RAG_MIN_RERANK_SCORE` or
  `RAG_MIN_QUERY_COVERAGE` to abstain more, lower them to answer more, and watch the
  unsupported-answer rate.
