-- Logins are created by an admin (edge function admin-create-user) with name + role in user metadata.
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into people (name, email, role, auth_user_id)
  values (
    coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email,'@',1)),
    new.email,
    coalesce(new.raw_user_meta_data->>'role', 'trainer'),
    new.id)
  on conflict (email) do update
    set auth_user_id = excluded.auth_user_id, role = excluded.role, name = excluded.name;
  return new;
end $$;

-- Only admins may delete people; everyone signed in may still read/write the rest.
drop policy "staff all" on people;
create policy "staff read write" on people for select to authenticated using (true);
create policy "staff insert" on people for insert to authenticated with check (true);
create policy "staff update" on people for update to authenticated using (true) with check (true);
create policy "admin delete" on people for delete to authenticated
  using (exists (select 1 from people me where me.auth_user_id = auth.uid() and me.role = 'admin'));
