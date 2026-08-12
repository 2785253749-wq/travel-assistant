create table public.weather_daily_usage (
  usage_date date primary key,
  used integer not null default 0 check (used >= 0)
);

alter table public.weather_daily_usage enable row level security;

create function public.reserve_weather_quota(
  p_usage_date date,
  p_daily_limit integer
)
returns boolean
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  reserved boolean;
begin
  if p_usage_date is null or p_daily_limit <= 0 then
    return false;
  end if;

  insert into public.weather_daily_usage (usage_date, used)
  select p_usage_date, 1 where 1 <= p_daily_limit
  on conflict (usage_date) do update
  set used = weather_daily_usage.used + 1
  where weather_daily_usage.used + 1 <= p_daily_limit
  returning true into reserved;

  return coalesce(reserved, false);
end;
$$;

revoke all on table public.weather_daily_usage from public, anon, authenticated;
revoke all on function public.reserve_weather_quota(date, integer) from public, anon, authenticated;
grant execute on function public.reserve_weather_quota(date, integer) to service_role;
