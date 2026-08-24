-- Task 12: authenticated administrator moderation queues and audit actions.
-- Queue projections expose image metadata only. The API server resolves image
-- IDs through its service-role client and returns short-lived signed URLs.

create or replace function public.list_pending_travel_notes_for_moderation(
  p_cursor_time timestamptz default null,
  p_cursor_id uuid default null,
  p_page_size integer default 20
)
returns table (
  id uuid,
  title text,
  body text,
  location_name text,
  category text,
  status text,
  review_reason text,
  submitted_at timestamptz,
  author_display_name text,
  image_manifest jsonb
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if auth.uid() is null or not public.is_community_admin() then
    raise exception 'community administrator access is required' using errcode = 'P0001';
  end if;

  return query
  select
    note_row.id,
    note_row.title,
    note_row.body,
    note_row.location_name,
    note_row.category,
    note_row.status,
    note_row.review_reason,
    note_row.submitted_at,
    coalesce(nullif(btrim(profile_row.display_name), ''), 'Voyage 旅行者'),
    coalesce(
      jsonb_agg(
        jsonb_build_object(
          'id', image_row.id,
          'sort_order', image_row.sort_order,
          'width', image_row.width,
          'height', image_row.height
        ) order by image_row.sort_order
      ) filter (where image_row.id is not null),
      '[]'::jsonb
    )
  from public.travel_notes as note_row
  left join public.profiles as profile_row on profile_row.user_id = note_row.author_id
  left join public.travel_note_images as image_row on image_row.note_id = note_row.id
  where note_row.status = 'pending_review'
    and note_row.deleted_at is null
    and (p_cursor_time is null or note_row.submitted_at > p_cursor_time or (note_row.submitted_at = p_cursor_time and note_row.id > p_cursor_id))
  group by note_row.id, profile_row.display_name
  order by note_row.submitted_at asc, note_row.id asc
  limit least(greatest(coalesce(p_page_size, 20), 1), 101);
end;
$$;

create or replace function public.list_pending_travel_note_comments_for_moderation(
  p_cursor_time timestamptz default null,
  p_cursor_id uuid default null,
  p_page_size integer default 20
)
returns table (
  id uuid,
  note_id uuid,
  author_display_name text,
  body text,
  status text,
  review_reason text,
  created_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if auth.uid() is null or not public.is_community_admin() then
    raise exception 'community administrator access is required' using errcode = 'P0001';
  end if;

  return query
  select
    comment_row.id,
    comment_row.note_id,
    coalesce(nullif(btrim(profile_row.display_name), ''), 'Voyage 旅行者'),
    comment_row.body,
    comment_row.status,
    comment_row.review_reason,
    comment_row.created_at
  from public.travel_note_comments as comment_row
  left join public.profiles as profile_row on profile_row.user_id = comment_row.author_id
  where comment_row.status = 'pending_review'
    and comment_row.deleted_at is null
    and (p_cursor_time is null or comment_row.created_at > p_cursor_time or (comment_row.created_at = p_cursor_time and comment_row.id > p_cursor_id))
  order by comment_row.created_at asc, comment_row.id asc
  limit least(greatest(coalesce(p_page_size, 20), 1), 101);
end;
$$;

create or replace function public.list_pending_travel_note_reports_for_moderation(
  p_cursor_time timestamptz default null,
  p_cursor_id uuid default null,
  p_page_size integer default 20
)
returns table (
  id uuid,
  target_type text,
  target_id uuid,
  reason text,
  status text,
  resolution_note text,
  created_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if auth.uid() is null or not public.is_community_admin() then
    raise exception 'community administrator access is required' using errcode = 'P0001';
  end if;

  return query
  select
    report_row.id,
    report_row.target_type,
    report_row.target_id,
    report_row.reason,
    report_row.status,
    report_row.resolution_note,
    report_row.created_at
  from public.travel_note_reports as report_row
  where report_row.status = 'pending'
    and (p_cursor_time is null or report_row.created_at > p_cursor_time or (report_row.created_at = p_cursor_time and report_row.id > p_cursor_id))
  order by report_row.created_at asc, report_row.id asc
  limit least(greatest(coalesce(p_page_size, 20), 1), 101);
end;
$$;

create or replace function public.get_travel_note_moderation_item(p_note_id uuid)
returns table (
  id uuid,
  title text,
  body text,
  location_name text,
  category text,
  status text,
  review_reason text,
  submitted_at timestamptz,
  author_display_name text,
  image_manifest jsonb
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if auth.uid() is null or not public.is_community_admin() then
    raise exception 'community administrator access is required' using errcode = 'P0001';
  end if;

  return query
  select
    note_row.id,
    note_row.title,
    note_row.body,
    note_row.location_name,
    note_row.category,
    note_row.status,
    note_row.review_reason,
    note_row.submitted_at,
    coalesce(nullif(btrim(profile_row.display_name), ''), 'Voyage 旅行者'),
    coalesce(
      jsonb_agg(
        jsonb_build_object(
          'id', image_row.id,
          'sort_order', image_row.sort_order,
          'width', image_row.width,
          'height', image_row.height
        ) order by image_row.sort_order
      ) filter (where image_row.id is not null),
      '[]'::jsonb
    )
  from public.travel_notes as note_row
  left join public.profiles as profile_row on profile_row.user_id = note_row.author_id
  left join public.travel_note_images as image_row on image_row.note_id = note_row.id
  where note_row.id = p_note_id and note_row.deleted_at is null
  group by note_row.id, profile_row.display_name;
end;
$$;

create or replace function public.get_travel_note_comment_moderation_item(p_comment_id uuid)
returns table (
  id uuid,
  note_id uuid,
  author_display_name text,
  body text,
  status text,
  review_reason text,
  created_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if auth.uid() is null or not public.is_community_admin() then
    raise exception 'community administrator access is required' using errcode = 'P0001';
  end if;

  return query
  select
    comment_row.id,
    comment_row.note_id,
    coalesce(nullif(btrim(profile_row.display_name), ''), 'Voyage 旅行者'),
    comment_row.body,
    comment_row.status,
    comment_row.review_reason,
    comment_row.created_at
  from public.travel_note_comments as comment_row
  left join public.profiles as profile_row on profile_row.user_id = comment_row.author_id
  where comment_row.id = p_comment_id and comment_row.deleted_at is null;
end;
$$;

create or replace function public.resolve_travel_note_report(
  p_report_id uuid,
  p_decision text,
  p_resolution_note text default null
)
returns table (
  id uuid,
  target_type text,
  target_id uuid,
  reason text,
  status text,
  resolution_note text,
  created_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_report public.travel_note_reports;
  v_decision text := lower(btrim(p_decision));
  v_resolution_note text := nullif(btrim(p_resolution_note), '');
begin
  if auth.uid() is null or not public.is_community_admin() then
    raise exception 'community administrator access is required' using errcode = 'P0001';
  end if;
  if v_decision not in ('dismissed', 'actioned') then
    raise exception 'invalid report resolution' using errcode = 'P0001';
  end if;

  select * into v_report from public.travel_note_reports where id = p_report_id for update;
  if not found then
    raise exception 'moderation report not found' using errcode = 'P0002';
  end if;
  if v_report.status <> 'pending' then
    raise exception 'moderation report is no longer pending' using errcode = 'P0001';
  end if;

  update public.travel_note_reports as report_row
  set status = v_decision,
      resolution_note = v_resolution_note,
      resolver_id = auth.uid(),
      resolved_at = now()
  where report_row.id = p_report_id
  returning report_row.* into v_report;

  -- actioned closes the report and records the decision only. It never hides
  -- or mutates the reported note/comment.
  insert into public.moderation_decisions(target_type, target_id, moderator_id, decision, reason)
  values ('report', v_report.id, auth.uid(), v_decision, v_resolution_note);

  return query
  select v_report.id, v_report.target_type, v_report.target_id, v_report.reason,
         v_report.status, v_report.resolution_note, v_report.created_at;
end;
$$;

revoke all on function public.list_pending_travel_notes_for_moderation(timestamptz, uuid, integer) from public, anon, authenticated;
grant execute on function public.list_pending_travel_notes_for_moderation(timestamptz, uuid, integer) to authenticated, service_role;
revoke all on function public.list_pending_travel_note_comments_for_moderation(timestamptz, uuid, integer) from public, anon, authenticated;
grant execute on function public.list_pending_travel_note_comments_for_moderation(timestamptz, uuid, integer) to authenticated, service_role;
revoke all on function public.list_pending_travel_note_reports_for_moderation(timestamptz, uuid, integer) from public, anon, authenticated;
grant execute on function public.list_pending_travel_note_reports_for_moderation(timestamptz, uuid, integer) to authenticated, service_role;
revoke all on function public.get_travel_note_moderation_item(uuid) from public, anon, authenticated;
grant execute on function public.get_travel_note_moderation_item(uuid) to authenticated, service_role;
revoke all on function public.get_travel_note_comment_moderation_item(uuid) from public, anon, authenticated;
grant execute on function public.get_travel_note_comment_moderation_item(uuid) to authenticated, service_role;
revoke all on function public.resolve_travel_note_report(uuid, text, text) from public, anon, authenticated;
grant execute on function public.resolve_travel_note_report(uuid, text, text) to authenticated, service_role;


create or replace function public.hide_travel_note_moderation_target(
  p_target_type text,
  p_target_id uuid
)
returns table (
  target_type text,
  target_id uuid,
  hidden boolean
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_target_type text := lower(btrim(p_target_type));
begin
  if auth.uid() is null or not public.is_community_admin() then
    raise exception 'community administrator access is required' using errcode = 'P0001';
  end if;
  if v_target_type not in ('note', 'comment') then
    raise exception 'invalid hide target type' using errcode = 'P0001';
  end if;

  perform set_config('travel_notes.allow_moderation_write', 'on', true);

  if v_target_type = 'note' then
    update public.travel_notes
    set deleted_at = now(), updated_at = now()
    where id = p_target_id and deleted_at is null;
  else
    update public.travel_note_comments
    set deleted_at = now(), updated_at = now()
    where id = p_target_id and deleted_at is null;
  end if;
  if not found then
    raise exception 'moderation target not found' using errcode = 'P0002';
  end if;

  insert into public.moderation_decisions(target_type, target_id, moderator_id, decision, reason)
  values (v_target_type, p_target_id, auth.uid(), 'hide_content', null);

  return query select v_target_type, p_target_id, true;
end;
$$;

revoke all on function public.hide_travel_note_moderation_target(text, uuid)
  from public, anon, authenticated;
grant execute on function public.hide_travel_note_moderation_target(text, uuid)
  to authenticated, service_role;
