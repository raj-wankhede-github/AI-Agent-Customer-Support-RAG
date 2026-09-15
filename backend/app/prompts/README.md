# Prompts

Versioned system prompts used when `LLM_PROVIDER` is `anthropic` or `openai`.
In offline mode (`LLM_PROVIDER=none`) no prompt is sent anywhere; deterministic
components take their place (see `docs/rag.md`).

Each file has front matter with `name`, `version` and `purpose`. The loaded version
(`name@version`) is stored on every assistant message and RAG trace. **Bump `version`
whenever the prompt text changes** so historical answers stay attributable.

| Prompt | Purpose |
| --- | --- |
| `query_understanding` | Rewrite a follow-up into a standalone retrieval query and classify intent, entities, sensitivity and handoff signals (merges the query-rewrite and intent-classifier steps into one call to save latency and cost). |
| `answer_generator` | Produce a grounded answer as claims that each cite evidence IDs, or abstain. |
| `answer_generator_strict` | Addendum used for the single retry after validation fails. |
| `grounding_validator` | LLM judge that checks each claim against its cited evidence (runs in addition to deterministic checks). |
| `reranker` | Optional LLM relevance scoring of retrieval candidates (`RERANKER_PROVIDER=llm`). |
| `conversation_summarizer` | Compress older turns into conversation memory (never treated as company facts). |

User content is assembled in code (`app/rag/prompt_builder.py`): conversation memory,
evidence and the customer question are placed in separate, explicitly labelled
sections, and evidence is marked as untrusted data.
