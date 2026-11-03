alter table assets add column if not exists photo_url text;

-- Public bucket for tool photos; any signed-in user can upload, anyone can view.
insert into storage.buckets (id, name, public) values ('asset-photos', 'asset-photos', true)
  on conflict (id) do nothing;
create policy "staff upload photos" on storage.objects for insert to authenticated with check (bucket_id = 'asset-photos');
create policy "staff replace photos" on storage.objects for update to authenticated using (bucket_id = 'asset-photos');
create policy "public read photos" on storage.objects for select using (bucket_id = 'asset-photos');
