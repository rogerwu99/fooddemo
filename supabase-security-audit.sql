-- FeedNomi Supabase launch audit
-- Run this in the Supabase SQL editor before launch.
-- It does not change data; it shows whether food log rows and photo storage are private.

select
  schemaname,
  tablename,
  rowsecurity as rls_enabled
from pg_tables
where schemaname = 'public'
  and tablename = 'food_logs';

select
  schemaname,
  tablename,
  policyname,
  cmd,
  qual as using_expression,
  with_check as check_expression
from pg_policies
where schemaname = 'public'
  and tablename = 'food_logs'
order by policyname;

select
  id,
  name,
  public as bucket_is_public
from storage.buckets
where id = 'food-photos';

select
  policyname,
  cmd,
  qual as using_expression,
  with_check as check_expression
from pg_policies
where schemaname = 'storage'
  and tablename = 'objects'
  and (
    qual ilike '%food-photos%'
    or with_check ilike '%food-photos%'
    or policyname ilike '%food photo%'
    or policyname ilike '%food-photos%'
  )
order by policyname;

select
  user_id,
  count(*) as saved_meals,
  count(*) filter (where photo_url is not null and photo_url <> '') as meals_with_photo_paths,
  count(*) filter (where photo_url like 'http%') as meals_with_public_photo_urls,
  max(created_at) as newest_meal
from public.food_logs
group by user_id
order by newest_meal desc;
