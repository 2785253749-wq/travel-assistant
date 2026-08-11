create extension if not exists vector;

create table public.knowledge_chunks (
  chunk_id text primary key,
  document_id text not null,
  document_version text not null,
  region text not null,
  topic text not null,
  content text not null,
  source_label text not null,
  reviewed_on date not null,
  embedding vector(1024) not null,
  imported_at timestamptz not null default now(),
  unique (document_id, document_version, chunk_id)
);

alter table public.knowledge_chunks enable row level security;

create table public.rag_embedding_daily_usage (
  usage_date date primary key,
  used integer not null default 0 check (used >= 0)
);

alter table public.rag_embedding_daily_usage enable row level security;

create index knowledge_chunks_region_idx on public.knowledge_chunks (region);
create index knowledge_chunks_embedding_idx on public.knowledge_chunks
  using ivfflat (embedding vector_cosine_ops) with (lists = 10);

create function public.match_knowledge_chunks(
  query_embedding vector(1024),
  filter_region text default null,
  match_count integer default 5
)
returns table (
  chunk_id text,
  content text,
  source_label text,
  score real
)
language sql
stable
security invoker
set search_path = public
as $$
  select
    knowledge_chunks.chunk_id,
    knowledge_chunks.content,
    knowledge_chunks.source_label,
    (1 - (knowledge_chunks.embedding <=> query_embedding))::real as score
  from public.knowledge_chunks
  where filter_region is null or knowledge_chunks.region = filter_region
  order by knowledge_chunks.embedding <=> query_embedding
  limit least(greatest(match_count, 1), 20)
$$;

create function public.reserve_rag_embedding_quota(
  requested integer,
  daily_limit integer
)
returns boolean
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  reserved boolean;
begin
  if requested <= 0 or daily_limit <= 0 then
    return false;
  end if;

  insert into public.rag_embedding_daily_usage (usage_date, used)
  select timezone('UTC', now())::date, requested where requested <= daily_limit
  on conflict (usage_date) do update
  set used = rag_embedding_daily_usage.used + excluded.used
  where rag_embedding_daily_usage.used + requested <= daily_limit
  returning true into reserved;

  return coalesce(reserved, false);
end;
$$;

revoke all on table public.knowledge_chunks from public, anon, authenticated;
revoke all on table public.rag_embedding_daily_usage from public, anon, authenticated;
revoke all on function public.match_knowledge_chunks(vector, text, integer) from public, anon, authenticated;
revoke all on function public.reserve_rag_embedding_quota(integer, integer) from public, anon, authenticated;
grant select, insert, update on table public.knowledge_chunks to service_role;
grant execute on function public.match_knowledge_chunks(vector, text, integer) to service_role;
grant execute on function public.reserve_rag_embedding_quota(integer, integer) to service_role;
