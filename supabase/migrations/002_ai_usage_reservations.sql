-- Service-role only accounting table.  Subject keys are server-derived:
-- verified UUIDs or one-way hashes of signed anonymous sessions, never body data.
create table if not exists public.ai_usage_counters (
  subject_key text not null,
  usage_date date not null,
  request_count integer not null default 0 check (request_count >= 0),
  pending integer not null default 0 check (pending >= 0),
  input_tokens bigint not null default 0 check (input_tokens >= 0),
  output_tokens bigint not null default 0 check (output_tokens >= 0),
  primary key (subject_key, usage_date)
);

alter table public.ai_usage_counters enable row level security;

-- Called using the service role.  Advisory transaction lock serializes the
-- daily global counter and prevents concurrent requests from overselling it.
create or replace function public.reserve_ai_usage(
  p_subject_key text, p_usage_date date, p_user_limit integer, p_global_limit integer
) returns text language plpgsql security definer set search_path = public as $$
declare user_total integer; global_total integer;
begin
  perform pg_advisory_xact_lock(hashtext('ai_usage:' || p_usage_date::text));
  insert into ai_usage_counters(subject_key, usage_date) values (p_subject_key, p_usage_date)
    on conflict (subject_key, usage_date) do nothing;
  select request_count + pending into user_total from ai_usage_counters where subject_key=p_subject_key and usage_date=p_usage_date;
  select coalesce(sum(request_count + pending), 0) into global_total from ai_usage_counters where usage_date=p_usage_date;
  if user_total >= p_user_limit then return 'user_limit'; end if;
  if global_total >= p_global_limit then return 'global_limit'; end if;
  update ai_usage_counters set pending=pending+1 where subject_key=p_subject_key and usage_date=p_usage_date;
  return 'reserved';
end $$;

create or replace function public.commit_ai_usage(p_subject_key text, p_usage_date date, p_input_tokens integer, p_output_tokens integer)
returns void language sql security definer set search_path = public as $$
  update ai_usage_counters set pending=pending-1, request_count=request_count+1,
    input_tokens=input_tokens+greatest(p_input_tokens, 0), output_tokens=output_tokens+greatest(p_output_tokens, 0)
  where subject_key=p_subject_key and usage_date=p_usage_date and pending > 0;
$$;
create or replace function public.rollback_ai_usage(p_subject_key text, p_usage_date date)
returns void language sql security definer set search_path = public as $$
  update ai_usage_counters set pending=pending-1 where subject_key=p_subject_key and usage_date=p_usage_date and pending > 0;
$$;
create or replace function public.get_ai_usage(p_subject_key text, p_usage_date date)
returns table(request_count integer, pending integer, input_tokens bigint, output_tokens bigint) language sql security definer set search_path = public as $$
  select c.request_count, c.pending, c.input_tokens, c.output_tokens from ai_usage_counters c where c.subject_key=p_subject_key and c.usage_date=p_usage_date;
$$;
create or replace function public.get_ai_global_usage(p_usage_date date)
returns table(request_count integer, pending integer, input_tokens bigint, output_tokens bigint) language sql security definer set search_path = public as $$
  select coalesce(sum(c.request_count),0)::integer, coalesce(sum(c.pending),0)::integer, coalesce(sum(c.input_tokens),0), coalesce(sum(c.output_tokens),0) from ai_usage_counters c where c.usage_date=p_usage_date;
$$;

revoke all on function public.reserve_ai_usage(text, date, integer, integer) from public;
revoke all on function public.commit_ai_usage(text, date, integer, integer) from public;
revoke all on function public.rollback_ai_usage(text, date) from public;
revoke all on function public.get_ai_usage(text, date) from public;
revoke all on function public.get_ai_global_usage(date) from public;
grant execute on function public.reserve_ai_usage(text, date, integer, integer) to service_role;
grant execute on function public.commit_ai_usage(text, date, integer, integer) to service_role;
grant execute on function public.rollback_ai_usage(text, date) to service_role;
grant execute on function public.get_ai_usage(text, date) to service_role;
grant execute on function public.get_ai_global_usage(date) to service_role;
