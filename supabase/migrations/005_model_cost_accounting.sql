-- Preserve actual paid-model call counts and a reproducible configured-rate
-- estimate. Rates live in application configuration because provider prices can
-- change; the database stores only the resulting micro-CNY amount.
create table if not exists public.ai_model_cost_counters (
  subject_key text not null,
  usage_date date not null,
  model_calls bigint not null default 0 check (model_calls >= 0),
  estimated_cost_micros bigint not null default 0
    check (estimated_cost_micros >= 0),
  primary key (subject_key, usage_date)
);

alter table public.ai_model_cost_counters enable row level security;

drop function if exists public.get_ai_usage(text, date);
create function public.get_ai_usage(p_subject_key text, p_usage_date date)
returns table(
  request_count integer,
  pending integer,
  input_tokens bigint,
  output_tokens bigint,
  model_calls bigint,
  estimated_cost_micros bigint
) language sql security definer set search_path = public as $$
  select c.request_count, c.pending, c.input_tokens, c.output_tokens,
    coalesce(m.model_calls, 0), coalesce(m.estimated_cost_micros, 0)
  from ai_usage_counters c
  left join ai_model_cost_counters m
    on m.subject_key=c.subject_key and m.usage_date=c.usage_date
  where c.subject_key=p_subject_key and c.usage_date=p_usage_date;
$$;

drop function if exists public.get_ai_global_usage(date);
create function public.get_ai_global_usage(p_usage_date date)
returns table(
  request_count integer,
  pending integer,
  input_tokens bigint,
  output_tokens bigint,
  model_calls bigint,
  estimated_cost_micros bigint
) language sql security definer set search_path = public as $$
  select coalesce(sum(c.request_count),0)::integer,
    coalesce(sum(c.pending),0)::integer,
    coalesce(sum(c.input_tokens),0),
    coalesce(sum(c.output_tokens),0),
    (select coalesce(sum(m.model_calls),0)
      from ai_model_cost_counters m where m.usage_date=p_usage_date),
    (select coalesce(sum(m.estimated_cost_micros),0)
      from ai_model_cost_counters m where m.usage_date=p_usage_date)
  from ai_usage_counters c where c.usage_date=p_usage_date;
$$;

create function public.commit_ai_usage(
  p_reservation_id uuid,
  p_subject_key text,
  p_usage_date date,
  p_input_tokens integer,
  p_output_tokens integer,
  p_model_calls integer,
  p_estimated_cost_micros bigint
) returns void language plpgsql security definer set search_path = public as $$
begin
  update ai_usage_reservations set status='committed'
  where id=p_reservation_id and subject_key=p_subject_key
    and usage_date=p_usage_date and status='reserved' and expires_at > now();
  if found then
    update ai_usage_counters set
      request_count=request_count+1,
      input_tokens=input_tokens+greatest(p_input_tokens,0),
      output_tokens=output_tokens+greatest(p_output_tokens,0),
      pending=greatest(pending-1,0)
    where subject_key=p_subject_key and usage_date=p_usage_date;
    insert into ai_model_cost_counters(
      subject_key, usage_date, model_calls, estimated_cost_micros
    ) values (
      p_subject_key,
      p_usage_date,
      greatest(p_model_calls,0),
      greatest(p_estimated_cost_micros,0)
    ) on conflict (subject_key, usage_date) do update set
      model_calls=ai_model_cost_counters.model_calls+excluded.model_calls,
      estimated_cost_micros=(
        ai_model_cost_counters.estimated_cost_micros
        + excluded.estimated_cost_micros
      );
  end if;
end $$;

revoke all on function public.get_ai_usage(text, date) from public;
revoke all on function public.get_ai_global_usage(date) from public;
revoke all on function public.commit_ai_usage(uuid, text, date, integer, integer, integer, bigint) from public;
grant execute on function public.get_ai_usage(text, date) to service_role;
grant execute on function public.get_ai_global_usage(date) to service_role;
grant execute on function public.commit_ai_usage(uuid, text, date, integer, integer, integer, bigint) to service_role;
