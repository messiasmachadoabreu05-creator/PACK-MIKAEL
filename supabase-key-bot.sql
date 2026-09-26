-- Integração do bot com o site existente (public.keys).
-- Faça backup do projeto Supabase antes de aplicar.
-- O bot usa service_role exclusivamente no servidor.

alter table public.keys add column if not exists duration_days integer;
alter table public.keys add column if not exists bot_status text not null default 'available';
alter table public.keys add column if not exists bot_reserved_by bigint;
alter table public.keys add column if not exists bot_ticket_channel bigint;
alter table public.keys add column if not exists bot_sent_at timestamptz;

update public.keys set duration_days = case plan
  when '7d' then 7 when '30d' then 30 when '365d' then 365
  when 'vitalicia' then null else duration_days end
where duration_days is null;

create index if not exists keys_bot_stock_idx on public.keys (created_at)
where redeemed_by is null and bot_status = 'available';

create or replace function public.bot_reserve_key(p_discord_user_id bigint, p_ticket_channel_id bigint)
returns table(id uuid, code text, plan text, duration_days integer)
language plpgsql security definer set search_path = public as $$
declare picked public.keys%rowtype;
begin
  select k.* into picked from public.keys k
   where k.redeemed_by is null and k.bot_status = 'available'
   order by k.created_at asc nulls first
   for update skip locked limit 1;
  if not found then return; end if;
  update public.keys set bot_status='reserved', bot_reserved_by=p_discord_user_id,
    bot_ticket_channel=p_ticket_channel_id where keys.id=picked.id;
  return query select picked.id, picked.code, picked.plan, picked.duration_days;
end $$;

create or replace function public.bot_release_key(p_ticket_channel_id bigint)
returns boolean language plpgsql security definer set search_path = public as $$
declare changed integer;
begin
  update public.keys set bot_status='available', bot_reserved_by=null, bot_ticket_channel=null
   where bot_ticket_channel=p_ticket_channel_id and bot_status='reserved';
  get diagnostics changed = row_count;
  return changed > 0;
end $$;

create or replace function public.bot_deliver_key(p_ticket_channel_id bigint)
returns table(id uuid, code text, plan text, duration_days integer, expires_at timestamptz, discord_user_id bigint)
language plpgsql security definer set search_path = public as $$
declare picked public.keys%rowtype; expiry timestamptz;
begin
  select k.* into picked from public.keys k where k.bot_ticket_channel=p_ticket_channel_id
    and k.bot_status='reserved' and k.redeemed_by is null for update;
  if not found then return; end if;
  if picked.duration_days is null and picked.plan not in ('vitalicia') then
    raise exception 'duration_days missing for this key';
  end if;
  expiry := case when picked.duration_days is null then null else now() + make_interval(days => picked.duration_days) end;
  update public.keys set bot_status='sent', bot_sent_at=now(), expires_at=expiry
    where keys.id=picked.id;
  return query select picked.id, picked.code, picked.plan, picked.duration_days, expiry, picked.bot_reserved_by;
end $$;

create or replace function public.bot_inspect_key(p_code text)
returns table(id uuid, code text, plan text, duration_days integer, redeemed_by uuid,
  expires_at timestamptz, bot_status text, bot_sent_at timestamptz)
language sql security definer set search_path = public as $$
  select k.id,k.code,k.plan,k.duration_days,k.redeemed_by,k.expires_at,k.bot_status,k.bot_sent_at
  from public.keys k where upper(k.code)=upper(trim(p_code)) limit 1
$$;

create or replace function public.bot_revoke_key(p_key_id uuid)
returns boolean language plpgsql security definer set search_path = public as $$
declare changed integer;
begin
  update public.keys set bot_status='revoked', expires_at=now()
   where id=p_key_id;
  get diagnostics changed = row_count;
  return changed > 0;
end $$;

-- Mantém a validade calculada no envio mesmo quando o site atualiza redeemed_by/device_id.
create or replace function public.bot_keep_delivery_expiry()
returns trigger language plpgsql as $$
begin
  if old.bot_sent_at is not null and new.bot_status <> 'revoked' then new.expires_at := old.expires_at; end if;
  return new;
end $$;
drop trigger if exists keys_keep_bot_delivery_expiry on public.keys;
create trigger keys_keep_bot_delivery_expiry before update on public.keys
for each row execute function public.bot_keep_delivery_expiry();

revoke all on function public.bot_reserve_key(bigint,bigint) from public, anon, authenticated;
revoke all on function public.bot_release_key(bigint) from public, anon, authenticated;
revoke all on function public.bot_deliver_key(bigint) from public, anon, authenticated;
revoke all on function public.bot_inspect_key(text) from public, anon, authenticated;
revoke all on function public.bot_revoke_key(uuid) from public, anon, authenticated;
grant execute on function public.bot_reserve_key(bigint,bigint) to service_role;
grant execute on function public.bot_release_key(bigint) to service_role;
grant execute on function public.bot_deliver_key(bigint) to service_role;
grant execute on function public.bot_inspect_key(text) to service_role;
grant execute on function public.bot_revoke_key(uuid) to service_role;
