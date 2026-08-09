-- A request may reserve capacity immediately before UTC midnight and invoke
-- the paid model after midnight. Charge each attempt atomically on its actual
-- invoke day before the provider call; final settlement must never add calls a
-- second time.
alter table public.ai_usage_reservations
  add column if not exists incurred_model_calls integer not null default 0
  check (incurred_model_calls between 0 and 2);

create or replace function public.reserve_ai_usage(
  p_subject_key text,
  p_usage_date date,
  p_user_limit integer,
  p_global_limit integer
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  user_total bigint;
  global_total bigint;
  new_reservation uuid;
  reservation_slots constant integer := 2;
begin
  perform pg_advisory_xact_lock(hashtext('ai_usage:' || p_usage_date::text));

  update ai_usage_reservations
  set status = 'expired'
  where usage_date = p_usage_date
    and status = 'reserved'
    and expires_at <= now();

  update ai_usage_counters c
  set pending = coalesce((
    select sum(r.reserved_model_calls - r.incurred_model_calls)
    from ai_usage_reservations r
    where r.subject_key = c.subject_key
      and r.usage_date = c.usage_date
      and r.status in ('reserved', 'expired')
  ), 0)
  where c.usage_date = p_usage_date;

  insert into ai_usage_counters(subject_key, usage_date)
  values (p_subject_key, p_usage_date)
  on conflict (subject_key, usage_date) do nothing;
  insert into ai_model_cost_counters(subject_key, usage_date)
  values (p_subject_key, p_usage_date)
  on conflict (subject_key, usage_date) do nothing;

  select m.model_calls + c.pending
  into user_total
  from ai_usage_counters c
  join ai_model_cost_counters m
    on m.subject_key = c.subject_key and m.usage_date = c.usage_date
  where c.subject_key = p_subject_key and c.usage_date = p_usage_date;

  select
    coalesce((select sum(model_calls) from ai_model_cost_counters
      where usage_date = p_usage_date), 0)
    + coalesce((select sum(pending) from ai_usage_counters
      where usage_date = p_usage_date), 0)
  into global_total;

  if user_total + reservation_slots > p_user_limit then
    return jsonb_build_object(
      'allowed', false,
      'reservation_id', null,
      'reason', 'user_limit'
    );
  end if;
  if global_total + reservation_slots > p_global_limit then
    return jsonb_build_object(
      'allowed', false,
      'reservation_id', null,
      'reason', 'global_limit'
    );
  end if;

  insert into ai_usage_reservations(
    subject_key, usage_date, reserved_model_calls
  ) values (
    p_subject_key, p_usage_date, reservation_slots
  ) returning id into new_reservation;
  update ai_usage_counters
  set pending = pending + reservation_slots
  where subject_key = p_subject_key and usage_date = p_usage_date;

  return jsonb_build_object(
    'allowed', true,
    'reservation_id', new_reservation::text,
    'reason', null
  );
end $$;

create function public.admit_ai_usage_call(
  p_reservation_id uuid,
  p_subject_key text,
  p_reservation_date date,
  p_call_usage_date date,
  p_user_limit integer,
  p_global_limit integer
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  reservation_slots integer;
  incurred_model_calls integer;
  reservation_status text;
  reservation_expires_at timestamptz;
  user_total bigint;
  global_total bigint;
begin
  -- Reserve and per-attempt admission take the same day locks. Taking both
  -- dates in a stable order prevents cross-midnight transfers from deadlocking.
  perform pg_advisory_xact_lock(hashtext(
    'ai_usage:' || least(p_reservation_date, p_call_usage_date)::text
  ));
  if p_reservation_date <> p_call_usage_date then
    perform pg_advisory_xact_lock(hashtext(
      'ai_usage:' || greatest(p_reservation_date, p_call_usage_date)::text
    ));
  end if;

  select
    r.reserved_model_calls,
    r.incurred_model_calls,
    r.status,
    r.expires_at
  into
    reservation_slots,
    incurred_model_calls,
    reservation_status,
    reservation_expires_at
  from ai_usage_reservations r
  where r.id = p_reservation_id
    and r.subject_key = p_subject_key
    and r.usage_date = p_reservation_date
  for update;
  if not found then
    raise exception 'reservation is missing or cannot admit a call'
      using errcode = '22023';
  end if;
  if reservation_status <> 'reserved' or reservation_expires_at <= now() then
    if reservation_status = 'reserved' and reservation_expires_at <= now() then
      update ai_usage_reservations
      set status = 'expired'
      where id = p_reservation_id;
    end if;
    return jsonb_build_object('allowed', false, 'reason', 'reservation_expired');
  end if;
  if incurred_model_calls >= reservation_slots then
    raise exception 'actual model calls exceed reserved slots'
      using errcode = '22023';
  end if;

  insert into ai_usage_counters(subject_key, usage_date)
  values (p_subject_key, p_call_usage_date)
  on conflict (subject_key, usage_date) do nothing;
  insert into ai_model_cost_counters(subject_key, usage_date)
  values (p_subject_key, p_call_usage_date)
  on conflict (subject_key, usage_date) do nothing;

  if p_call_usage_date <> p_reservation_date then
    select m.model_calls + c.pending
    into user_total
    from ai_usage_counters c
    join ai_model_cost_counters m
      on m.subject_key = c.subject_key and m.usage_date = c.usage_date
    where c.subject_key = p_subject_key
      and c.usage_date = p_call_usage_date;

    select
      coalesce((select sum(model_calls) from ai_model_cost_counters
        where usage_date = p_call_usage_date), 0)
      + coalesce((select sum(pending) from ai_usage_counters
        where usage_date = p_call_usage_date), 0)
    into global_total;

    if user_total + 1 > p_user_limit then
      return jsonb_build_object('allowed', false, 'reason', 'user_limit');
    end if;
    if global_total + 1 > p_global_limit then
      return jsonb_build_object('allowed', false, 'reason', 'global_limit');
    end if;
  end if;

  update ai_usage_counters
  set pending = pending - 1
  where subject_key = p_subject_key
    and usage_date = p_reservation_date
    and pending > 0;
  if not found then
    raise exception 'usage reservation slot is missing'
      using errcode = '22023';
  end if;

  insert into ai_model_cost_counters(
    subject_key, usage_date, model_calls, estimated_cost_micros
  ) values (
    p_subject_key, p_call_usage_date, 1, 0
  ) on conflict (subject_key, usage_date) do update set
    model_calls = ai_model_cost_counters.model_calls + 1;
  update ai_usage_reservations as r
  set incurred_model_calls = r.incurred_model_calls + 1
  where r.id = p_reservation_id;

  return jsonb_build_object('allowed', true, 'reason', null);
end $$;

create or replace function public.commit_ai_usage(
  p_reservation_id uuid,
  p_subject_key text,
  p_usage_date date,
  p_input_tokens integer,
  p_output_tokens integer,
  p_model_calls integer,
  p_estimated_cost_micros bigint
) returns boolean language plpgsql security definer set search_path = public as $$
declare
  reservation_slots integer;
  incurred_model_calls integer;
begin
  perform pg_advisory_xact_lock(hashtext('ai_usage:' || p_usage_date::text));
  select r.reserved_model_calls, r.incurred_model_calls
  into reservation_slots, incurred_model_calls
  from ai_usage_reservations r
  where r.id = p_reservation_id
    and r.subject_key = p_subject_key
    and r.usage_date = p_usage_date
    and r.status in ('reserved', 'expired')
  for update;
  if not found then
    raise exception 'reservation is missing or cannot be settled'
      using errcode = '22023';
  end if;
  if p_model_calls <> incurred_model_calls then
    raise exception 'reported model calls do not match admitted attempts'
      using errcode = '22023';
  end if;

  update ai_usage_counters set
    request_count = request_count + 1,
    input_tokens = input_tokens + greatest(p_input_tokens, 0),
    output_tokens = output_tokens + greatest(p_output_tokens, 0),
    pending = pending - (reservation_slots - incurred_model_calls)
  where subject_key = p_subject_key
    and usage_date = p_usage_date
    and pending >= (reservation_slots - incurred_model_calls);
  if not found then
    raise exception 'usage counter is missing for reservation'
      using errcode = '22023';
  end if;
  update ai_usage_reservations set status = 'committed'
  where id = p_reservation_id;
  insert into ai_model_cost_counters(
    subject_key, usage_date, model_calls, estimated_cost_micros
  ) values (
    p_subject_key,
    p_usage_date,
    0,
    greatest(p_estimated_cost_micros, 0)
  ) on conflict (subject_key, usage_date) do update set
    estimated_cost_micros = (
      ai_model_cost_counters.estimated_cost_micros
      + excluded.estimated_cost_micros
    );
  return true;
end $$;

create or replace function public.rollback_ai_usage(
  p_reservation_id uuid,
  p_subject_key text,
  p_usage_date date
) returns void language plpgsql security definer set search_path = public as $$
declare
  reservation_slots integer;
  incurred_model_calls integer;
begin
  perform pg_advisory_xact_lock(hashtext('ai_usage:' || p_usage_date::text));
  select r.reserved_model_calls, r.incurred_model_calls
  into reservation_slots, incurred_model_calls
  from ai_usage_reservations r
  where r.id = p_reservation_id
    and r.subject_key = p_subject_key
    and r.usage_date = p_usage_date
    and r.status = 'reserved'
  for update;
  if not found then
    return;
  end if;

  update ai_usage_counters
  set pending = pending - (reservation_slots - incurred_model_calls)
  where subject_key = p_subject_key
    and usage_date = p_usage_date
    and pending >= (reservation_slots - incurred_model_calls);
  if not found then
    raise exception 'usage counter is missing for reservation'
      using errcode = '22023';
  end if;
  update ai_usage_reservations set status = 'rolled_back'
  where id = p_reservation_id;
end $$;

revoke all on function public.reserve_ai_usage(text, date, integer, integer) from public;
revoke all on function public.admit_ai_usage_call(uuid, text, date, date, integer, integer) from public;
revoke all on function public.commit_ai_usage(uuid, text, date, integer, integer, integer, bigint) from public;
revoke all on function public.rollback_ai_usage(uuid, text, date) from public;
grant execute on function public.reserve_ai_usage(text, date, integer, integer) to service_role;
grant execute on function public.admit_ai_usage_call(uuid, text, date, date, integer, integer) to service_role;
grant execute on function public.commit_ai_usage(uuid, text, date, integer, integer, integer, bigint) to service_role;
grant execute on function public.rollback_ai_usage(uuid, text, date) to service_role;
