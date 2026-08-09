-- No anonymous table access: the only public path is this narrow projection.
revoke all on table public.trips from public, anon;
revoke all on table public.share_links from public, anon;
revoke all on table public.conversation_messages from public, anon;
revoke all on table public.profiles from public, anon;
revoke all on table public.ai_usage from public, anon;
grant select, insert, update, delete on table public.profiles, public.trips, public.share_links, public.conversation_messages, public.ai_usage to authenticated;

create or replace function public.get_shared_trip_by_token_hash(p_token_hash text)
returns table (
  id uuid,
  title text,
  status text,
  profile jsonb,
  itinerary jsonb,
  updated_at timestamptz
)
language sql
security definer
set search_path = pg_catalog, public
as $$
  select t.id, t.title, t.status, t.profile, t.itinerary, t.updated_at
  from public.share_links as s
  join public.trips as t on t.id = s.trip_id and t.user_id = s.user_id
  where s.token_hash = p_token_hash
    and s.revoked_at is null
    and s.expires_at > now()
  limit 1;
$$;

revoke all on function public.get_shared_trip_by_token_hash(text) from public;
grant execute on function public.get_shared_trip_by_token_hash(text) to anon, authenticated;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger trips_set_updated_at
before update on public.trips
for each row execute function public.set_updated_at();
