from pathlib import Path


MIGRATION = Path("supabase/migrations/008_rag_knowledge.sql")


def test_rag_migration_keeps_knowledge_table_private_and_enables_vector():
    """Removing RLS or granting anon would expose the knowledge corpus."""
    assert MIGRATION.exists(), "Task 2 migration must define the private knowledge store"
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create extension if not exists vector" in sql
    assert "create table public.knowledge_chunks" in sql
    assert "alter table public.knowledge_chunks enable row level security" in sql
    assert "create policy" not in sql
    assert (
        "revoke all on table public.knowledge_chunks from public, anon, authenticated"
        in sql
    )
    assert (
        "revoke all on function public.match_knowledge_chunks(vector, text, integer) "
        "from public, anon, authenticated"
    ) in sql
    assert "grant select, insert, update on table public.knowledge_chunks to service_role" in sql
    assert (
        "grant execute on function public.match_knowledge_chunks(vector, text, integer) "
        "to service_role"
    ) in sql
    assert "to anon" not in sql
    assert "to authenticated" not in sql


def test_rag_migration_stores_versioned_chunks_with_embeddings_and_provenance():
    """Dropping a retrieval or attribution column must make this contract fail."""
    assert MIGRATION.exists(), "Task 2 migration must define the private knowledge store"
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    for column in (
        "chunk_id text primary key",
        "document_id text not null",
        "document_version text not null",
        "region text not null",
        "topic text not null",
        "content text not null",
        "source_label text not null",
        "reviewed_on date not null",
        "embedding vector(1024) not null",
    ):
        assert column in sql
