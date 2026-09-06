create or replace function public.match_rag_v2_chunks(
    p_dataset_key text,
    p_query_embedding vector(1024),
    p_destination_code text default null,
    p_destination_level text default null,
    p_province_code text default null,
    p_attraction_id uuid default null,
    p_candidate_k integer default 40
)
returns table (
    corpus_version_id uuid,
    attraction_id uuid,
    chunk_key text,
    chunk_type text,
    content text,
    content_hash text,
    source_label text,
    source_url text,
    source_type text,
    reviewed_on date,
    score real
)
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
    active_count bigint;
    active_corpus_version_id uuid;
begin
    if p_query_embedding is null then
        raise exception 'RAG V2 query embedding is required';
    end if;

    if p_candidate_k is null
       or p_candidate_k < 1
       or p_candidate_k > 200 then
        raise exception 'RAG V2 candidate_k is out of range';
    end if;

    select count(*)
      into active_count
      from public.rag_corpus_versions as rcv
     where rcv.dataset_key = p_dataset_key
       and rcv.status = 'active';

    if active_count = 0 then
        return;
    elsif active_count > 1 then
        raise exception 'RAG V2 active corpus invariant is violated';
    end if;

    select rcv.corpus_version_id
      into active_corpus_version_id
      from public.rag_corpus_versions as rcv
     where rcv.dataset_key = p_dataset_key
       and rcv.status = 'active';

    return query
    with candidates as (
        select
            c.corpus_version_id,
            c.attraction_id,
            c.chunk_key,
            c.chunk_type,
            c.content,
            c.content_hash,
            c.source_label,
            c.source_url,
            c.source_type,
            c.reviewed_on,
            c.embedding <=> p_query_embedding as distance
        from public.rag_attraction_chunks as c
        join public.rag_attraction_versions as av
          on av.corpus_version_id = c.corpus_version_id
         and av.attraction_id = c.attraction_id
        join public.rag_attractions as a
          on a.attraction_id = c.attraction_id
        where c.corpus_version_id = active_corpus_version_id
          and av.corpus_version_id = active_corpus_version_id
          and av.status = 'included'
          and a.lifecycle_status = 'active'
          and c.status = 'embedded'
          and c.embedding is not null
          and (
              p_destination_code is null
              or av.destination_code = p_destination_code
          )
          and (
              p_destination_level is null
              or av.destination_level = p_destination_level
          )
          and (
              p_province_code is null
              or av.province_code = p_province_code
          )
          and (
              p_attraction_id is null
              or c.attraction_id = p_attraction_id
          )
        order by c.embedding <=> p_query_embedding asc
        limit p_candidate_k
    )
    select
        cand.corpus_version_id,
        cand.attraction_id,
        cand.chunk_key,
        cand.chunk_type,
        cand.content,
        cand.content_hash,
        cand.source_label,
        cand.source_url,
        cand.source_type,
        cand.reviewed_on,
        (1 - cand.distance)::real as score
    from candidates as cand
    order by
        (1 - cand.distance)::real desc,
        cand.attraction_id asc,
        cand.chunk_key asc;
end;
$$;
