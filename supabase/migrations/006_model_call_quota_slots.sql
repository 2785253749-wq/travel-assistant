-- Daily AI limits count paid-model invocations, not HTTP requests. A planning
-- request can invoke once and then invoke once more to repair invalid output,
-- so admission reserves two slots and settlement releases any unused slot.
alter table public.ai_usage_reservations
  add column if not exists reserved_model_calls integer not null default 1
  check (reserved_model_calls between 1 and 2);

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
    select sum(r.reserved_model_calls)
    from ai_usage_reservations r
    where r.subject_key = c.subject_key
      and r.usage_date = c.usage_date
      and r.status = 'reserved'
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

-- Remove every cost-blind overload published by earlier migrations. Keeping
-- one of these callable would let an older application bypass model-call
-- quota and cost accounting entirely.
drop function if exists public.commit_ai_usage(uuid, text, date, integer, integer);
drop function if exists public.commit_ai_usage(text, date, integer, integer);
drop function if exists public.rollback_ai_usage(text, date);

-- PostgreSQL cannot change a function's return type with CREATE OR REPLACE.
drop function if exists public.commit_ai_usage(
  uuid, text, date, integer, integer, integer, bigint
);

create function public.commit_ai_usage(
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
  reservation_status text;
begin
  perform pg_advisory_xact_lock(hashtext('ai_usage:' || p_usage_date::text));
  select reserved_model_calls, status
  into reservation_slots, reservation_status
  from ai_usage_reservations
  where id = p_reservation_id
    and subject_key = p_subject_key
    and usage_date = p_usage_date
    and status in ('reserved', 'expired')
  for update;
  if not found then
    raise exception 'reservation is missing or cannot be settled'
      using errcode = '22023';
  end if;
  if p_model_calls < 0 or p_model_calls > reservation_slots then
    raise exception 'actual model calls exceed reserved slots'
      using errcode = '22023';
  end if;

  update ai_usage_reservations set status = 'committed'
  where id = p_reservation_id;
  update ai_usage_counters set
    request_count = request_count + 1,
    input_tokens = input_tokens + greatest(p_input_tokens, 0),
    output_tokens = output_tokens + greatest(p_output_tokens, 0),
    pending = case
      when reservation_status = 'reserved'
        then greatest(pending - reservation_slots, 0)
      else pending
    end
  where subject_key = p_subject_key and usage_date = p_usage_date;
  if not found then
    raise exception 'usage counter is missing for reservation'
      using errcode = '22023';
  end if;
  insert into ai_model_cost_counters(
    subject_key, usage_date, model_calls, estimated_cost_micros
  ) values (
    p_subject_key,
    p_usage_date,
    greatest(p_model_calls, 0),
    greatest(p_estimated_cost_micros, 0)
  ) on conflict (subject_key, usage_date) do update set
    model_calls = ai_model_cost_counters.model_calls + excluded.model_calls,
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
begin
  perform pg_advisory_xact_lock(hashtext('ai_usage:' || p_usage_date::text));
  select reserved_model_calls
  into reservation_slots
  from ai_usage_reservations
  where id = p_reservation_id
    and subject_key = p_subject_key
    and usage_date = p_usage_date
    and status = 'reserved'
  for update;
  if not found then
    return;
  end if;

  update ai_usage_reservations set status = 'rolled_back'
  where id = p_reservation_id;
  update ai_usage_counters
  set pending = greatest(pending - reservation_slots, 0)
  where subject_key = p_subject_key and usage_date = p_usage_date;
end $$;

revoke all on function public.reserve_ai_usage(text, date, integer, integer) from public;
revoke all on function public.commit_ai_usage(uuid, text, date, integer, integer, integer, bigint) from public;
revoke all on function public.rollback_ai_usage(uuid, text, date) from public;
grant execute on function public.reserve_ai_usage(text, date, integer, integer) to service_role;
grant execute on function public.commit_ai_usage(uuid, text, date, integer, integer, integer, bigint) to service_role;
grant execute on function public.rollback_ai_usage(uuid, text, date) to service_role;
