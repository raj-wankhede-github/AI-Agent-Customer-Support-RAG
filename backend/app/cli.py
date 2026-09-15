"""Operational commands.

python -m app.cli seed [--skip-documents]   Demo tenants, users and knowledge base (development)
python -m app.cli ingest-pending            Process queued document versions now
python -m app.cli reindex-all [--force]     Re-embed documents (e.g. after changing EMBEDDING_MODEL)
python -m app.cli ensure-vector-index       Create the HNSW index for VECTOR_DIMENSION
python -m app.cli purge-conversations       Apply CONVERSATION_RETENTION_DAYS
python -m app.cli create-user ...           Create a user (password read from an env var)
python -m app.cli eval [...]                Run the RAG evaluation suite
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select, text

from app.core.config import Settings, get_settings
from app.core.container import Container, build_container
from app.models import Company, Document, DocumentVersion, User
from app.models.enums import DocumentStatus, SourceAuthority, UserRole
from app.observability.logging import configure_logging
from app.security.principal import Principal
from app.services.auth import create_user
from app.services.knowledge import KnowledgeService, UploadMetadata
from app.services.retention import purge_conversations

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"

# Local development credentials only. Refused when APP_ENV=production.
DEV_PASSWORDS = {"admin": "AcmeAdmin!2026", "agent": "AcmeAgent!2026", "customer": "AcmeCustomer!2026"}


def _password(settings: Settings, kind: str) -> str:
    configured = getattr(settings, f"seed_{kind}_password")
    if configured and configured.get_secret_value():
        return str(configured.get_secret_value())
    if settings.app_env == "production":
        raise SystemExit(f"Refusing to seed default credentials in production: set SEED_{kind.upper()}_PASSWORD")
    return DEV_PASSWORDS[kind]


async def _company(container: Container, slug: str, name: str) -> Company:
    async with container.sessions() as session:
        company = (await session.execute(select(Company).where(Company.slug == slug))).scalar_one_or_none()
        if company is None:
            company = Company(slug=slug, name=name)
            session.add(company)
            await session.commit()
        return company


async def _user(container: Container, company: Company, email: str, name: str, role: UserRole, password: str) -> User:
    async with container.sessions() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = await create_user(
                session, company_id=company.id, email=email, name=name, role=role, password=password
            )
            await session.commit()
            print(f"  created {role.value:<8} {email}")
        return user


def _principal(user: User, company: Company) -> Principal:
    return Principal(
        user.id, company.id, company.name, UserRole(user.role), user.email, user.name, "cli", datetime.now(UTC)
    )


async def ingest_documents(container: Container, principal: Principal, manifest_path: Path) -> None:
    manifest = json.loads(await asyncio.to_thread(manifest_path.read_text, encoding="utf-8"))
    service = KnowledgeService(container.settings, container.storage)
    for entry in manifest["documents"]:
        path = manifest_path.parent / entry["file"]
        meta = UploadMetadata(
            title=entry.get("title"), authority=SourceAuthority(entry.get("authority", "SUPPORT_ARTICLE")),
            category=entry.get("category"), product=entry.get("product"), locale=entry.get("locale"),
            effective_date=date.fromisoformat(entry["effective_date"]) if entry.get("effective_date") else None,
            source_uri=entry.get("source_uri"),
        )  # fmt: skip
        data = await asyncio.to_thread(path.read_bytes)
        async with container.sessions() as session:
            result = await service.upload(
                session, principal, filename=path.name, content_type=None, data=data, meta=meta
            )
        print(f"  {'exists ' if result.duplicate else 'queued '} {result.document.title}")
    await process_queue(container)


async def process_queue(container: Container) -> int:
    processed = 0
    while (version_id := await container.pipeline.claim_next()) is not None:
        status = await container.pipeline.process_version(version_id)
        async with container.sessions() as session:
            version = await session.get(DocumentVersion, version_id)
            document = await session.get(Document, version.document_id) if version else None
        suffix = f" ({version.error_code}: {version.error_message})" if version and version.error_message else ""
        print(f"  {status.value:<10} {document.title if document else version_id}{suffix}")
        processed += 1
    return processed


async def cmd_seed(container: Container, args: argparse.Namespace) -> None:
    settings = container.settings
    print("Seeding tenants and users (development credentials - see README)")
    acme = await _company(container, "acme", "Acme Support")
    admin = await _user(
        container, acme, "admin@acme.example", "Avery Admin", UserRole.ADMIN, _password(settings, "admin")
    )
    await _user(container, acme, "agent@acme.example", "Sam Agent", UserRole.AGENT, _password(settings, "agent"))
    await _user(
        container, acme, "customer@acme.example", "Casey Customer", UserRole.CUSTOMER, _password(settings, "customer")
    )
    globex = await _company(container, "globex", "Globex Corporation")
    globex_admin = await _user(
        container, globex, "admin@globex.example", "Gale Globex", UserRole.ADMIN, _password(settings, "admin")
    )
    await _user(
        container, globex, "customer@globex.example", "Gray Globex", UserRole.CUSTOMER, _password(settings, "customer")
    )
    if args.skip_documents:
        return
    print("Ingesting Acme knowledge base")
    await ingest_documents(container, _principal(admin, acme), SEED_DIR / "acme" / "manifest.json")
    print("Ingesting Globex knowledge base (separate tenant)")
    await ingest_documents(container, _principal(globex_admin, globex), SEED_DIR / "globex" / "manifest.json")


async def cmd_ingest_pending(container: Container, args: argparse.Namespace) -> None:
    print(f"Processed {await process_queue(container)} version(s)")


async def cmd_reindex_all(container: Container, args: argparse.Namespace) -> None:
    service = KnowledgeService(container.settings, container.storage)
    async with container.sessions() as session:
        rows = (
            await session.execute(
                select(Document, DocumentVersion.embedding_model)
                .join(DocumentVersion, DocumentVersion.id == Document.active_version_id, isouter=True)
                .where(Document.status != DocumentStatus.DELETED)
            )
        ).all()
    queued = 0
    for document, model in rows:
        if args.force or model != container.embedder.model:
            async with container.sessions() as session:
                await service.reindex(session, None, document.id, document.company_id)
            queued += 1
    print(f"Queued {queued} document(s) for reindexing with {container.embedder.model}")
    await process_queue(container)


async def cmd_ensure_vector_index(container: Container, args: argparse.Namespace) -> None:
    dim = int(container.settings.vector_dimension)
    async with container.engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chunk_embeddings_hnsw_{dim} ON chunk_embeddings "
            f"USING hnsw ((embedding::vector({dim})) vector_cosine_ops) WHERE dimension = {dim}"
        ))  # fmt: skip
    print(f"HNSW index for dimension {dim} is present")


async def cmd_purge(container: Container, args: argparse.Namespace) -> None:
    days = args.days if args.days is not None else container.settings.conversation_retention_days
    async with container.sessions() as session:
        count = await purge_conversations(session, days, dry_run=args.dry_run)
    print(f"{'Would delete' if args.dry_run else 'Deleted'} {count} conversation(s) older than {days} day(s)")


async def cmd_create_user(container: Container, args: argparse.Namespace) -> None:
    password = os.environ.get(args.password_env)
    if not password or len(password) < 12:
        raise SystemExit(f"Set a password of at least 12 characters in ${args.password_env}")
    company = await _company(container, args.company_slug, args.company_name or args.company_slug)
    await _user(container, company, args.email.lower(), args.name, UserRole(args.role), password)


async def cmd_close_resolved(container: Container, args: argparse.Namespace) -> None:
    from app.services.lifecycle import auto_close_resolved

    count = await auto_close_resolved(container.sessions, container.settings)
    print(
        f"Closed {count} resolved conversation(s) with no reply for {container.settings.resolved_auto_close_days} day(s)"
    )


async def cmd_eval(container: Container, args: argparse.Namespace) -> None:
    from app.evals.runner import run_evaluation

    code = await run_evaluation(
        container,
        dataset=Path(args.dataset) if args.dataset else None,
        output=Path(args.output) if args.output else None,
    )
    if code:
        raise SystemExit(code)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed")
    seed.add_argument("--skip-documents", action="store_true")
    sub.add_parser("ingest-pending")
    reindex = sub.add_parser("reindex-all")
    reindex.add_argument("--force", action="store_true", help="Reindex even when the embedding model is unchanged")
    sub.add_parser("ensure-vector-index")
    sub.add_parser("close-resolved", help="Close resolved conversations with no reply for RESOLVED_AUTO_CLOSE_DAYS")
    purge = sub.add_parser("purge-conversations")
    purge.add_argument("--days", type=int)
    purge.add_argument("--dry-run", action="store_true")
    user = sub.add_parser("create-user")
    user.add_argument("--company-slug", required=True)
    user.add_argument("--company-name")
    user.add_argument("--email", required=True)
    user.add_argument("--name", required=True)
    user.add_argument("--role", choices=[r.value for r in UserRole], required=True)
    user.add_argument("--password-env", default="NEW_USER_PASSWORD")
    evaluation = sub.add_parser("eval")
    evaluation.add_argument("--dataset")
    evaluation.add_argument("--output")
    args = parser.parse_args(argv)

    handlers = {
        "seed": cmd_seed, "ingest-pending": cmd_ingest_pending, "reindex-all": cmd_reindex_all,
        "ensure-vector-index": cmd_ensure_vector_index, "purge-conversations": cmd_purge,
        "create-user": cmd_create_user, "eval": cmd_eval, "close-resolved": cmd_close_resolved,
    }  # fmt: skip
    settings = get_settings()
    configure_logging("WARNING", json_logs=False, service="cli")

    async def run() -> None:
        container = build_container(settings)
        try:
            await handlers[args.command](container, args)
        finally:
            await container.aclose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
