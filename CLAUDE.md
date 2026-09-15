# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A grounded customer-support RAG agent: FastAPI + PostgreSQL/pgvector backend (`backend/`), React/Vite/Tailwind frontend (`frontend/`). The core invariant: **an answer is returned only if it is supported by retrieved, validated evidence; every other path abstains or hands off to a human.** Don't add code paths that call the LLM without evidence, return unvalidated text, or relax validation to make a case pass - add an eval case instead. Design docs: `docs/architecture.md`, `docs/rag.md`, `docs/security.md`.

## Commands

Backend (run from `backend/`, uv-managed Python 3.14 - verify new dependencies ship 3.14 wheels):

```bash
uv sync --extra s3                                   # install (dev group included)
docker compose up -d db                              # from repo root: Postgres+pgvector, creates support_test DB
uv run alembic upgrade head                          # migrations are explicit, never at app startup
uv run python -m app.cli seed                        # demo tenants/users/knowledge base (dev credentials in README)
uv run uvicorn app.main:create_app --factory --reload
uv run pytest -m "not integration"                   # unit tests (no DB, no network)
uv run pytest -m integration                         # needs DB; includes E2E workflows and the golden RAG eval
uv run pytest tests/unit/test_support_agent.py::test_unanswerable_question_abstains_without_citations
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run python -m app.cli eval                        # RAG evaluation report + thresholds
uv run python scripts/ask.py -v "question" ["follow-up"]   # run the real agent against the seeded KB, print decision/trace
uv run python scripts/live_acceptance.py --base-url http://localhost:8080   # acceptance scenarios against a running stack (creates data)
uv run alembic check                                 # models vs migrations drift
```

Frontend (from `frontend/`): `npm run dev` (proxies `/api` to :8000), `npm test`, `npm run lint`, `npm run typecheck`, `npm run build`.

Full stack: `cp .env.example .env` (set `JWT_SECRET`), `docker compose up --build`, then `docker compose exec backend python -m app.cli seed`; UI on :8080, Swagger on :8000/docs.

## Architecture essentials

- **Composition root** `app/core/container.py` builds everything from settings; API, worker, CLI, evals and tests share it. Tests inject fakes (`tests/helpers.py`: `ScriptedLLMProvider`, `InMemoryRetriever`, `build_agent`) rather than patching.
- **Chat turn** (`services/chat.py`): commit user message → run `SupportAgent.respond` with no DB transaction open → persist assistant message + `RagTrace` + handoff atomically. SSE streams only after validation and persistence.
- **Agent pipeline** (`agents/support_agent.py`): query analysis (`rag/query_analysis.py`, rules always run; LLM can add but not remove safety flags) → deterministic policy (`agents/policy.py`) → `PgHybridRetriever` (pgvector + FTS, RRF; tenant/active-version/embedding-model filters are in SQL) → `HeuristicReranker` → `select_evidence` (relevance, coverage, required entities) → `detect_conflicts`/`resolve_conflicts` → generator (`LLMAnswerGenerator` or offline `ExtractiveAnswerGenerator`) → `GroundingValidator` (+ LLM judge) with exactly one stricter retry → `assess_confidence`.
- **Offline mode** (`LLM_PROVIDER=none`, `EMBEDDING_PROVIDER=hashing`) is the default and what CI uses. Text utilities in `utils/text.py` (stemming, number extraction) are shared by indexing, ranking and validation - **changing them changes hashing embeddings, so reindex** (`python -m app.cli reindex-all --force`).
- **Conversation lifecycle** (`services/lifecycle.py`): resolve / reopen-on-customer-reply / close (by a person or automatically via `workers/maintenance.py`) each record actor + time and add a SYSTEM transcript event. `ChatService.create` continues a recent non-closed chat with the same opening question instead of duplicating it; lists hide conversations with no messages.
- **Ingestion** (`ingestion/pipeline.py`): DB is the queue (`FOR UPDATE SKIP LOCKED`); chunks, vectors, READY and activation commit together; retrieval reads only `documents.active_version_id`.
- **Vectors** live in `chunk_embeddings` keyed by model with a dimensionless column and per-dimension partial HNSW indexes on `embedding::vector(N)`; retriever SQL interpolates only the integer dimension.
- **Prompts** are versioned Markdown in `app/prompts/` (front matter `name`/`version`/`purpose`); bump `version` on any text change. Anthropic provider uses the official SDK with `output_config.format` and no sampling params.
- **Multi-tenancy**: every tenant row has `company_id`; customers are additionally scoped by `user_id`; foreign resources return 404.
- **Errors**: raise `app/core/errors.py` classes (safe `message`, log-only `detail`); handlers in `api/errors.py`.

## Conventions

- Ruff (line length 120, formatter owns wrapping) and mypy are configured in `backend/pyproject.toml`; ESLint + `tsc` for the frontend.
- Tunable thresholds belong in `core/config.py` with a comment explaining the default, and in `.env.example`.
- Keep source ASCII: tool-written `\uXXXX` escapes become literal characters; use `chr(0x2013)` or code-point maps.
- Integration tests refuse any database not named `*_test` and truncate all tables per test.
