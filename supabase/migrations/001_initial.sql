create extension if not exists pgcrypto;

create table public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  preferences jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.trips (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null check (char_length(title) between 1 and 100),
  status text not null check (status in ('collecting', 'planned')),
  profile jsonb not null default '{}'::jsonb,
  itinerary jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, user_id)
);

create table public.conversation_messages (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  trip_id uuid,
  role text not null check (role in ('user', 'assistant')),
  content text not null check (char_length(content) between 1 and 4000),
  created_at timestamptz not null default now(),
  foreign key (trip_id, user_id) references public.trips(id, user_id) on delete cascade
);

create table public.share_links (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  trip_id uuid not null,
  token_hash text not null unique,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  foreign key (trip_id, user_id) references public.trips(id, user_id) on delete cascade
);

create table public.ai_usage (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  usage_date date not null default current_date,
  request_count integer not null default 0 check (request_count >= 0),
  input_tokens integer not null default 0 check (input_tokens >= 0),
  output_tokens integer not null default 0 check (output_tokens >= 0),
  status text not null default 'allowed' check (status in ('allowed', 'blocked')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, usage_date)
);

alter table public.profiles enable row level security;
alter table public.trips enable row level security;
alter table public.conversation_messages enable row level security;
alter table public.share_links enable row level security;
alter table public.ai_usage enable row level security;

create policy "users manage own profiles" on public.profiles
for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "users manage own trips" on public.trips
for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "users manage own conversation messages" on public.conversation_messages
for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "users manage own share links" on public.share_links
for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "users manage own ai usage" on public.ai_usage
for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
