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
