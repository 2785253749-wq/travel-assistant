create extension if not exists vector;

create table public.rag_corpus_versions (
    corpus_version_id uuid not null primary key,
    dataset_key text not null,
    version_label text not null,
    manifest_hash text not null
        check (manifest_hash ~ '^[0-9a-f]{64}$'),
    status text not null default 'staging'
        check (status in ('staging', 'active', 'superseded', 'failed')),
    created_at timestamptz not null default now(),
    activated_at timestamptz null,
    superseded_at timestamptz null,
    unique (dataset_key, version_label)
);

create unique index rag_corpus_versions_one_active_idx
    on public.rag_corpus_versions (dataset_key)
    where status = 'active';

create table public.rag_attractions (
    attraction_id uuid not null primary key,
    lifecycle_status text not null default 'active'
        check (lifecycle_status in ('active', 'retired', 'merged')),
    created_at timestamptz not null default now(),
    retired_at timestamptz null,
    merged_into_attraction_id uuid null
        references public.rag_attractions(attraction_id)
        on delete restrict
        on update restrict,
    check (
        (lifecycle_status = 'active'
            and retired_at is null
            and merged_into_attraction_id is null)
        or (lifecycle_status = 'retired'
            and retired_at is not null
            and merged_into_attraction_id is null)
        or (lifecycle_status = 'merged'
            and merged_into_attraction_id is not null)
    ),
    check (
        merged_into_attraction_id is null
        or merged_into_attraction_id <> attraction_id
    )
);

create table public.rag_attraction_versions (
    corpus_version_id uuid not null,
    attraction_id uuid not null,
    canonical_name text not null,
    aliases jsonb not null,
    destination_code text not null
        check (destination_code ~ '^[0-9]{6}$'),
    destination_level text not null
        check (
            destination_level in (
                'province',
                'prefecture_city',
                'autonomous_prefecture',
                'county_city'
            )
        ),
    destination_name text not null,
    province_code text not null
        check (province_code ~ '^[0-9]{6}$'),
    province_name text not null,
    district_name text null,
    category text null,
    tags jsonb not null,
    latitude numeric null
        check (latitude is null or latitude between -90 and 90),
    longitude numeric null
        check (longitude is null or longitude between -180 and 180),
    status text not null
        check (status in ('included', 'suppressed')),
    metadata_hash text not null
        check (metadata_hash ~ '^[0-9a-f]{64}$'),
    primary key (corpus_version_id, attraction_id),
    foreign key (corpus_version_id)
        references public.rag_corpus_versions(corpus_version_id)
        on delete restrict
        on update restrict,
    foreign key (attraction_id)
        references public.rag_attractions(attraction_id)
        on delete restrict
        on update restrict
);

create table public.rag_attraction_chunks (
    corpus_version_id uuid not null,
    attraction_id uuid not null,
    chunk_key text not null,
    chunk_type text not null
        check (
            chunk_type in (
                'overview',
                'highlights',
                'transport',
                'visit_advice',
                'seasonal'
            )
        ),
    ordinal integer not null
        check (ordinal >= 0),
    content text not null,
    content_hash text not null
        check (content_hash ~ '^[0-9a-f]{64}$'),
    embedding_input_hash text not null
        check (embedding_input_hash ~ '^[0-9a-f]{64}$'),
    embedding_input_schema_version text not null
        check (embedding_input_schema_version = 'rag-v2-embedding-input-v1'),
    source_label text not null,
    source_url text not null,
    source_type text not null,
    reviewed_on date not null,
    embedding_model text not null
        check (embedding_model = 'jina-embeddings-v3'),
    embedding_task text not null
        check (embedding_task = 'retrieval.passage'),
    embedding_dimensions integer not null
        check (embedding_dimensions = 1024),
    embedding vector(1024) null,
    status text not null
        check (status in ('pending', 'embedded', 'failed', 'excluded')),
    embedding_error_code text null,
    embedding_error_message text null,
    primary key (corpus_version_id, chunk_key),
    foreign key (corpus_version_id, attraction_id)
        references public.rag_attraction_versions(
            corpus_version_id,
            attraction_id
        )
        on delete restrict
        on update restrict,
    unique (corpus_version_id, attraction_id, chunk_type, ordinal),
    check (
        (status = 'pending'
            and embedding is null
            and embedding_error_code is null
            and embedding_error_message is null)
        or (status = 'embedded'
            and embedding is not null
            and embedding_error_code is null
            and embedding_error_message is null)
        or (status = 'failed'
            and embedding is null
            and embedding_error_code is not null)
        or (status = 'excluded'
            and embedding is null
            and embedding_error_code is null
            and embedding_error_message is null)
    )
);

create function public.enforce_rag_v2_corpus_update()
returns trigger
language plpgsql
as $$
begin
    if new.corpus_version_id is distinct from old.corpus_version_id
       or new.dataset_key is distinct from old.dataset_key
       or new.version_label is distinct from old.version_label
       or new.manifest_hash is distinct from old.manifest_hash
       or new.created_at is distinct from old.created_at then
        raise exception 'RAG V2 corpus identity fields are immutable';
    end if;

    -- status, activated_at, and superseded_at are lifecycle-only fields.
    if old.status <> new.status then
        if old.status = 'staging' and new.status = 'active' then
            return new;
        elsif old.status = 'staging' and new.status = 'failed' then
            return new;
        elsif old.status = 'active' and new.status = 'superseded' then
            return new;
        end if;

        raise exception 'RAG V2 corpus lifecycle transition is invalid';
    end if;

    return new;
end;
$$;

create trigger rag_v2_corpus_update_guard
before update on public.rag_corpus_versions
for each row execute function public.enforce_rag_v2_corpus_update();

create function public.enforce_rag_v2_attraction_update()
returns trigger
language plpgsql
as $$
begin
    if new.attraction_id is distinct from old.attraction_id
       or new.created_at is distinct from old.created_at then
        raise exception 'RAG V2 attraction identity fields are immutable';
    end if;

    -- lifecycle_status, retired_at, and merged_into_attraction_id remain
    -- the lifecycle mutation surface; Task 1 CHECKs own row shape.
    if old.lifecycle_status = new.lifecycle_status then
        return new;
    elsif old.lifecycle_status = 'active'
          and new.lifecycle_status = 'retired' then
        return new;
    elsif old.lifecycle_status = 'active'
          and new.lifecycle_status = 'merged' then
        return new;
    end if;

    raise exception 'RAG V2 attraction lifecycle transition is invalid';
end;
$$;

create trigger rag_v2_attraction_update_guard
before update on public.rag_attractions
for each row execute function public.enforce_rag_v2_attraction_update();

create function public.enforce_rag_v2_version_mutation()
returns trigger
language plpgsql
as $$
declare
    parent_status text;
begin
    -- Mutation guard for public.rag_attraction_versions.
    if tg_op = 'UPDATE' then
        raise exception 'RAG V2 attraction version updates are forbidden';
    end if;

    if tg_op = 'DELETE' then
        select status
          into parent_status
          from public.rag_corpus_versions
         where corpus_version_id = old.corpus_version_id
           and status = 'staging';
    else
        select status
          into parent_status
          from public.rag_corpus_versions
         where corpus_version_id = new.corpus_version_id
           and status = 'staging';
    end if;

    if parent_status is distinct from 'staging' then
        raise exception 'RAG V2 attraction version mutation requires staging corpus';
    end if;

    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end;
$$;

create trigger rag_v2_version_insert_guard
before insert on public.rag_attraction_versions
for each row execute function public.enforce_rag_v2_version_mutation();

create trigger rag_v2_version_update_guard
before update on public.rag_attraction_versions
for each row execute function public.enforce_rag_v2_version_mutation();

create trigger rag_v2_version_delete_guard
before delete on public.rag_attraction_versions
for each row execute function public.enforce_rag_v2_version_mutation();

create function public.enforce_rag_v2_chunk_mutation()
returns trigger
language plpgsql
as $$
declare
    parent_status text;
begin
    -- Mutation guard for public.rag_attraction_chunks.
    if tg_op = 'INSERT' then
        select status
          into parent_status
          from public.rag_corpus_versions
         where corpus_version_id = new.corpus_version_id
           and status = 'staging';

        if parent_status is distinct from 'staging' then
            if parent_status in ('active', 'superseded', 'failed') then
                raise exception 'RAG V2 chunk insert is forbidden for terminal corpus';
            end if;
            raise exception 'RAG V2 chunk insert requires status = ''staging''';
        end if;
        return new;
    elsif tg_op = 'DELETE' then
        select status
          into parent_status
          from public.rag_corpus_versions
         where corpus_version_id = old.corpus_version_id
           and status = 'staging';

        if parent_status is distinct from 'staging' then
            if parent_status in ('active', 'superseded', 'failed') then
                raise exception 'RAG V2 chunk delete is forbidden for terminal corpus';
            end if;
            raise exception 'RAG V2 chunk delete requires status = ''staging''';
        end if;
        return old;
    end if;

    select status
      into parent_status
      from public.rag_corpus_versions
     where corpus_version_id = old.corpus_version_id
       and status = 'staging';

    if parent_status is distinct from 'staging' then
        if parent_status in ('active', 'superseded', 'failed') then
            raise exception 'RAG V2 chunk update is forbidden for terminal corpus';
        end if;
        raise exception 'RAG V2 chunk update requires status = ''staging''';
    end if;

    if new.corpus_version_id is distinct from old.corpus_version_id
       or new.attraction_id is distinct from old.attraction_id
       or new.chunk_key is distinct from old.chunk_key
       or new.chunk_type is distinct from old.chunk_type
       or new.ordinal is distinct from old.ordinal
       or new.content is distinct from old.content
       or new.content_hash is distinct from old.content_hash
       or new.embedding_input_hash is distinct from old.embedding_input_hash
       or new.embedding_input_schema_version is distinct from old.embedding_input_schema_version
       or new.source_label is distinct from old.source_label
       or new.source_url is distinct from old.source_url
       or new.source_type is distinct from old.source_type
       or new.reviewed_on is distinct from old.reviewed_on
       or new.embedding_model is distinct from old.embedding_model
       or new.embedding_task is distinct from old.embedding_task
       or new.embedding_dimensions is distinct from old.embedding_dimensions then
        raise exception 'RAG V2 chunk identity/content/profile fields are immutable';
    end if;

    if old.status = new.status then
        return new;
    elsif old.status = 'pending' and new.status = 'embedded' then
        return new;
    elsif old.status = 'pending' and new.status = 'failed' then
        return new;
    elsif old.status = 'failed' and new.status = 'pending' then
        -- failed -> pending requires embedding_error_code = null and
        -- embedding_error_message = null; Task 1 CHECKs enforce the row shape.
        if new.embedding is not null
           or new.embedding_error_code is not null
           or new.embedding_error_message is not null then
            raise exception 'RAG V2 chunk retry reset must clear embedding errors';
        end if;
        return new;
    end if;

    raise exception 'RAG V2 chunk embedding transition is invalid';
end;
$$;

create trigger rag_v2_chunk_insert_guard
before insert on public.rag_attraction_chunks
for each row execute function public.enforce_rag_v2_chunk_mutation();

create trigger rag_v2_chunk_update_guard
before update on public.rag_attraction_chunks
for each row execute function public.enforce_rag_v2_chunk_mutation();

create trigger rag_v2_chunk_delete_guard
before delete on public.rag_attraction_chunks
for each row execute function public.enforce_rag_v2_chunk_mutation();

alter table public.rag_corpus_versions enable row level security;
alter table public.rag_attractions enable row level security;
alter table public.rag_attraction_versions enable row level security;
alter table public.rag_attraction_chunks enable row level security;

revoke all on table public.rag_corpus_versions from public, anon, authenticated;
revoke all on table public.rag_attractions from public, anon, authenticated;
revoke all on table public.rag_attraction_versions from public, anon, authenticated;
revoke all on table public.rag_attraction_chunks from public, anon, authenticated;

grant select, insert, update, delete
on table public.rag_corpus_versions
to service_role;
grant select, insert, update, delete
on table public.rag_attractions
to service_role;
grant select, insert, update, delete
on table public.rag_attraction_versions
to service_role;
grant select, insert, update, delete
on table public.rag_attraction_chunks
to service_role;

create or replace function public.activate_rag_v2_corpus(
    p_dataset_key text,
    p_corpus_version_id uuid,
    p_expected_active_corpus_version_id uuid default null
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    target_dataset_key text;
    target_status text;
    current_active_corpus_version_id uuid;
begin
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'rag-v2-corpus:' || p_dataset_key,
            0
        )
    );

    select dataset_key, status
      into target_dataset_key, target_status
      from public.rag_corpus_versions
     where corpus_version_id = p_corpus_version_id;

    if not found then
        raise exception 'RAG_V2_NOT_FOUND: corpus version does not exist';
    end if;

    if target_dataset_key is distinct from p_dataset_key then
        raise exception 'RAG_V2_VERSION_CONFLICT: corpus dataset mismatch';
    end if;

    select corpus_version_id
      into current_active_corpus_version_id
      from public.rag_corpus_versions
     where dataset_key = p_dataset_key
       and status = 'active';

    if current_active_corpus_version_id = p_corpus_version_id then
        return;
    end if;

    if p_expected_active_corpus_version_id is null then
        if current_active_corpus_version_id is not null then
            raise exception 'RAG_V2_ACTIVATION_CONFLICT: active corpus changed';
        end if;
    else
        if p_expected_active_corpus_version_id is not null
           and current_active_corpus_version_id is distinct from
               p_expected_active_corpus_version_id then
            raise exception 'RAG_V2_ACTIVATION_CONFLICT: active corpus changed';
        end if;
    end if;

    if not exists (
        select 1
          from public.rag_corpus_versions
         where corpus_version_id = p_corpus_version_id
           and status = 'staging'
    ) then
        raise exception 'RAG_V2_INVALID_LIFECYCLE: target corpus is not staging';
    end if;

    if not exists (
        select 1
          from public.rag_attraction_versions
         where corpus_version_id = p_corpus_version_id
           and status = 'included'
    ) then
        raise exception 'RAG_V2_INVALID_LIFECYCLE: corpus version is not activation-ready';
    end if;

    if exists (
        select 1
          from public.rag_attraction_versions as av
         where av.corpus_version_id = p_corpus_version_id
           and av.status = 'included'
           and not exists (
               select 1
                 from public.rag_attraction_chunks as c
                where c.corpus_version_id = av.corpus_version_id
                  and c.attraction_id = av.attraction_id
                  and c.status = 'embedded'
                  and c.embedding is not null
           )
    ) then
        raise exception 'RAG_V2_INVALID_LIFECYCLE: corpus version is not activation-ready';
    end if;

    if exists (
        select 1
          from public.rag_attraction_versions as av
          join public.rag_attraction_chunks as c
            on c.corpus_version_id = av.corpus_version_id
           and c.attraction_id = av.attraction_id
         where av.corpus_version_id = p_corpus_version_id
           and av.status = 'included'
           and c.status in ('pending', 'failed')
    ) then
        raise exception 'RAG_V2_INVALID_LIFECYCLE: corpus version is not activation-ready';
    end if;

    if exists (
        select 1
          from public.rag_attraction_versions as av
          join public.rag_attractions as a
            on a.attraction_id = av.attraction_id
         where av.corpus_version_id = p_corpus_version_id
           and av.status = 'included'
           and a.lifecycle_status <> 'active'
    ) then
        raise exception 'RAG_V2_INVALID_LIFECYCLE: corpus version is not activation-ready';
    end if;

    if exists (
        select 1
          from public.rag_attraction_versions as av
          join public.rag_attraction_chunks as c
            on c.corpus_version_id = av.corpus_version_id
           and c.attraction_id = av.attraction_id
         where av.corpus_version_id = p_corpus_version_id
           and av.status = 'included'
           and (
               not (btrim(source_label) <> '')
               or not (btrim(source_url) <> '')
               or not (btrim(source_type) <> '')
               or reviewed_on is null
           )
    ) then
        raise exception 'RAG_V2_INVALID_LIFECYCLE: corpus version is not activation-ready';
    end if;

    if current_active_corpus_version_id is not null then
        update public.rag_corpus_versions
           set status = 'superseded',
               superseded_at = now()
         where corpus_version_id = current_active_corpus_version_id;
    end if;

    update public.rag_corpus_versions
       set status = 'active',
           activated_at = now()
     where corpus_version_id = p_corpus_version_id;
end;
$$;

revoke execute
on function public.activate_rag_v2_corpus(text, uuid, uuid)
from public, anon, authenticated;

grant execute
on function public.activate_rag_v2_corpus(text, uuid, uuid)
to service_role;
