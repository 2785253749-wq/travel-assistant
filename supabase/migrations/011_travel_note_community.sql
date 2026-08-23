create or replace function public.generate_creator_slug()
returns text
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_candidate text;
begin
  loop
    v_candidate := 'creator-' || encode(gen_random_bytes(6), 'hex');
    if not exists (
      select 1
      from public.profiles as profile_row
      where profile_row.creator_slug = v_candidate
    ) then
      return v_candidate;
    end if;
  end loop;
end;
$$;

revoke all on function public.generate_creator_slug() from public, anon;
grant execute on function public.generate_creator_slug() to authenticated, service_role;

alter table public.profiles
  add column if not exists creator_slug text;

alter table public.profiles
  add column if not exists avatar_path text;

update public.profiles
set creator_slug = public.generate_creator_slug()
where creator_slug is null;

alter table public.profiles
  alter column creator_slug set default public.generate_creator_slug();

alter table public.profiles
  alter column creator_slug set not null;

alter table public.profiles
  add constraint profiles_creator_slug_format
  check (creator_slug ~ '^[a-z0-9-]{8,40}$');

alter table public.profiles
  add constraint profiles_avatar_path_length
  check (avatar_path is null or char_length(btrim(avatar_path)) between 5 and 500);

create unique index if not exists profiles_creator_slug_key
on public.profiles (creator_slug);

create table public.travel_notes (
  id uuid primary key default gen_random_uuid(),
  author_id uuid not null references auth.users(id) on delete cascade,
  source_trip_id uuid references public.trips(id) on delete set null,
  itinerary_snapshot jsonb check (
    itinerary_snapshot is null
    or (
      jsonb_typeof(itinerary_snapshot) = 'object'
      and public.community_public_itinerary_is_valid(itinerary_snapshot)
    )
  ),
  title text not null check (char_length(btrim(title)) between 1 and 60),
  body text not null check (char_length(btrim(body)) between 1 and 5000),
  location_name text not null check (char_length(btrim(location_name)) between 1 and 80),
  category text not null check (category in ('摄影控', '美食地图', '独自旅行', '城市漫步', '自然风光', '亲子游')),
  status text not null default 'draft' check (status in ('draft', 'pending_review', 'approved', 'rejected')),
  like_count integer not null default 0 check (like_count >= 0),
  comment_count integer not null default 0 check (comment_count >= 0),
  review_reason text check (review_reason is null or char_length(btrim(review_reason)) between 1 and 500),
  submitted_at timestamptz,
  published_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz,
  unique (id, author_id)
);

create table public.travel_note_images (
  id uuid primary key default gen_random_uuid(),
  note_id uuid not null,
  owner_id uuid not null references auth.users(id) on delete cascade,
  storage_path text not null unique check (char_length(btrim(storage_path)) between 5 and 500),
  sort_order integer not null check (sort_order between 0 and 8),
  width integer not null check (width between 1 and 8192),
  height integer not null check (height between 1 and 8192),
  created_at timestamptz not null default now(),
  foreign key (note_id, owner_id)
    references public.travel_notes(id, author_id) on delete cascade,
  unique (note_id, sort_order)
);

create table public.travel_note_likes (
  user_id uuid not null references auth.users(id) on delete cascade,
  note_id uuid not null references public.travel_notes(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, note_id)
);

create table public.travel_note_bookmarks (
  user_id uuid not null references auth.users(id) on delete cascade,
  note_id uuid not null references public.travel_notes(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, note_id)
);

create table public.travel_note_comments (
  id uuid primary key default gen_random_uuid(),
  note_id uuid not null references public.travel_notes(id) on delete cascade,
  author_id uuid not null references auth.users(id) on delete cascade,
  body text not null check (char_length(btrim(body)) between 1 and 500),
  status text not null default 'pending_review' check (status in ('pending_review', 'approved', 'rejected')),
  review_reason text check (review_reason is null or char_length(btrim(review_reason)) between 1 and 500),
  published_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);

create table public.travel_note_reports (
  id uuid primary key default gen_random_uuid(),
  reporter_id uuid not null references auth.users(id) on delete cascade,
  target_type text not null check (target_type in ('note', 'comment')),
  target_id uuid not null,
  reason text not null check (char_length(btrim(reason)) between 1 and 500),
  status text not null default 'pending' check (status in ('pending', 'dismissed', 'actioned')),
  resolution_note text check (resolution_note is null or char_length(btrim(resolution_note)) between 1 and 500),
  resolver_id uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  unique (reporter_id, target_type, target_id)
);

create table public.moderation_decisions (
  id uuid primary key default gen_random_uuid(),
  target_type text not null check (target_type in ('note', 'comment', 'report')),
  target_id uuid not null,
  moderator_id uuid not null references auth.users(id) on delete cascade,
  decision text not null check (decision in ('approved', 'rejected', 'dismissed', 'hide_content')),
  reason text check (reason is null or char_length(btrim(reason)) between 1 and 500),
  created_at timestamptz not null default now()
);

create table public.user_roles (
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null check (role in ('admin')),
  created_at timestamptz not null default now(),
  primary key (user_id, role)
);

create table public.community_media_cleanup_jobs (
  id uuid primary key default gen_random_uuid(),
  note_id uuid references public.travel_notes(id) on delete set null,
  image_id uuid references public.travel_note_images(id) on delete set null,
  storage_path text not null check (char_length(btrim(storage_path)) between 5 and 500),
  status text not null default 'pending' check (status in ('pending', 'processing', 'completed', 'failed')),
  attempts integer not null default 0 check (attempts >= 0),
  available_at timestamptz not null default now(),
  last_error text check (last_error is null or char_length(last_error) between 1 and 1000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index travel_notes_public_feed_idx
on public.travel_notes (published_at desc, id desc)
where status = 'approved' and deleted_at is null;

create index travel_notes_owner_status_idx
on public.travel_notes (author_id, status, updated_at desc, id desc)
where deleted_at is null;

create index travel_notes_review_queue_idx
on public.travel_notes (status, submitted_at asc, created_at asc, id asc)
where status = 'pending_review' and deleted_at is null;

create index travel_note_comments_public_idx
on public.travel_note_comments (note_id, published_at desc, id desc)
where status = 'approved' and deleted_at is null;

create index travel_note_comments_review_queue_idx
on public.travel_note_comments (status, created_at asc, id asc)
where status = 'pending_review' and deleted_at is null;

create index travel_note_reports_review_queue_idx
on public.travel_note_reports (status, created_at asc, id asc);

create index moderation_decisions_target_idx
on public.moderation_decisions (target_type, target_id, created_at desc);

create index community_media_cleanup_jobs_status_idx
on public.community_media_cleanup_jobs (status, available_at asc, created_at asc);

alter table public.travel_notes enable row level security;
alter table public.travel_note_images enable row level security;
alter table public.travel_note_likes enable row level security;
alter table public.travel_note_bookmarks enable row level security;
alter table public.travel_note_comments enable row level security;
alter table public.travel_note_reports enable row level security;
alter table public.moderation_decisions enable row level security;
alter table public.user_roles enable row level security;
alter table public.community_media_cleanup_jobs enable row level security;

revoke all on table public.travel_notes from public, anon, authenticated;
revoke all on table public.travel_note_images from public, anon, authenticated;
revoke all on table public.travel_note_likes from public, anon, authenticated;
revoke all on table public.travel_note_bookmarks from public, anon, authenticated;
revoke all on table public.travel_note_comments from public, anon, authenticated;
revoke all on table public.travel_note_reports from public, anon, authenticated;
revoke all on table public.moderation_decisions from public, anon, authenticated;
revoke all on table public.user_roles from public, anon, authenticated;
revoke all on table public.community_media_cleanup_jobs from public, anon, authenticated;

grant select, insert, update on table public.travel_notes to authenticated;
grant select, insert, update, delete on table public.travel_note_images to authenticated;
grant select, insert, delete on table public.travel_note_likes to authenticated;
grant select, insert, delete on table public.travel_note_bookmarks to authenticated;
grant select, insert on table public.travel_note_comments to authenticated;
grant select, insert on table public.travel_note_reports to authenticated;

grant select, insert, update, delete on table public.travel_notes to service_role;
grant select, insert, update, delete on table public.travel_note_images to service_role;
grant select, insert, update, delete on table public.travel_note_likes to service_role;
grant select, insert, update, delete on table public.travel_note_bookmarks to service_role;
grant select, insert, update, delete on table public.travel_note_comments to service_role;
grant select, insert, update, delete on table public.travel_note_reports to service_role;
grant select, insert, update, delete on table public.moderation_decisions to service_role;
grant select, insert, update, delete on table public.user_roles to service_role;
grant select, insert, update, delete on table public.community_media_cleanup_jobs to service_role;

create policy "authors view own travel notes" on public.travel_notes
for select to authenticated
using (auth.uid() = author_id);

create policy "authors create own draft travel notes" on public.travel_notes
for insert to authenticated
with check (auth.uid() = author_id);

create policy "authors edit own draft or rejected travel notes" on public.travel_notes
for update to authenticated
using (
  auth.uid() = author_id
  and status in ('draft', 'rejected')
  and deleted_at is null
)
with check (
  auth.uid() = author_id
  and deleted_at is null
);

create policy "owners view own travel note images" on public.travel_note_images
for select to authenticated
using (auth.uid() = owner_id);

create policy "owners insert images for editable travel notes" on public.travel_note_images
for insert to authenticated
with check (
  auth.uid() = owner_id
  and exists (
    select 1
    from public.travel_notes as note_row
    where note_row.id = note_id
      and note_row.author_id = owner_id
      and note_row.status in ('draft', 'rejected')
      and note_row.deleted_at is null
  )
);

create policy "owners update images for editable travel notes" on public.travel_note_images
for update to authenticated
using (
  auth.uid() = owner_id
  and exists (
    select 1
    from public.travel_notes as note_row
    where note_row.id = note_id
      and note_row.author_id = owner_id
      and note_row.status in ('draft', 'rejected')
      and note_row.deleted_at is null
  )
)
with check (
  auth.uid() = owner_id
  and exists (
    select 1
    from public.travel_notes as note_row
    where note_row.id = note_id
      and note_row.author_id = owner_id
      and note_row.status in ('draft', 'rejected')
      and note_row.deleted_at is null
  )
);

create policy "owners delete images for editable travel notes" on public.travel_note_images
for delete to authenticated
using (
  auth.uid() = owner_id
  and exists (
    select 1
    from public.travel_notes as note_row
    where note_row.id = note_id
      and note_row.author_id = owner_id
      and note_row.status in ('draft', 'rejected')
      and note_row.deleted_at is null
  )
);

create policy "users view own travel note likes" on public.travel_note_likes
for select to authenticated
using (auth.uid() = user_id);

create policy "users insert own travel note likes" on public.travel_note_likes
for insert to authenticated
with check (auth.uid() = user_id);

create policy "users delete own travel note likes" on public.travel_note_likes
for delete to authenticated
using (auth.uid() = user_id);

create policy "users view own travel note bookmarks" on public.travel_note_bookmarks
for select to authenticated
using (auth.uid() = user_id);

create policy "users insert own travel note bookmarks" on public.travel_note_bookmarks
for insert to authenticated
with check (auth.uid() = user_id);

create policy "users delete own travel note bookmarks" on public.travel_note_bookmarks
for delete to authenticated
using (auth.uid() = user_id);

create policy "authors view own travel note comments" on public.travel_note_comments
for select to authenticated
using (auth.uid() = author_id);

create policy "authors insert own travel note comments" on public.travel_note_comments
for insert to authenticated
with check (auth.uid() = author_id);

create policy "reporters view own travel note reports" on public.travel_note_reports
for select to authenticated
using (auth.uid() = reporter_id);

create policy "reporters insert own travel note reports" on public.travel_note_reports
for insert to authenticated
with check (auth.uid() = reporter_id);

create or replace function public.enforce_travel_note_client_write_rules()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  v_allow_moderation_write boolean := coalesce(
    current_setting('travel_notes.allow_moderation_write', true),
    'off'
  ) = 'on';
begin
  if v_allow_moderation_write then
    return new;
  end if;

  if tg_op = 'insert' then
    if new.status <> 'draft'
      or new.review_reason is not null
      or new.submitted_at is not null
      or new.published_at is not null
      or new.deleted_at is not null
      or new.itinerary_snapshot is not null
      or new.like_count <> 0
      or new.comment_count <> 0
    then
      raise exception 'travel note direct inserts must remain draft-owned'
        using errcode = '42501';
    end if;

    return new;
  end if;

  if old.status not in ('draft', 'rejected') or old.deleted_at is not null then
    raise exception 'approved or pending travel notes require rpc moderation flow'
      using errcode = '42501';
  end if;

  if new.review_reason is distinct from old.review_reason
    or new.submitted_at is distinct from old.submitted_at
    or new.published_at is distinct from old.published_at
    or new.deleted_at is distinct from old.deleted_at
    or new.itinerary_snapshot is distinct from old.itinerary_snapshot
    or new.like_count is distinct from old.like_count
    or new.comment_count is distinct from old.comment_count
  then
    raise exception 'travel note moderation fields are rpc controlled'
      using errcode = '42501';
  end if;

  if old.status = 'draft' and new.status <> 'draft' then
    raise exception 'draft status changes require rpc submission'
      using errcode = '42501';
  end if;

  if old.status = 'rejected' and new.status not in ('rejected', 'draft') then
    raise exception 'rejected travel notes may only return to draft directly'
      using errcode = '42501';
  end if;

  return new;
end;
$$;

create or replace function public.enforce_travel_note_image_write_rules()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  v_allow_moderation_write boolean := coalesce(
    current_setting('travel_notes.allow_moderation_write', true),
    'off'
  ) = 'on';
  v_note_id uuid := case when tg_op = 'delete' then old.note_id else new.note_id end;
  v_owner_id uuid := case when tg_op = 'delete' then old.owner_id else new.owner_id end;
begin
  if v_allow_moderation_write then
    if tg_op = 'delete' then
      return old;
    end if;

    return new;
  end if;

  perform 1
  from public.travel_notes as note_row
  where note_row.id = v_note_id
    and note_row.author_id = v_owner_id
    and note_row.status in ('draft', 'rejected')
    and note_row.deleted_at is null
  for key share;

  if not found then
    raise exception 'travel note images require editable parent note'
      using errcode = '42501';
  end if;

  if tg_op = 'delete' then
    return old;
  end if;

  return new;
end;
$$;

create trigger enforce_travel_note_client_write_rules
before insert or update on public.travel_notes
for each row execute function public.enforce_travel_note_client_write_rules();

create trigger enforce_travel_note_image_write_rules
before insert or update or delete on public.travel_note_images
for each row execute function public.enforce_travel_note_image_write_rules();

create trigger travel_notes_set_updated_at
before update on public.travel_notes
for each row execute function public.set_updated_at();

create trigger travel_note_comments_set_updated_at
before update on public.travel_note_comments
for each row execute function public.set_updated_at();

create trigger community_media_cleanup_jobs_set_updated_at
before update on public.community_media_cleanup_jobs
for each row execute function public.set_updated_at();

create or replace function public.is_community_admin()
returns boolean
language sql
security definer
set search_path = pg_catalog, public
as $$
  select exists (
    select 1
    from public.user_roles as role_row
    where role_row.user_id = auth.uid()
      and role_row.role = 'admin'
  );
$$;

revoke all on function public.is_community_admin() from public, anon, authenticated;
grant execute on function public.is_community_admin() to authenticated;

create or replace function public.list_public_travel_notes_internal(
  cursor_published_at timestamptz default null,
  cursor_id uuid default null,
  page_size integer default 20,
  category_filter text default null,
  search_query text default null
)
returns table (
  id uuid,
  creator_slug text,
  author_display_name text,
  author_avatar_path text,
  title text,
  location_name text,
  category text,
  cover_storage_path text,
  published_at timestamptz,
  like_count integer,
  comment_count integer
)
language sql
security definer
set search_path = pg_catalog, public
as $$
  select
    note_row.id,
    profile_row.creator_slug,
    coalesce(nullif(btrim(profile_row.display_name), ''), 'Voyage 旅行者') as author_display_name,
    profile_row.avatar_path as author_avatar_path,
    note_row.title,
    note_row.location_name,
    note_row.category,
    cover_image.storage_path as cover_storage_path,
    note_row.published_at,
    note_row.like_count,
    note_row.comment_count
  from public.travel_notes as note_row
  join public.profiles as profile_row
    on profile_row.user_id = note_row.author_id
  left join lateral (
    select image_row.storage_path
    from public.travel_note_images as image_row
    where image_row.note_id = note_row.id
    order by image_row.sort_order asc
    limit 1
  ) as cover_image on true
  where note_row.status = 'approved'
    and note_row.deleted_at is null
    and note_row.published_at is not null
    and (
      category_filter is null
      or category_filter = ''
      or note_row.category = btrim(category_filter)
    )
    and (
      search_query is null
      or btrim(search_query) = ''
      or note_row.title ilike '%' || btrim(search_query) || '%'
      or note_row.location_name ilike '%' || btrim(search_query) || '%'
    )
    and (
      cursor_published_at is null
      or cursor_id is null
      or note_row.published_at < cursor_published_at
      or (
        note_row.published_at = cursor_published_at
        and note_row.id < cursor_id
      )
    )
  order by note_row.published_at desc, note_row.id desc
  limit least(greatest(coalesce(page_size, 20), 1), 51);
$$;

revoke all on function public.list_public_travel_notes_internal(timestamptz, uuid, integer, text, text)
from public, anon, authenticated;
grant execute on function public.list_public_travel_notes_internal(timestamptz, uuid, integer, text, text)
to service_role;

create or replace function public.get_public_travel_note_internal(
  p_note_id uuid
)
returns table (
  id uuid,
  creator_slug text,
  author_display_name text,
  author_avatar_path text,
  title text,
  body text,
  location_name text,
  category text,
  itinerary_snapshot jsonb,
  published_at timestamptz,
  like_count integer,
  comment_count integer,
  image_manifest jsonb
)
language sql
security definer
set search_path = pg_catalog, public
as $$
  select
    note_row.id,
    profile_row.creator_slug,
    coalesce(nullif(btrim(profile_row.display_name), ''), 'Voyage 旅行者') as author_display_name,
    profile_row.avatar_path as author_avatar_path,
    note_row.title,
    note_row.body,
    note_row.location_name,
    note_row.category,
    note_row.itinerary_snapshot,
    note_row.published_at,
    note_row.like_count,
    note_row.comment_count,
    coalesce(
      jsonb_agg(
        jsonb_build_object(
          'storage_path', image_row.storage_path,
          'width', image_row.width,
          'height', image_row.height,
          'sort_order', image_row.sort_order
        )
        order by image_row.sort_order asc
      ) filter (where image_row.id is not null),
      '[]'::jsonb
    ) as image_manifest
  from public.travel_notes as note_row
  join public.profiles as profile_row
    on profile_row.user_id = note_row.author_id
  left join public.travel_note_images as image_row
    on image_row.note_id = note_row.id
  where note_row.id = p_note_id
    and note_row.status = 'approved'
    and note_row.deleted_at is null
    and note_row.published_at is not null
  group by
    note_row.id,
    profile_row.creator_slug,
    profile_row.display_name,
    profile_row.avatar_path,
    note_row.title,
    note_row.body,
    note_row.location_name,
    note_row.category,
    note_row.itinerary_snapshot,
    note_row.published_at,
    note_row.like_count,
    note_row.comment_count;
$$;

revoke all on function public.get_public_travel_note_internal(uuid)
from public, anon, authenticated;
grant execute on function public.get_public_travel_note_internal(uuid)
to service_role;

create or replace function public.submit_travel_note(
  p_note_id uuid
)
returns table (
  id uuid,
  status text,
  submitted_at timestamptz,
  published_at timestamptz,
  itinerary_snapshot jsonb
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_user_id uuid := auth.uid();
  v_note public.travel_notes;
  v_trip public.trips;
  v_image_count integer;
  v_has_cover boolean;
  v_current_image_count integer;
  v_current_has_cover boolean;
  v_snapshot jsonb;
begin
  if v_user_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;

  perform set_config('travel_notes.allow_moderation_write', 'on', true);

  select note_row.*
  into v_note
  from public.travel_notes as note_row
  where note_row.id = p_note_id
    and note_row.author_id = v_user_id
    and note_row.deleted_at is null
  for update;

  if not found then
    raise exception 'travel note not found' using errcode = 'P0002';
  end if;

  if v_note.status not in ('draft', 'rejected') then
    raise exception 'travel note is not submittable' using errcode = 'P0001';
  end if;

  select count(*), bool_or(image_row.sort_order = 0)
  into v_image_count, v_has_cover
  from public.travel_note_images as image_row
  where image_row.note_id = v_note.id
    and image_row.owner_id = v_user_id;

  if v_image_count not between 1 and 9 or not coalesce(v_has_cover, false) then
    raise exception 'travel note requires one to nine ordered images'
      using errcode = 'P0001';
  end if;

  if v_note.source_trip_id is not null then
    select trip_row.*
    into v_trip
    from public.trips as trip_row
    where trip_row.id = v_note.source_trip_id
      and trip_row.user_id = v_user_id;

    if not found then
      raise exception 'source trip not found' using errcode = 'P0002';
    end if;

    if v_trip.status <> 'planned' then
      raise exception 'source trip is not publishable' using errcode = 'P0001';
    end if;

    if v_trip.itinerary is null
      or not public.community_public_itinerary_is_valid(v_trip.itinerary)
    then
      raise exception 'source trip itinerary is not publishable'
        using errcode = 'P0001';
    end if;

    v_snapshot := v_trip.itinerary;
  else
    v_snapshot := null;
  end if;

  update public.travel_notes as note_row
  set status = 'pending_review',
      review_reason = null,
      submitted_at = now(),
      published_at = null,
      itinerary_snapshot = v_snapshot
  where note_row.id = v_note.id
    and note_row.author_id = v_user_id
    and note_row.status = v_note.status
    and note_row.deleted_at is null
    and (
      select count(*)
      from public.travel_note_images as image_row
      where image_row.note_id = v_note.id
        and image_row.owner_id = v_user_id
    ) = v_image_count
    and coalesce((
      select bool_or(image_row.sort_order = 0)
      from public.travel_note_images as image_row
      where image_row.note_id = v_note.id
        and image_row.owner_id = v_user_id
    ), false) = coalesce(v_has_cover, false)
  returning note_row.* into v_note;

  if not found then
    select count(*), bool_or(image_row.sort_order = 0)
    into v_current_image_count, v_current_has_cover
    from public.travel_note_images as image_row
    where image_row.note_id = v_note.id
      and image_row.owner_id = v_user_id;

    if v_current_image_count <> v_image_count
      or coalesce(v_current_has_cover, false) <> coalesce(v_has_cover, false)
    then
      raise exception 'travel note images changed during submission'
        using errcode = 'P0001';
    end if;

    raise exception 'travel note submission is stale' using errcode = 'P0001';
  end if;

  return query
  select
    v_note.id,
    v_note.status,
    v_note.submitted_at,
    v_note.published_at,
    v_note.itinerary_snapshot;
end;
$$;

revoke all on function public.submit_travel_note(uuid) from public, anon, authenticated;
grant execute on function public.submit_travel_note(uuid) to authenticated;

create or replace function public.review_travel_note(
  p_note_id uuid,
  decision text,
  reason text
)
returns table (
  id uuid,
  status text,
  review_reason text,
  published_at timestamptz,
  reviewed_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_moderator_id uuid := auth.uid();
  v_note public.travel_notes;
  v_reason text := nullif(btrim(reason), '');
  v_decision text := lower(btrim(decision));
  v_reviewed_at timestamptz;
begin
  if v_moderator_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;

  if not public.is_community_admin() then
    raise exception 'community admin required' using errcode = '42501';
  end if;

  perform set_config('travel_notes.allow_moderation_write', 'on', true);

  if v_decision not in ('approved', 'rejected') then
    raise exception 'invalid review decision' using errcode = 'P0001';
  end if;

  select note_row.*
  into v_note
  from public.travel_notes as note_row
  where note_row.id = p_note_id
    and note_row.status = 'pending_review'
    and note_row.deleted_at is null
  for update;

  if not found then
    raise exception 'travel note not found' using errcode = 'P0002';
  end if;

  if v_decision = 'rejected'
    and (v_reason is null or char_length(v_reason) not between 1 and 500)
  then
    raise exception 'rejection reason is required' using errcode = 'P0001';
  end if;

  v_reviewed_at := now();

  if v_decision = 'approved' then
    update public.travel_notes as note_row
    set status = 'approved',
        review_reason = null,
        published_at = v_reviewed_at
    where note_row.id = v_note.id
      and note_row.status = 'pending_review'
      and note_row.deleted_at is null
    returning note_row.* into v_note;

    if not found then
      raise exception 'travel note review is stale' using errcode = 'P0001';
    end if;

    insert into public.moderation_decisions (
      target_type,
      target_id,
      moderator_id,
      decision,
      reason
    )
    values (
      'note',
      v_note.id,
      v_moderator_id,
      v_decision,
      null
    );

    return query
    select
      v_note.id,
      v_note.status,
      v_note.review_reason,
      v_note.published_at,
      v_reviewed_at;
  end if;

  update public.travel_notes as note_row
  set status = 'rejected',
      review_reason = v_reason,
      published_at = null
  where note_row.id = v_note.id
    and note_row.status = 'pending_review'
    and note_row.deleted_at is null
  returning note_row.* into v_note;

  if not found then
    raise exception 'travel note review is stale' using errcode = 'P0001';
  end if;

  insert into public.moderation_decisions (
    target_type,
    target_id,
    moderator_id,
    decision,
    reason
  )
  values (
    'note',
    v_note.id,
    v_moderator_id,
    v_decision,
    v_reason
  );

  return query
  select
    v_note.id,
    v_note.status,
    v_note.review_reason,
    v_note.published_at,
    v_reviewed_at;
end;
$$;

revoke all on function public.review_travel_note(uuid, text, text) from public, anon, authenticated;
grant execute on function public.review_travel_note(uuid, text, text) to authenticated;

create or replace function public.review_travel_note_comment(
  p_comment_id uuid,
  decision text,
  reason text
)
returns table (
  id uuid,
  note_id uuid,
  status text,
  review_reason text,
  published_at timestamptz,
  reviewed_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_moderator_id uuid := auth.uid();
  v_comment public.travel_note_comments;
  v_parent_comment_count integer;
  v_reason text := nullif(btrim(reason), '');
  v_decision text := lower(btrim(decision));
  v_reviewed_at timestamptz;
begin
  if v_moderator_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;

  if not public.is_community_admin() then
    raise exception 'community admin required' using errcode = '42501';
  end if;

  perform set_config('travel_notes.allow_moderation_write', 'on', true);

  if v_decision not in ('approved', 'rejected') then
    raise exception 'invalid review decision' using errcode = 'P0001';
  end if;

  select comment_row.*
  into v_comment
  from public.travel_note_comments as comment_row
  where comment_row.id = p_comment_id
    and comment_row.status = 'pending_review'
    and comment_row.deleted_at is null
  for update;

  if not found then
    raise exception 'travel note comment not found' using errcode = 'P0002';
  end if;

  if v_decision = 'rejected'
    and (v_reason is null or char_length(v_reason) not between 1 and 500)
  then
    raise exception 'rejection reason is required' using errcode = 'P0001';
  end if;

  v_reviewed_at := now();

  if v_decision = 'approved' then
    update public.travel_note_comments as comment_row
    set status = 'approved',
        review_reason = null,
        published_at = v_reviewed_at
    where comment_row.id = v_comment.id
      and comment_row.status = 'pending_review'
      and comment_row.deleted_at is null
    returning comment_row.* into v_comment;

    if not found then
      raise exception 'travel note comment review is stale' using errcode = 'P0001';
    end if;

    insert into public.moderation_decisions (
      target_type,
      target_id,
      moderator_id,
      decision,
      reason
    )
    values (
      'comment',
      v_comment.id,
      v_moderator_id,
      v_decision,
      null
    );

    perform 1
    from public.travel_notes as note_row
    where note_row.id = v_comment.note_id
      and note_row.status = 'approved'
      and note_row.deleted_at is null
    for update;

    if not found then
      raise exception 'travel note comment parent is stale' using errcode = 'P0001';
    end if;

    update public.travel_notes as note_row
    set comment_count = note_row.comment_count + 1
    where note_row.id = v_comment.note_id
      and note_row.status = 'approved'
      and note_row.deleted_at is null
    returning note_row.comment_count into v_parent_comment_count;

    if not found then
      raise exception 'travel note comment parent is stale' using errcode = 'P0001';
    end if;

    return query
    select
      v_comment.id,
      v_comment.note_id,
      v_comment.status,
      v_comment.review_reason,
      v_comment.published_at,
      v_reviewed_at;
  end if;

  update public.travel_note_comments as comment_row
  set status = 'rejected',
      review_reason = v_reason,
      published_at = null
  where comment_row.id = v_comment.id
    and comment_row.status = 'pending_review'
    and comment_row.deleted_at is null
  returning comment_row.* into v_comment;

  if not found then
    raise exception 'travel note comment review is stale' using errcode = 'P0001';
  end if;

  insert into public.moderation_decisions (
    target_type,
    target_id,
    moderator_id,
    decision,
    reason
  )
  values (
    'comment',
    v_comment.id,
    v_moderator_id,
    v_decision,
    v_reason
  );

  return query
  select
    v_comment.id,
    v_comment.note_id,
    v_comment.status,
    v_comment.review_reason,
    v_comment.published_at,
    v_reviewed_at;
end;
$$;

revoke all on function public.review_travel_note_comment(uuid, text, text) from public, anon, authenticated;
grant execute on function public.review_travel_note_comment(uuid, text, text) to authenticated;


create or replace function public.set_travel_note_like_internal(
  p_note_id uuid,
  p_enabled boolean
)
returns table (
  note_id uuid,
  liked boolean,
  bookmarked boolean,
  like_count integer,
  comment_count integer
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_user_id uuid := auth.uid();
  v_note public.travel_notes;
begin
  if v_user_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;

  select note_row.*
  into v_note
  from public.travel_notes as note_row
  where note_row.id = p_note_id
    and note_row.status = 'approved'
    and note_row.deleted_at is null
  for update;

  if not found then
    raise exception 'travel note not found' using errcode = 'P0002';
  end if;

  if p_enabled then
    insert into public.travel_note_likes (user_id, note_id)
    values (v_user_id, p_note_id)
    on conflict (user_id, note_id) do nothing;
  else
    delete from public.travel_note_likes
    where user_id = v_user_id and note_id = p_note_id;
  end if;

  perform set_config('travel_notes.allow_moderation_write', 'on', true);
  update public.travel_notes as note_row
  set like_count = (
    select count(*)::integer
    from public.travel_note_likes as like_row
    where like_row.note_id = p_note_id
  )
  where note_row.id = p_note_id
  returning note_row.* into v_note;

  return query
  select
    v_note.id,
    exists (
      select 1
      from public.travel_note_likes as like_row
      where like_row.user_id = v_user_id and like_row.note_id = p_note_id
    ),
    exists (
      select 1
      from public.travel_note_bookmarks as bookmark_row
      where bookmark_row.user_id = v_user_id and bookmark_row.note_id = p_note_id
    ),
    v_note.like_count,
    v_note.comment_count;
end;
$$;

create or replace function public.set_travel_note_bookmark_internal(
  p_note_id uuid,
  p_enabled boolean
)
returns table (
  note_id uuid,
  liked boolean,
  bookmarked boolean,
  like_count integer,
  comment_count integer
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_user_id uuid := auth.uid();
  v_note public.travel_notes;
begin
  if v_user_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;

  select note_row.*
  into v_note
  from public.travel_notes as note_row
  where note_row.id = p_note_id
    and note_row.status = 'approved'
    and note_row.deleted_at is null
  for update;

  if not found then
    raise exception 'travel note not found' using errcode = 'P0002';
  end if;

  if p_enabled then
    insert into public.travel_note_bookmarks (user_id, note_id)
    values (v_user_id, p_note_id)
    on conflict (user_id, note_id) do nothing;
  else
    delete from public.travel_note_bookmarks
    where user_id = v_user_id and note_id = p_note_id;
  end if;

  return query
  select
    v_note.id,
    exists (
      select 1
      from public.travel_note_likes as like_row
      where like_row.user_id = v_user_id and like_row.note_id = p_note_id
    ),
    exists (
      select 1
      from public.travel_note_bookmarks as bookmark_row
      where bookmark_row.user_id = v_user_id and bookmark_row.note_id = p_note_id
    ),
    v_note.like_count,
    v_note.comment_count;
end;
$$;

create or replace function public.get_travel_note_interaction_state_internal(
  p_note_id uuid
)
returns table (
  note_id uuid,
  liked boolean,
  bookmarked boolean,
  like_count integer,
  comment_count integer
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_user_id uuid := auth.uid();
  v_note public.travel_notes;
begin
  if v_user_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;

  select note_row.*
  into v_note
  from public.travel_notes as note_row
  where note_row.id = p_note_id
    and note_row.status = 'approved'
    and note_row.deleted_at is null;

  if not found then
    raise exception 'travel note not found' using errcode = 'P0002';
  end if;

  return query
  select
    v_note.id,
    exists (
      select 1
      from public.travel_note_likes as like_row
      where like_row.user_id = v_user_id and like_row.note_id = p_note_id
    ),
    exists (
      select 1
      from public.travel_note_bookmarks as bookmark_row
      where bookmark_row.user_id = v_user_id and bookmark_row.note_id = p_note_id
    ),
    v_note.like_count,
    v_note.comment_count;
end;
$$;

create or replace function public.create_travel_note_comment_internal(
  p_note_id uuid,
  p_body text
)
returns table (
  id uuid,
  note_id uuid,
  author_display_name text,
  body text,
  status text,
  published_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_user_id uuid := auth.uid();
  v_body text := btrim(p_body);
  v_comment public.travel_note_comments;
begin
  if v_user_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;
  if char_length(v_body) not between 1 and 500 then
    raise exception 'comment body must be between 1 and 500 characters' using errcode = 'P0001';
  end if;
  perform 1
  from public.travel_notes as note_row
  where note_row.id = p_note_id
    and note_row.status = 'approved'
    and note_row.deleted_at is null;
  if not found then
    raise exception 'travel note not found' using errcode = 'P0002';
  end if;

  insert into public.travel_note_comments (note_id, author_id, body)
  values (p_note_id, v_user_id, v_body)
  returning * into v_comment;

  return query
  select
    v_comment.id,
    v_comment.note_id,
    coalesce(nullif(btrim(profile_row.display_name), ''), 'Voyage 旅行者'),
    v_comment.body,
    v_comment.status,
    v_comment.published_at
  from public.profiles as profile_row
  where profile_row.user_id = v_comment.author_id;

  if not found then
    return query
    select
      v_comment.id,
      v_comment.note_id,
      'Voyage 旅行者'::text,
      v_comment.body,
      v_comment.status,
      v_comment.published_at;
  end if;
end;
$$;

create or replace function public.list_public_travel_note_comments_internal(
  p_note_id uuid,
  p_cursor text default null,
  p_page_size integer default 20,
  p_viewer_id uuid default null
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_items jsonb;
begin
  if p_page_size not between 1 and 50 then
    raise exception 'comment page size must be between 1 and 50' using errcode = 'P0001';
  end if;
  perform 1
  from public.travel_notes as note_row
  where note_row.id = p_note_id
    and note_row.status = 'approved'
    and note_row.deleted_at is null;
  if not found then
    raise exception 'travel note not found' using errcode = 'P0002';
  end if;

  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'id', comment_row.id,
        'note_id', comment_row.note_id,
        'author_display_name',
          coalesce(nullif(btrim(profile_row.display_name), ''), 'Voyage 旅行者'),
        'body', comment_row.body,
        'status', comment_row.status,
        'published_at', comment_row.published_at
      )
      order by comment_row.published_at desc, comment_row.id desc
    ),
    '[]'::jsonb
  )
  into v_items
  from public.travel_note_comments as comment_row
  left join public.profiles as profile_row
    on profile_row.user_id = comment_row.author_id
  where comment_row.note_id = p_note_id
    and (
      comment_row.status = 'approved'
      or (comment_row.status = 'pending_review' and p_viewer_id is not null and comment_row.author_id = p_viewer_id)
    )
    and comment_row.deleted_at is null;

  return jsonb_build_object(
    'items', v_items,
    'next_cursor', null
  );
end;
$$;

create or replace function public.create_travel_note_report_internal(
  p_note_id uuid,
  p_target_type text,
  p_target_id uuid,
  p_reason text
)
returns table (
  id uuid,
  target_type text,
  target_id uuid,
  status text
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_user_id uuid := auth.uid();
  v_target_type text := lower(btrim(p_target_type));
  v_reason text := btrim(p_reason);
  v_report public.travel_note_reports;
begin
  if v_user_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;
  if char_length(v_reason) not between 1 and 500 then
    raise exception 'report reason must be between 1 and 500 characters' using errcode = 'P0001';
  end if;
  perform 1
  from public.travel_notes as note_row
  where note_row.id = p_note_id
    and note_row.status = 'approved'
    and note_row.deleted_at is null;
  if not found then
    raise exception 'travel note not found' using errcode = 'P0002';
  end if;

  if v_target_type = 'note' then
    if p_target_id <> p_note_id then
      raise exception 'reported note does not match path note' using errcode = 'P0002';
    end if;
  elsif v_target_type = 'comment' then
    perform 1
    from public.travel_note_comments as comment_row
    where comment_row.id = p_target_id
      and comment_row.note_id = p_note_id
      and comment_row.deleted_at is null;
    if not found then
      raise exception 'travel note comment not found' using errcode = 'P0002';
    end if;
  else
    raise exception 'invalid report target type' using errcode = 'P0001';
  end if;

  insert into public.travel_note_reports (
    reporter_id, target_type, target_id, reason
  )
  values (v_user_id, v_target_type, p_target_id, v_reason)
  on conflict (reporter_id, target_type, target_id)
  do update set reason = excluded.reason
  returning * into v_report;

  return query
  select v_report.id, v_report.target_type, v_report.target_id, v_report.status;
end;
$$;

revoke all on function public.set_travel_note_like_internal(uuid, boolean)
  from public, anon, authenticated;
grant execute on function public.set_travel_note_like_internal(uuid, boolean)
  to authenticated;
revoke all on function public.set_travel_note_bookmark_internal(uuid, boolean)
  from public, anon, authenticated;
grant execute on function public.set_travel_note_bookmark_internal(uuid, boolean)
  to authenticated;
revoke all on function public.get_travel_note_interaction_state_internal(uuid)
  from public, anon, authenticated;
grant execute on function public.get_travel_note_interaction_state_internal(uuid)
  to authenticated;
revoke all on function public.create_travel_note_comment_internal(uuid, text)
  from public, anon, authenticated;
grant execute on function public.create_travel_note_comment_internal(uuid, text)
  to authenticated;
revoke all on function public.list_public_travel_note_comments_internal(uuid, text, integer, uuid)
  from public, anon, authenticated;
grant execute on function public.list_public_travel_note_comments_internal(uuid, text, integer, uuid)
  to service_role;
revoke all on function public.create_travel_note_report_internal(uuid, text, uuid, text)
  from public, anon, authenticated;
grant execute on function public.create_travel_note_report_internal(uuid, text, uuid, text)
  to authenticated;

insert into storage.buckets (id, name, public)
values ('community-media', 'community-media', false)
on conflict (id) do update
set name = excluded.name,
    public = false;

create policy "users upload own community media"
on storage.objects for insert to authenticated
with check (
  bucket_id = 'community-media'
  and (storage.foldername(name))[1] = auth.uid()::text
);

create policy "users read own community media"
on storage.objects for select to authenticated
using (
  bucket_id = 'community-media'
  and (storage.foldername(name))[1] = auth.uid()::text
);

create policy "users update own community media"
on storage.objects for update to authenticated
using (
  bucket_id = 'community-media'
  and (storage.foldername(name))[1] = auth.uid()::text
)
with check (
  bucket_id = 'community-media'
  and (storage.foldername(name))[1] = auth.uid()::text
);

create policy "users delete own community media"
on storage.objects for delete to authenticated
using (
  bucket_id = 'community-media'
  and (storage.foldername(name))[1] = auth.uid()::text
);

create or replace function public.list_public_travel_notes_by_creator_internal(
  p_creator_slug text,
  cursor_published_at timestamptz default null,
  cursor_id uuid default null,
  page_size integer default 20
)
returns table (
  id uuid,
  creator_slug text,
  author_display_name text,
  author_avatar_path text,
  title text,
  location_name text,
  category text,
  cover_storage_path text,
  published_at timestamptz,
  like_count integer,
  comment_count integer
)
language sql
security definer
set search_path = pg_catalog, public
as $$
  select
    note_row.id,
    profile_row.creator_slug,
    coalesce(nullif(btrim(profile_row.display_name), ''), 'Voyage 旅行者'),
    profile_row.avatar_path,
    note_row.title,
    note_row.location_name,
    note_row.category,
    cover_image.storage_path,
    note_row.published_at,
    note_row.like_count,
    note_row.comment_count
  from public.travel_notes as note_row
  join public.profiles as profile_row
    on profile_row.user_id = note_row.author_id
  left join lateral (
    select image_row.storage_path
    from public.travel_note_images as image_row
    where image_row.note_id = note_row.id
    order by image_row.sort_order asc, image_row.id asc
    limit 1
  ) as cover_image on true
  where note_row.status = 'approved'
    and note_row.deleted_at is null
    and note_row.published_at is not null
    and profile_row.creator_slug = nullif(btrim(p_creator_slug), '')
    and cover_image.storage_path is not null
    and (
      cursor_published_at is null
      or note_row.published_at < cursor_published_at
      or (
        note_row.published_at = cursor_published_at
        and note_row.id < cursor_id
      )
    )
  order by note_row.published_at desc, note_row.id desc
  limit least(greatest(coalesce(page_size, 20), 1), 51);
$$;

revoke all on function public.list_public_travel_notes_by_creator_internal(
  text, timestamptz, uuid, integer
) from public, anon, authenticated;
grant execute on function public.list_public_travel_notes_by_creator_internal(
  text, timestamptz, uuid, integer
) to service_role;
