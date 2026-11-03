create extension if not exists pgcrypto;

-- People: trainers/admins (with logins) and students (borrowers, usually no login)
create table people (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  role text not null default 'student' check (role in ('admin','trainer','student')),
  email text unique,
  badge_code text unique,
  auth_user_id uuid unique references auth.users(id) on delete set null,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

-- Hierarchical storage: Toolbox -> Drawer, Facility -> Locker -> Cart
create table locations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  code text unique not null,
  parent_id uuid references locations(id) on delete set null,
  created_at timestamptz not null default now()
);

create table assets (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  kind text not null default 'individual' check (kind in ('individual','hand_tool','kit')),
  barcode text unique,
  serial text,
  quantity integer not null default 1 check (quantity >= 1),
  kit_id uuid references assets(id) on delete set null,
  home_location_id uuid references locations(id) on delete set null,
  location_id uuid references locations(id) on delete set null,
  holder_id uuid references people(id) on delete set null,
  status text not null default 'available' check (status in ('available','checked_out','needs_repair','lost')),
  condition text not null default 'good' check (condition in ('good','fair','damaged')),
  due_at timestamptz,
  notes text,
  created_at timestamptz not null default now()
);
create index assets_kit_idx on assets(kit_id);
create index assets_holder_idx on assets(holder_id);

create table movements (
  id uuid primary key default gen_random_uuid(),
  asset_id uuid not null references assets(id) on delete cascade,
  action text not null check (action in ('check_out','check_in','audit','damage','move')),
  person_id uuid references people(id) on delete set null,
  location_id uuid references locations(id) on delete set null,
  condition text check (condition in ('good','fair','damaged')),
  note text,
  due_at timestamptz,
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now()
);
create index movements_asset_idx on movements(asset_id, created_at desc);

-- Single-tenant lab: any signed-in staff member can read/write; anon gets nothing.
alter table people enable row level security;
alter table locations enable row level security;
alter table assets enable row level security;
alter table movements enable row level security;
create policy "staff all" on people for all to authenticated using (true) with check (true);
create policy "staff all" on locations for all to authenticated using (true) with check (true);
create policy "staff all" on assets for all to authenticated using (true) with check (true);
create policy "staff all" on movements for all to authenticated using (true) with check (true);

-- Auto-create a people row for each login so trainers appear in pick lists.
create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into people (name, email, role, auth_user_id)
  values (coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email,'@',1)), new.email, 'trainer', new.id)
  on conflict (email) do update set auth_user_id = excluded.auth_user_id;
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();
