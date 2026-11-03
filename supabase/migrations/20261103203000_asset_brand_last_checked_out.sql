alter table assets add column if not exists brand text, add column if not exists last_checked_out_at timestamptz;

-- backfill from history
update assets a set last_checked_out_at = m.at
  from (select asset_id, max(created_at) as at from movements where action = 'check_out' group by asset_id) m
 where m.asset_id = a.id;

-- record_movement now stamps last_checked_out_at on check_out (full function body in 20261103180100, one line added)
create or replace function public.record_movement(
  p_asset_id uuid,
  p_action text,
  p_person_id uuid default null,
  p_location_id uuid default null,
  p_condition text default null,
  p_note text default null,
  p_due_at timestamptz default null,
  p_missing_ids uuid[] default '{}'
) returns void
language plpgsql security invoker set search_path = public as $$
declare
  ids uuid[];
begin
  select array_agg(id) into ids from assets
   where (id = p_asset_id or kit_id = p_asset_id) and not (id = any(p_missing_ids));

  if p_action = 'check_out' then
    update assets set status='checked_out', holder_id=p_person_id, location_id=p_location_id, last_checked_out_at=now(),
      due_at=coalesce(p_due_at, date_trunc('day', now()) + interval '1 day' - interval '1 second')
     where id = any(ids);
  elsif p_action = 'check_in' then
    update assets set status = case when coalesce(p_condition,condition)='damaged' then 'needs_repair' else 'available' end,
      holder_id=null, due_at=null,
      location_id=coalesce(p_location_id, home_location_id, location_id),
      condition=coalesce(p_condition, condition)
     where id = any(ids);
  elsif p_action = 'damage' then
    update assets set status='needs_repair', condition='damaged' where id = any(ids);
  elsif p_action = 'move' then
    update assets set location_id=p_location_id where id = any(ids);
  elsif p_action = 'audit' then
    update assets set location_id=coalesce(p_location_id, location_id) where id = any(ids);
  else
    raise exception 'unknown action %', p_action;
  end if;

  insert into movements (asset_id, action, person_id, location_id, condition, note, due_at, created_by)
  select id, p_action, p_person_id, p_location_id, p_condition, p_note, p_due_at, auth.uid() from unnest(ids) as id;

  update assets set status='lost' where id = any(p_missing_ids);
  insert into movements (asset_id, action, note, created_by)
  select id, 'audit', 'missing at kit check-out', auth.uid() from unnest(p_missing_ids) as id;
end $$;
