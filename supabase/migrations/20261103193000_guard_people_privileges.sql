-- Roles and login links are authorization data: only admins (or the service role) may set them.
create or replace function public.guard_people_privileges() returns trigger
language plpgsql security definer set search_path = public as $$
declare is_admin boolean;
begin
  if auth.uid() is null then return new; end if; -- service role / auth trigger
  select exists (select 1 from people where auth_user_id = auth.uid() and role = 'admin') into is_admin;
  if is_admin then return new; end if;
  if tg_op = 'INSERT' then
    if new.role <> 'student' or new.auth_user_id is not null then
      raise exception 'Only admins can add trainers, admins or logins';
    end if;
  elsif new.role is distinct from old.role or new.auth_user_id is distinct from old.auth_user_id then
    raise exception 'Only admins can change roles or logins';
  end if;
  return new;
end $$;
drop trigger if exists people_guard_privileges on people;
create trigger people_guard_privileges before insert or update on people
  for each row execute function public.guard_people_privileges();

-- Self-service sign-up (anon key + /auth/v1/signup) carries no role metadata: refuse it.
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.raw_user_meta_data->>'role' is null then
    raise exception 'Accounts are created by a lab admin';
  end if;
  insert into people (name, email, role, auth_user_id)
  values (
    coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email,'@',1)),
    new.email,
    new.raw_user_meta_data->>'role',
    new.id)
  on conflict (email) do update
    set auth_user_id = excluded.auth_user_id, role = excluded.role, name = excluded.name;
  return new;
end $$;
