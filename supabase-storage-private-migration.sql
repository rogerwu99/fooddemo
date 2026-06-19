-- FeedNomi storage privacy migration
-- Run this in the Supabase SQL editor for the existing project.
-- New uploads should be stored as private object paths and displayed with signed URLs.

update storage.buckets
set public = false
where id = 'food-photos';

drop policy if exists "Users can read food photos" on storage.objects;
create policy "Users can read food photos"
  on storage.objects
  for select
  using (
    bucket_id = 'food-photos'
    and auth.uid()::text = (storage.foldername(name))[1]
  );

drop policy if exists "Users can update own food photos" on storage.objects;
create policy "Users can update own food photos"
  on storage.objects
  for update
  using (
    bucket_id = 'food-photos'
    and auth.uid()::text = (storage.foldername(name))[1]
  )
  with check (
    bucket_id = 'food-photos'
    and auth.uid()::text = (storage.foldername(name))[1]
  );

drop policy if exists "Users can delete own food photos" on storage.objects;
create policy "Users can delete own food photos"
  on storage.objects
  for delete
  using (
    bucket_id = 'food-photos'
    and auth.uid()::text = (storage.foldername(name))[1]
  );
