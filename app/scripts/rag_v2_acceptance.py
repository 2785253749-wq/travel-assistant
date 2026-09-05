from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.rag_v2.acceptance import (
    DATASET_KEYS,
    AcceptanceContext,
    AcceptanceReport,
    DatasetRole,
    build_import_request,
    run_import,
    run_incremental_check,
    run_post_activation_smoke,
    run_preflight,
    run_retrieval_check,
    run_same_manifest_check,
    write_report,
    validate_dataset_key,
)
from app.rag_v2.acceptance_cases import load_acceptance_cases


class _AcceptanceCommandError(ValueError):
    """Safe operator-facing failure for a rejected command."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run RAG V2 live acceptance checks")
    subparsers = parser.add_subparsers(dest="command", required=True)

    commands = (
        "preflight",
        "smoke",
        "acceptance-import",
        "incremental-check",
        "production-import",
        "retrieval-check",
        "activation-preview",
        "activate",
        "post-activation-smoke",
    )
    for command in commands:
        command_parser = subparsers.add_parser(command)
        _add_common_arguments(command_parser)

    subparsers.choices["activate"].add_argument(
        "--role",
        choices=("smoke", "acceptance", "production"),
        required=True,
    )
    subparsers.choices["activate"].add_argument(
        "--expected-active-corpus-version-id",
        type=UUID,
    )
    subparsers.choices["activation-preview"].add_argument(
        "--current-active-corpus-version-id",
        type=UUID,
    )
    return parser


def run_cli(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        context = _build_context(args)
        result = _dispatch(args, context)
        report = _as_report(args.command, result, args)
        write_report(report=report, report_dir=args.report_dir)
        print(f"{args.command}: {report.status}")
        return 0 if report.status == "passed" else 1
    except Exception:
        failure_report = AcceptanceReport(
            run_id=args.command,
            command=args.command,
            status="failed",
            dataset_key=args.dataset_key,
            version_label=args.version_label,
            corpus_version_id=args.corpus_version_id,
            manifest_hash=args.manifest_hash,
            details={"reason": "unexpected operational failure"},
        )
        try:
            write_report(report=failure_report, report_dir=args.report_dir)
        except Exception:
            pass
        print(f"{args.command}: failed")
        return 1


def activate_dataset(
    *,
    context: AcceptanceContext,
    role: DatasetRole,
    dataset_key: str,
    corpus_version_id: UUID,
    manifest_hash: str,
    confirmation: str,
    expected_active_corpus_version_id: UUID | None,
) -> AcceptanceReport:
    validate_dataset_key(role=role, dataset_key=dataset_key)
    corpus = context.repository.get_corpus_version(
        corpus_version_id=corpus_version_id
    )
    if not _eligible_for_activation(
        context=context,
        corpus=corpus,
        dataset_key=dataset_key,
        corpus_version_id=corpus_version_id,
        manifest_hash=manifest_hash,
    ):
        raise _AcceptanceCommandError("corpus is not ready for activation")
    if confirmation != "ACTIVATE":
        raise _AcceptanceCommandError("explicit ACTIVATE confirmation is required")

    context.repository.activate_corpus(
        dataset_key=dataset_key,
        corpus_version_id=corpus_version_id,
        expected_active_corpus_version_id=expected_active_corpus_version_id,
    )
    return AcceptanceReport(
        run_id="activate",
        command="activate",
        status="passed",
        dataset_key=dataset_key,
        version_label=getattr(corpus, "version_label", None),
        corpus_version_id=corpus_version_id,
        manifest_hash=manifest_hash,
        details={"activation": "completed"},
    )


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset-key")
    parser.add_argument("--version-label")
    parser.add_argument("--corpus-version-id", type=UUID)
    parser.add_argument("--manifest-hash")
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--authoring-dir", type=Path)
    parser.add_argument("--patch-path", type=Path)
    parser.add_argument("--baseline-corpus-version-id", type=UUID)
    parser.add_argument("--baseline-version-label")
    parser.add_argument("--next-version-label")
    parser.add_argument("--report-dir", type=Path, default=Path(".rag-v2-reports"))
    parser.add_argument("--confirm", default="")


def _dispatch(args: argparse.Namespace, context: AcceptanceContext) -> object:
    command = args.command
    if command == "preflight":
        return run_preflight(context=context)
    if command in {"smoke", "acceptance-import", "production-import"}:
        role = {
            "smoke": "smoke",
            "acceptance-import": "acceptance",
            "production-import": "production",
        }[command]
        dataset_key = args.dataset_key or DATASET_KEYS[role]
        validate_dataset_key(role=role, dataset_key=dataset_key)
        args.dataset_key = dataset_key
        return run_import(context=context, request=_request_from_args(args))
    if command == "incremental-check":
        return run_incremental_check(
            context=context,
            baseline_documents=_documents_from_args(args),
            patch_path=args.patch_path,
            baseline_corpus_version_id=args.baseline_corpus_version_id,
            baseline_version_label=args.baseline_version_label,
            next_version_label=args.next_version_label,
        )
    if command == "retrieval-check":
        return run_retrieval_check(
            context=context,
            cases=_cases_from_args(args),
            dataset_key=args.dataset_key or DATASET_KEYS["production"],
            required_active_corpus_id=args.corpus_version_id,
            case_set_id=args.cases.stem if args.cases is not None else None,
            case_file_path=args.cases,
        )
    if command == "activation-preview":
        return _activation_preview(context=context, args=args)
    if command == "activate":
        return activate_dataset(
            context=context,
            role=args.role,
            dataset_key=args.dataset_key,
            corpus_version_id=args.corpus_version_id,
            manifest_hash=args.manifest_hash,
            confirmation=args.confirm,
            expected_active_corpus_version_id=args.expected_active_corpus_version_id,
        )
    if command == "post-activation-smoke":
        return run_post_activation_smoke(
            context=context,
            cases=_cases_from_args(args),
            dataset_key=args.dataset_key or DATASET_KEYS["production"],
        )
    raise _AcceptanceCommandError("unsupported acceptance command")


def _eligible_for_activation(
    *,
    context: AcceptanceContext,
    corpus: object | None,
    dataset_key: str,
    corpus_version_id: UUID,
    manifest_hash: str,
) -> bool:
    if corpus is None:
        return False
    if getattr(corpus, "corpus_version_id", None) != corpus_version_id:
        return False
    if getattr(corpus, "dataset_key", None) != dataset_key:
        return False
    if getattr(corpus, "manifest_hash", None) != manifest_hash:
        return False
    if getattr(corpus, "status", None) != "staging":
        return False
    ready = getattr(corpus, "ready_for_activation", None)
    if ready is None:
        ready = _compute_authoritative_readiness(context, corpus_version_id)
    return ready is True


def _compute_authoritative_readiness(
    context: AcceptanceContext,
    corpus_version_id: UUID,
) -> bool:
    try:
        rows = context.repository.list_chunk_rows(
            corpus_version_id=corpus_version_id
        )
    except Exception:
        return False
    if not rows:
        return False
    return all(
        getattr(getattr(row, "status", None), "value", row.status) == "embedded"
        and getattr(row, "embedding", None) is not None
        and getattr(row, "embedding_error_code", None) is None
        for row in rows
    )


def _activation_preview(
    *,
    context: AcceptanceContext,
    args: argparse.Namespace,
) -> AcceptanceReport:
    corpus = context.repository.get_corpus_version(
        corpus_version_id=args.corpus_version_id
    )
    ready = getattr(corpus, "ready_for_activation", None)
    if ready is None and corpus is not None:
        ready = _compute_authoritative_readiness(
            context,
            getattr(corpus, "corpus_version_id", args.corpus_version_id),
        )
    ready = ready is True
    rows = ()
    list_chunk_rows = getattr(context.repository, "list_chunk_rows", None)
    if corpus is not None and callable(list_chunk_rows):
        rows = tuple(
            list_chunk_rows(
                corpus_version_id=getattr(
                    corpus, "corpus_version_id", args.corpus_version_id
                )
            )
        )
    statuses = tuple(
        getattr(getattr(row, "status", None), "value", getattr(row, "status", None))
        for row in rows
    )
    return AcceptanceReport(
        run_id="activation-preview",
        command="activation-preview",
        status="passed" if corpus is not None else "failed",
        dataset_key=getattr(corpus, "dataset_key", args.dataset_key),
        version_label=getattr(corpus, "version_label", args.version_label),
        corpus_version_id=getattr(corpus, "corpus_version_id", args.corpus_version_id),
        manifest_hash=getattr(corpus, "manifest_hash", args.manifest_hash),
        details={
            "attraction_count": len(
                {
                    attraction_id
                    for attraction_id in (
                        getattr(row, "attraction_id", None) for row in rows
                    )
                    if attraction_id is not None
                }
            ),
            "chunk_count": len(rows),
            "embedded_count": statuses.count("embedded"),
            "pending_count": statuses.count("pending"),
            "failed_count": statuses.count("failed"),
            "ready_for_activation": ready,
            "current_active_corpus_version_id": args.current_active_corpus_version_id,
            "candidate_corpus_version_id": getattr(
                corpus, "corpus_version_id", args.corpus_version_id
            ),
            "retrieval acceptance": "NOT YET RUN",
        },
    )


def _as_report(
    command: str,
    result: object,
    args: argparse.Namespace,
) -> AcceptanceReport:
    if isinstance(result, AcceptanceReport):
        return result
    status = getattr(result, "status", None)
    if status not in {"passed", "failed"}:
        status = "failed" if getattr(result, "failures", ()) else "passed"
    corpus = getattr(result, "corpus", None)
    return AcceptanceReport(
        run_id=command,
        command=command,
        status=status,
        dataset_key=getattr(corpus, "dataset_key", args.dataset_key),
        version_label=getattr(corpus, "version_label", args.version_label),
        corpus_version_id=getattr(corpus, "corpus_version_id", args.corpus_version_id),
        manifest_hash=getattr(corpus, "manifest_hash", args.manifest_hash),
        details={"result": result},
    )


def _cases_from_args(args: argparse.Namespace) -> tuple[object, ...]:
    if args.cases is None:
        return ()
    return load_acceptance_cases(args.cases)


def _request_from_args(args: argparse.Namespace) -> object:
    from app.rag_v2.authoring import load_authoring_directory
    from app.rag_v2.chunking import SemanticChunker

    if (
        args.dataset_key is None
        or args.version_label is None
        or args.corpus_version_id is None
    ):
        if args.authoring_dir is None:
            return None
        raise _AcceptanceCommandError(
            "dataset, version, and corpus identity are required"
        )
    authoring_dir = args.authoring_dir or Path("app/rag_v2/content/production")
    documents = load_authoring_directory(authoring_dir)
    return build_import_request(
        documents=documents,
        dataset_key=args.dataset_key,
        version_label=args.version_label,
        corpus_version_id=args.corpus_version_id,
        chunker=SemanticChunker(),
    )


def _documents_from_args(args: argparse.Namespace) -> tuple[object, ...]:
    from app.rag_v2.authoring import load_authoring_directory

    authoring_dir = args.authoring_dir or Path("app/rag_v2/content/production")
    return load_authoring_directory(authoring_dir)


def _build_context(args: argparse.Namespace) -> AcceptanceContext:
    from app.core.config import get_settings
    from app.rag_v2.embedding import JinaQueryEmbedder, JinaQueryProvider
    from app.rag_v2.importer import RagV2Importer
    from app.rag_v2.passage_embedding import JinaPassageEmbedder, JinaPassageProvider
    from app.rag_v2.repository import RagV2Repository
    from app.rag_v2.retrieval import RetrievalService

    from app.rag_v2.authoring import load_authoring_directory
    from app.rag_v2.chunking import SemanticChunker

    settings = get_settings()
    if settings.jina_api_key is None:
        raise _AcceptanceCommandError("JINA_API_KEY is missing")
    repository = RagV2Repository(settings=settings)
    passage_provider = JinaPassageProvider(
        api_key=settings.jina_api_key,
        timeout_seconds=settings.weather_timeout_seconds,
    )
    query_provider = JinaQueryProvider(
        api_key=settings.jina_api_key,
        timeout_seconds=settings.weather_timeout_seconds,
    )
    passage_embedder = JinaPassageEmbedder(provider=passage_provider)
    query_embedder = JinaQueryEmbedder(provider=query_provider)

    authoring_dir = args.authoring_dir or Path("app/rag_v2/content/production")
    documents = load_authoring_directory(authoring_dir)
    identity_values = {
        attraction.registry_key: attraction.metadata.attraction_id
        for document in documents
        for attraction in document.attractions
    }

    importer = RagV2Importer(
        identity_source=_StaticIdentitySource(identity_values),
        repository=repository,
        chunker=SemanticChunker(),
        passage_embedder=passage_embedder,
    )
    return AcceptanceContext(
        settings=settings,
        repository=repository,
        importer=importer,
        passage_embedder=passage_embedder,
        query_embedder=query_embedder,
        retrieval_service=RetrievalService(
            embedder=query_embedder,
            repository=repository,
        ),
        report_dir=args.report_dir,
    )


@dataclass
class _StaticIdentitySource:
    values: dict[str, UUID]

    def resolve(self, registry_key: str) -> UUID | None:
        return self.values.get(registry_key)

    def allocate(self, registry_key: str) -> UUID:
        raise _AcceptanceCommandError(
            f"unknown authored registry key: {registry_key}"
        )


if __name__ == "__main__":
    raise SystemExit(run_cli())
