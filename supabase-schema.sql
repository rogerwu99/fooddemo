-- PlatePoints Supabase setup
-- Run this in the Supabase SQL editor.

create table if not exists public.food_logs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  serving text,
  calories numeric,
  protein text,
  fiber text,
  sugar text,
  points integer not null default 0,
  confidence numeric,
  photo_url text,
  nutrition jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

alter table public.food_logs enable row level security;

drop policy if exists "Users can read own food logs" on public.food_logs;
create policy "Users can read own food logs"
  on public.food_logs
  for select
  using (auth.uid() = user_id);

drop policy if exists "Users can insert own food logs" on public.food_logs;
create policy "Users can insert own food logs"
  on public.food_logs
  for insert
  with check (auth.uid() = user_id);

drop policy if exists "Users can update own food logs" on public.food_logs;
create policy "Users can update own food logs"
  on public.food_logs
  for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "Users can delete own food logs" on public.food_logs;
create policy "Users can delete own food logs"
  on public.food_logs
  for delete
  using (auth.uid() = user_id);

create index if not exists food_logs_user_created_idx
  on public.food_logs (user_id, created_at desc);

insert into storage.buckets (id, name, public)
values ('food-photos', 'food-photos', true)
on conflict (id) do nothing;

drop policy if exists "Users can upload own food photos" on storage.objects;
create policy "Users can upload own food photos"
  on storage.objects
  for insert
  with check (
    bucket_id = 'food-photos'
    and auth.uid()::text = (storage.foldername(name))[1]
  );

drop policy if exists "Users can read food photos" on storage.objects;
create policy "Users can read food photos"
  on storage.objects
  for select
  using (bucket_id = 'food-photos');
