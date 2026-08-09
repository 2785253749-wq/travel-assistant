-- PostgreSQL cannot change a function's return type with CREATE OR REPLACE.
-- Drop the already-published text function by its exact identity before
-- recreating the same RPC parameters with the structured JSONB response.
drop function if exists public.reserve_ai_usage(text, date, integer, integer);

create function public.reserve_ai_usage(
  p_subject_key text, p_usage_date date, p_user_limit integer, p_global_limit integer
) returns jsonb language plpgsql security definer set search_path = public as $$
declare user_total integer; global_total integer;
declare new_reservation uuid;
begin
  perform pg_advisory_xact_lock(hashtext('ai_usage:' || p_usage_date::text));
  update ai_usage_reservations set status='expired'
    where usage_date=p_usage_date and status='reserved' and expires_at <= now();
  insert into ai_usage_counters(subject_key, usage_date) values (p_subject_key, p_usage_date)
    on conflict (subject_key, usage_date) do nothing;
  select request_count + (select count(*) from ai_usage_reservations r where r.subject_key=p_subject_key and r.usage_date=p_usage_date and r.status='reserved') into user_total from ai_usage_counters where subject_key=p_subject_key and usage_date=p_usage_date;
  select coalesce(sum(c.request_count), 0) + (select count(*) from ai_usage_reservations r where r.usage_date=p_usage_date and r.status='reserved') into global_total from ai_usage_counters c where c.usage_date=p_usage_date;
  if user_total >= p_user_limit then
    return jsonb_build_object('allowed', false, 'reservation_id', null, 'reason', 'user_limit');
  end if;
  if global_total >= p_global_limit then
    return jsonb_build_object('allowed', false, 'reservation_id', null, 'reason', 'global_limit');
  end if;
  insert into ai_usage_reservations(subject_key, usage_date) values (p_subject_key, p_usage_date) returning id into new_reservation;
  update ai_usage_counters set pending=(select count(*) from ai_usage_reservations r where r.subject_key=p_subject_key and r.usage_date=p_usage_date and r.status='reserved') where subject_key=p_subject_key and usage_date=p_usage_date;
  return jsonb_build_object('allowed', true, 'reservation_id', new_reservation::text, 'reason', null);
end $$;

revoke all on function public.reserve_ai_usage(text, date, integer, integer) from public;
grant execute on function public.reserve_ai_usage(text, date, integer, integer) to service_role;
