create table public.user_footprints (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  city_adcode text not null check (city_adcode ~ '^[0-9]{6}$'),
  city_name text not null check (char_length(btrim(city_name)) between 1 and 40),
  province_adcode text not null check (province_adcode ~ '^[0-9]{6}$'),
  province_name text not null check (char_length(btrim(province_name)) between 1 and 40),
  center_lng double precision not null check (center_lng between 73 and 136),
  center_lat double precision not null check (center_lat between 3 and 54),
  visited_at date not null check (visited_at <= current_date),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, city_adcode),
  unique (id, user_id)
);

create index user_footprints_owner_visited_at_idx
on public.user_footprints (user_id, visited_at desc, created_at desc, id desc);

alter table public.user_footprints enable row level security;

revoke all on table public.user_footprints from public, anon, authenticated;
grant select, insert, update, delete on table public.user_footprints to authenticated;

create policy "users view own footprints" on public.user_footprints
for select to authenticated
using (auth.uid() = user_id);

create policy "users create own footprints" on public.user_footprints
for insert to authenticated
with check (auth.uid() = user_id);

create policy "users update own footprints" on public.user_footprints
for update to authenticated
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

create policy "users delete own footprints" on public.user_footprints
for delete to authenticated
using (auth.uid() = user_id);

create trigger user_footprints_set_updated_at
before update on public.user_footprints
for each row execute function public.set_updated_at();
