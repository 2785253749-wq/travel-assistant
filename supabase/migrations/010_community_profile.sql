create or replace function public.profile_travel_styles_are_valid(p_value jsonb)
returns boolean
language sql
immutable
set search_path = pg_catalog, public
as $$
  select case
    when p_value is null then true
    when jsonb_typeof(p_value) <> 'array' then false
    when jsonb_array_length(p_value) > 5 then false
    else not exists (
      select 1
      from jsonb_array_elements_text(p_value) as travel_style(value)
      where value not in ('美食', '人文', '自然', '亲子', '户外', '休闲')
    )
  end;
$$;

create or replace function public.community_jsonb_object_has_only_keys(
  p_value jsonb,
  p_required_keys text[],
  p_allowed_keys text[]
)
returns boolean
language sql
immutable
set search_path = pg_catalog, public
as $$
  select case
    when coalesce(jsonb_typeof(p_value), '') <> 'object' then false
    else p_value ?& p_required_keys
      and not exists (
        select 1
        from jsonb_object_keys(p_value) as object_key(key)
        where object_key.key <> all(p_allowed_keys)
      )
  end;
$$;

create or replace function public.community_jsonb_string_array_is_valid(
  p_value jsonb,
  p_max_items integer,
  p_max_length integer
)
returns boolean
language sql
immutable
set search_path = pg_catalog, public
as $$
  select case
    when coalesce(jsonb_typeof(p_value), '') <> 'array' then false
    else jsonb_array_length(p_value) <= p_max_items
      and not exists (
        select 1
        from jsonb_array_elements(p_value) as array_item(value)
        where jsonb_typeof(array_item.value) <> 'string'
          or char_length(array_item.value #>> '{}') > p_max_length
      )
  end;
$$;

create or replace function public.community_public_citations_are_valid(
  p_value jsonb,
  p_max_items integer
)
returns boolean
language sql
immutable
set search_path = pg_catalog, public
as $$
  select case
    when coalesce(jsonb_typeof(p_value), '') <> 'array' then false
    else jsonb_array_length(p_value) <= p_max_items
      and not exists (
        select 1
        from jsonb_array_elements(p_value) as citation_item(value)
        where not (
          public.community_jsonb_object_has_only_keys(
            citation_item.value,
            array['evidence_id', 'source_url', 'source_type', 'fetched_at', 'freshness'],
            array['evidence_id', 'source_url', 'source_type', 'fetched_at', 'freshness', 'fact', 'source_label']
          )
          and jsonb_typeof(citation_item.value -> 'evidence_id') = 'string'
          and char_length(btrim(citation_item.value ->> 'evidence_id')) between 1 and 200
          and jsonb_typeof(citation_item.value -> 'source_url') = 'string'
          and char_length(citation_item.value ->> 'source_url') between 8 and 2048
          and citation_item.value ->> 'source_url' ~ '^https://'
          and jsonb_typeof(citation_item.value -> 'source_type') = 'string'
          and citation_item.value ->> 'source_type' in (
            'official', 'government', 'trusted_provider'
          )
          and jsonb_typeof(citation_item.value -> 'fetched_at') = 'string'
          and char_length(citation_item.value ->> 'fetched_at') between 1 and 64
          and jsonb_typeof(citation_item.value -> 'freshness') = 'string'
          and char_length(citation_item.value ->> 'freshness') between 1 and 500
          and (
            not (citation_item.value ? 'fact')
            or (
              jsonb_typeof(citation_item.value -> 'fact') = 'string'
              and char_length(citation_item.value ->> 'fact') <= 1000
            )
          )
          and (
            not (citation_item.value ? 'source_label')
            or citation_item.value -> 'source_label' = 'null'::jsonb
            or (
              jsonb_typeof(citation_item.value -> 'source_label') = 'string'
              and char_length(btrim(citation_item.value ->> 'source_label'))
                between 1 and 200
            )
          )
        )
      )
  end;
$$;

create or replace function public.community_public_facts_are_valid(
  p_value jsonb,
  p_max_items integer
)
returns boolean
language sql
immutable
set search_path = pg_catalog, public
as $$
  select case
    when coalesce(jsonb_typeof(p_value), '') <> 'array' then false
    else jsonb_array_length(p_value) <= p_max_items
      and not exists (
        select 1
        from jsonb_array_elements(p_value) as fact_item(value)
        where not (
          public.community_jsonb_object_has_only_keys(
            fact_item.value,
            array['text', 'evidence_id'],
            array['text', 'evidence_id']
          )
          and jsonb_typeof(fact_item.value -> 'text') = 'string'
          and char_length(btrim(fact_item.value ->> 'text')) between 1 and 1000
          and jsonb_typeof(fact_item.value -> 'evidence_id') = 'string'
          and char_length(btrim(fact_item.value ->> 'evidence_id')) between 1 and 200
        )
      )
  end;
$$;

create or replace function public.community_public_itinerary_is_valid(
  p_itinerary jsonb
)
returns boolean
language plpgsql
immutable
set search_path = pg_catalog, public
as $$
declare
  v_activity jsonb;
  v_assumption jsonb;
  v_booking_links jsonb;
  v_budget jsonb;
  v_day jsonb;
  v_estimate jsonb;
  v_numeric_key text;
  v_slot text;
  v_weather jsonb;
begin
  if not public.community_jsonb_object_has_only_keys(
    p_itinerary,
    array['title', 'start_date', 'end_date', 'days', 'budget', 'assumptions'],
    array['title', 'start_date', 'end_date', 'days', 'budget', 'notes', 'assumptions', 'citations', 'booking_links']
  ) then
    return false;
  end if;

  if jsonb_typeof(p_itinerary -> 'title') <> 'string'
    or char_length(btrim(p_itinerary ->> 'title')) not between 1 and 300
    or jsonb_typeof(p_itinerary -> 'start_date') <> 'string'
    or p_itinerary ->> 'start_date' !~ '^\d{4}-\d{2}-\d{2}$'
    or jsonb_typeof(p_itinerary -> 'end_date') <> 'string'
    or p_itinerary ->> 'end_date' !~ '^\d{4}-\d{2}-\d{2}$'
  then
    return false;
  end if;

  if jsonb_typeof(p_itinerary -> 'days') <> 'array'
    or jsonb_array_length(p_itinerary -> 'days') not between 2 and 7
  then
    return false;
  end if;

  for v_day in
    select day_item.value
    from jsonb_array_elements(p_itinerary -> 'days') as day_item(value)
  loop
    if not public.community_jsonb_object_has_only_keys(
      v_day,
      array['date', 'morning', 'afternoon', 'evening'],
      array['date', 'morning', 'afternoon', 'evening', 'weather']
    ) then
      return false;
    end if;
    if jsonb_typeof(v_day -> 'date') <> 'string'
      or v_day ->> 'date' !~ '^\d{4}-\d{2}-\d{2}$'
    then
      return false;
    end if;

    foreach v_slot in array array['morning', 'afternoon', 'evening']
    loop
      v_activity := v_day -> v_slot;
      if not public.community_jsonb_object_has_only_keys(
        v_activity,
        array['title', 'start_time', 'end_time'],
        array['title', 'start_time', 'end_time', 'notes', 'facts', 'citations']
      ) then
        return false;
      end if;
      if jsonb_typeof(v_activity -> 'title') <> 'string'
        or char_length(btrim(v_activity ->> 'title')) not between 1 and 300
        or jsonb_typeof(v_activity -> 'start_time') <> 'string'
        or v_activity ->> 'start_time' !~ '^\d{2}:\d{2}$'
        or jsonb_typeof(v_activity -> 'end_time') <> 'string'
        or v_activity ->> 'end_time' !~ '^\d{2}:\d{2}$'
      then
        return false;
      end if;
      if not public.community_jsonb_string_array_is_valid(
        coalesce(v_activity -> 'notes', '[]'::jsonb), 20, 500
      ) or not public.community_public_facts_are_valid(
        coalesce(v_activity -> 'facts', '[]'::jsonb), 20
      ) or not public.community_public_citations_are_valid(
        coalesce(v_activity -> 'citations', '[]'::jsonb), 20
      ) then
        return false;
      end if;
    end loop;

    if v_day ? 'weather' and v_day -> 'weather' <> 'null'::jsonb then
      v_weather := v_day -> 'weather';
      if not public.community_jsonb_object_has_only_keys(
        v_weather,
        array['date', 'city', 'status', 'summary'],
        array['date', 'city', 'status', 'summary', 'report_time']
      ) then
        return false;
      end if;
      if jsonb_typeof(v_weather -> 'date') <> 'string'
        or v_weather ->> 'date' !~ '^\d{4}-\d{2}-\d{2}$'
        or jsonb_typeof(v_weather -> 'city') <> 'string'
        or char_length(btrim(v_weather ->> 'city')) not between 1 and 80
        or jsonb_typeof(v_weather -> 'status') <> 'string'
        or v_weather ->> 'status' not in ('available', 'unavailable', 'seasonal')
        or jsonb_typeof(v_weather -> 'summary') <> 'string'
        or char_length(btrim(v_weather ->> 'summary')) not between 1 and 500
        or (
          v_weather ? 'report_time'
          and v_weather -> 'report_time' <> 'null'::jsonb
          and jsonb_typeof(v_weather -> 'report_time') <> 'string'
        )
      then
        return false;
      end if;
    end if;
  end loop;

  v_budget := p_itinerary -> 'budget';
  if not public.community_jsonb_object_has_only_keys(
    v_budget,
    array['transport', 'hotel', 'food', 'tickets', 'reserve', 'other', 'total', 'currency', 'traveler_basis', 'traveler_count', 'trip_total', 'estimate'],
    array['transport', 'hotel', 'food', 'tickets', 'reserve', 'other', 'total', 'currency', 'traveler_basis', 'traveler_count', 'trip_total', 'estimate']
  ) then
    return false;
  end if;
  foreach v_numeric_key in array array[
    'transport', 'hotel', 'food', 'tickets', 'reserve', 'other',
    'total', 'traveler_count', 'trip_total'
  ]
  loop
    if jsonb_typeof(v_budget -> v_numeric_key) <> 'number' then
      return false;
    end if;
  end loop;
  if jsonb_typeof(v_budget -> 'currency') <> 'string'
    or v_budget ->> 'currency' <> 'CNY'
    or jsonb_typeof(v_budget -> 'traveler_basis') <> 'string'
    or v_budget ->> 'traveler_basis' not in ('trip_total', 'per_person')
  then
    return false;
  end if;

  v_estimate := v_budget -> 'estimate';
  if not public.community_jsonb_object_has_only_keys(
    v_estimate,
    array['low', 'point', 'high', 'currency', 'basis', 'assumption_id'],
    array['low', 'point', 'high', 'currency', 'basis', 'assumption_id']
  ) then
    return false;
  end if;
  foreach v_numeric_key in array array['low', 'point', 'high']
  loop
    if jsonb_typeof(v_estimate -> v_numeric_key) <> 'number' then
      return false;
    end if;
  end loop;
  if jsonb_typeof(v_estimate -> 'currency') <> 'string'
    or v_estimate ->> 'currency' <> 'CNY'
    or jsonb_typeof(v_estimate -> 'basis') <> 'string'
    or v_estimate ->> 'basis' not in ('trip_total', 'per_person')
    or jsonb_typeof(v_estimate -> 'assumption_id') <> 'string'
    or char_length(btrim(v_estimate ->> 'assumption_id')) not between 1 and 100
  then
    return false;
  end if;

  if not public.community_jsonb_string_array_is_valid(
    coalesce(p_itinerary -> 'notes', '[]'::jsonb), 40, 500
  ) then
    return false;
  end if;

  if jsonb_typeof(p_itinerary -> 'assumptions') <> 'array'
    or jsonb_array_length(p_itinerary -> 'assumptions') not between 1 and 40
  then
    return false;
  end if;
  for v_assumption in
    select assumption_item.value
    from jsonb_array_elements(p_itinerary -> 'assumptions')
      as assumption_item(value)
  loop
    if not public.community_jsonb_object_has_only_keys(
      v_assumption,
      array['assumption_id', 'category', 'description'],
      array['assumption_id', 'category', 'description']
    ) then
      return false;
    end if;
    if jsonb_typeof(v_assumption -> 'assumption_id') <> 'string'
      or char_length(btrim(v_assumption ->> 'assumption_id')) not between 1 and 100
      or jsonb_typeof(v_assumption -> 'category') <> 'string'
      or v_assumption ->> 'category' not in ('budget', 'transport', 'pacing')
      or jsonb_typeof(v_assumption -> 'description') <> 'string'
      or char_length(btrim(v_assumption ->> 'description')) not between 1 and 500
    then
      return false;
    end if;
  end loop;

  if not public.community_public_citations_are_valid(
    coalesce(p_itinerary -> 'citations', '[]'::jsonb), 100
  ) then
    return false;
  end if;

  if p_itinerary ? 'booking_links'
    and p_itinerary -> 'booking_links' <> 'null'::jsonb
  then
    v_booking_links := p_itinerary -> 'booking_links';
    if not public.community_jsonb_object_has_only_keys(
      v_booking_links,
      array['train', 'hotel', 'flight', 'disclaimer'],
      array['train', 'hotel', 'flight', 'disclaimer']
    ) then
      return false;
    end if;
    if jsonb_typeof(v_booking_links -> 'train') <> 'string'
      or v_booking_links ->> 'train' !~ '^https://www[.]12306[.]cn([/?#]|$)'
      or jsonb_typeof(v_booking_links -> 'hotel') <> 'string'
      or v_booking_links ->> 'hotel' !~ '^https://www[.]ctrip[.]com([/?#]|$)'
      or jsonb_typeof(v_booking_links -> 'flight') <> 'string'
      or v_booking_links ->> 'flight' !~ '^https://www[.]ctrip[.]com([/?#]|$)'
      or jsonb_typeof(v_booking_links -> 'disclaimer') <> 'string'
      or char_length(btrim(v_booking_links ->> 'disclaimer')) not between 1 and 500
    then
      return false;
    end if;
  end if;

  return true;
exception
  when others then
    return false;
end;
$$;

revoke all on function public.community_jsonb_object_has_only_keys(jsonb, text[], text[])
from public, anon, authenticated;
revoke all on function public.community_jsonb_string_array_is_valid(jsonb, integer, integer)
from public, anon, authenticated;
revoke all on function public.community_public_citations_are_valid(jsonb, integer)
from public, anon, authenticated;
revoke all on function public.community_public_facts_are_valid(jsonb, integer)
from public, anon, authenticated;
revoke all on function public.community_public_itinerary_is_valid(jsonb)
from public, anon, authenticated;

update public.profiles as profile_row
set preferences = (
  case
    when jsonb_typeof(profile_row.preferences) = 'object'
    then profile_row.preferences
    else '{}'::jsonb
  end
  - 'bio'
  - 'home_city'
  - 'travel_styles'
)
|| jsonb_strip_nulls(
  jsonb_build_object(
    'bio',
    case
      when jsonb_typeof(profile_row.preferences -> 'bio') = 'string'
      then left(btrim(profile_row.preferences ->> 'bio'), 160)
      else null
    end,
    'home_city',
    case
      when jsonb_typeof(profile_row.preferences -> 'home_city') = 'string'
      then left(btrim(profile_row.preferences ->> 'home_city'), 40)
      else null
    end,
    'travel_styles',
    case
      when public.profile_travel_styles_are_valid(
        profile_row.preferences -> 'travel_styles'
      )
      then profile_row.preferences -> 'travel_styles'
      else null
    end
  )
);

alter table public.profiles
  alter column preferences set default '{}'::jsonb;

alter table public.profiles
  add constraint profiles_preferences_is_object
  check (jsonb_typeof(preferences) = 'object');

alter table public.profiles
  add constraint profiles_preferences_bio_is_valid
  check (not (preferences ? 'bio') or (jsonb_typeof(preferences -> 'bio') = 'string' and char_length(btrim(preferences ->> 'bio')) <= 160));

alter table public.profiles
  add constraint profiles_preferences_home_city_is_valid
  check (not (preferences ? 'home_city') or (jsonb_typeof(preferences -> 'home_city') = 'string' and char_length(btrim(preferences ->> 'home_city')) <= 40));

alter table public.profiles
  add constraint profiles_preferences_travel_styles_are_valid
  check (not (preferences ? 'travel_styles') or (jsonb_typeof(preferences -> 'travel_styles') = 'array' and jsonb_array_length(preferences -> 'travel_styles') <= 5 and public.profile_travel_styles_are_valid(preferences -> 'travel_styles')));

create trigger profiles_set_updated_at
before update on public.profiles
for each row execute function public.set_updated_at();

create table public.community_posts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  source_trip_id uuid references public.trips(id) on delete set null,
  author_display_name text not null check (char_length(author_display_name) between 1 and 40),
  title text not null check (char_length(title) between 1 and 100),
  destination text not null check (char_length(destination) between 1 and 80),
  summary text not null check (char_length(summary) between 1 and 300),
  itinerary_snapshot jsonb not null check (
    jsonb_typeof(itinerary_snapshot) = 'object'
    and public.community_public_itinerary_is_valid(itinerary_snapshot)
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index community_posts_user_source_trip_idx
on public.community_posts (user_id, source_trip_id)
where source_trip_id is not null;

create index community_posts_created_at_id_idx
on public.community_posts (created_at desc, id desc);

alter table public.community_posts enable row level security;

revoke all on table public.community_posts from public, anon, authenticated;
grant select, delete on table public.community_posts to authenticated;

create policy "users view own community posts" on public.community_posts
for select using (auth.uid() = user_id);

create policy "users delete own community posts" on public.community_posts
for delete using (auth.uid() = user_id);

create trigger community_posts_set_updated_at
before update on public.community_posts
for each row execute function public.set_updated_at();

create or replace function public.publish_community_post(
  p_source_trip_id uuid,
  p_summary text
)
returns table (
  id uuid,
  author_display_name text,
  title text,
  destination text,
  summary text,
  itinerary_snapshot jsonb,
  created_at timestamptz,
  updated_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_user_id uuid := auth.uid();
  v_trip public.trips;
  v_author_display_name text;
  v_destination text;
  v_itinerary_snapshot jsonb;
  v_summary text := btrim(p_summary);
  v_post public.community_posts;
begin
  if v_user_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;

  if p_source_trip_id is null then
    raise exception 'source_trip_id is required' using errcode = 'P0001';
  end if;

  if v_summary is null or char_length(v_summary) not between 1 and 300 then
    raise exception 'summary must be between 1 and 300 characters'
      using errcode = 'P0001';
  end if;

  select trip_row.*
  into v_trip
  from public.trips as trip_row
  where trip_row.id = p_source_trip_id
    and trip_row.user_id = v_user_id;

  if not found then
    raise exception 'trip not found' using errcode = 'P0002';
  end if;

  if not (v_trip.status = 'planned') then
    raise exception 'trip is not publishable' using errcode = 'P0001';
  end if;

  if not public.community_public_itinerary_is_valid(v_trip.itinerary) then
    raise exception 'trip itinerary is not publishable' using errcode = 'P0001';
  end if;
  v_itinerary_snapshot := v_trip.itinerary;

  v_destination := nullif(btrim(v_trip.profile ->> 'destination'), '');
  if v_destination is null then
    raise exception 'trip destination is required' using errcode = 'P0001';
  end if;

  select coalesce(nullif(btrim(profile_row.display_name), ''), 'Voyage 旅行者')
  into v_author_display_name
  from public.profiles as profile_row
  where profile_row.user_id = v_user_id;

  v_author_display_name := coalesce(nullif(btrim(v_author_display_name), ''), 'Voyage 旅行者');

  if char_length(v_author_display_name) not between 1 and 40 then
    raise exception 'author display name must be between 1 and 40 characters'
      using errcode = 'P0001';
  end if;

  if exists (
    select 1
    from public.community_posts as post_row
    where post_row.user_id = v_user_id
      and post_row.source_trip_id = p_source_trip_id
  ) then
    raise exception 'duplicate community post' using errcode = '23505';
  end if;

  insert into public.community_posts as inserted_post (
    user_id,
    source_trip_id,
    author_display_name,
    title,
    destination,
    summary,
    itinerary_snapshot
  )
  values (
    v_user_id,
    p_source_trip_id,
    v_author_display_name,
    v_trip.title,
    v_destination,
    v_summary,
    v_itinerary_snapshot
  )
  returning inserted_post.* into v_post;

  return query
  select
    v_post.id,
    v_post.author_display_name,
    v_post.title,
    v_post.destination,
    v_post.summary,
    v_post.itinerary_snapshot,
    v_post.created_at,
    v_post.updated_at;
exception
  when unique_violation then
    raise exception 'duplicate community post' using errcode = '23505';
end;
$$;

revoke all on function public.publish_community_post(uuid, text) from public, anon, authenticated;
grant execute on function public.publish_community_post(uuid, text) to authenticated;

create or replace function public.list_community_posts(
  cursor_created_at timestamptz default null,
  cursor_id uuid default null,
  page_size integer default 20
)
returns table (
  id uuid,
  author_display_name text,
  title text,
  destination text,
  summary text,
  itinerary_snapshot jsonb,
  created_at timestamptz,
  updated_at timestamptz
)
language sql
security definer
set search_path = pg_catalog, public
as $$
  select
    post_row.id,
    post_row.author_display_name,
    post_row.title,
    post_row.destination,
    post_row.summary,
    post_row.itinerary_snapshot,
    post_row.created_at,
    post_row.updated_at
  from public.community_posts as post_row
  where (
    cursor_created_at is null
    or cursor_id is null
    or post_row.created_at < cursor_created_at
    or (
      post_row.created_at = cursor_created_at
      and post_row.id < cursor_id
    )
  )
  order by post_row.created_at desc, post_row.id desc
  limit least(greatest(coalesce(page_size, 20), 1), 51);
$$;

revoke all on function public.list_community_posts(timestamptz, uuid, integer) from public, anon, authenticated;
grant execute on function public.list_community_posts(timestamptz, uuid, integer) to anon, authenticated;

create or replace function public.get_community_post(post_id uuid)
returns table (
  id uuid,
  author_display_name text,
  title text,
  destination text,
  summary text,
  itinerary_snapshot jsonb,
  created_at timestamptz,
  updated_at timestamptz
)
language sql
security definer
set search_path = pg_catalog, public
as $$
  select
    post_row.id,
    post_row.author_display_name,
    post_row.title,
    post_row.destination,
    post_row.summary,
    post_row.itinerary_snapshot,
    post_row.created_at,
    post_row.updated_at
  from public.community_posts as post_row
  where post_row.id = post_id
  limit 1;
$$;

revoke all on function public.get_community_post(uuid) from public, anon, authenticated;
grant execute on function public.get_community_post(uuid) to anon, authenticated;
