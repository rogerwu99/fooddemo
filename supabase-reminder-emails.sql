-- FeedNomi reminder email setup
-- Run this in the Supabase SQL editor after the main schema.

create table if not exists public.notification_settings (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  reminders_enabled boolean not null default false,
  timezone text not null default 'America/New_York',
  last_reminder_sent_at timestamptz,
  reminder_streak integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.notification_settings enable row level security;

drop policy if exists "Users can read own notification settings" on public.notification_settings;
create policy "Users can read own notification settings"
  on public.notification_settings
  for select
  using (auth.uid() = user_id);

drop policy if exists "Users can insert own notification settings" on public.notification_settings;
create policy "Users can insert own notification settings"
  on public.notification_settings
  for insert
  with check (auth.uid() = user_id);

drop policy if exists "Users can update own notification settings" on public.notification_settings;
create policy "Users can update own notification settings"
  on public.notification_settings
  for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create index if not exists notification_settings_enabled_idx
  on public.notification_settings (reminders_enabled, last_reminder_sent_at);

-- Optional scheduled job.
-- 1. Deploy supabase/functions/send-nomi-reminders first.
-- 2. Add Supabase function secrets: RESEND_API_KEY, EMAIL_FROM, APP_URL, CRON_SECRET.
-- 3. Replace YOUR_PROJECT_REF and YOUR_CRON_SECRET below.
-- 4. Run the pg_cron/pg_net extensions if they are not already enabled.

create extension if not exists pg_cron with schema extensions;
create extension if not exists pg_net with schema extensions;

-- Uncomment after secrets and the Edge Function are deployed.
-- select cron.schedule(
--   'send-nomi-reminders-hourly',
--   '15 * * * *',
--   $$
--   select net.http_post(
--     url := 'https://YOUR_PROJECT_REF.functions.supabase.co/send-nomi-reminders',
--     headers := jsonb_build_object(
--       'Content-Type', 'application/json',
--       'x-cron-secret', 'YOUR_CRON_SECRET'
--     ),
--     body := '{}'::jsonb
--   );
--   $$
-- );
