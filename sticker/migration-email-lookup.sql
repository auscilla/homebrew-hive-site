-- Email route for the free-sticker page.
-- Apply on top of sticker_program_v2. Safe to re-run.
-- Does not change sticker_submit. Shop route inside find_match is unchanged.
-- Lookup window: created_at > now() - 90 days.
-- Lookup caps: 10 per visitor IP per hour, 300 per hour overall.
-- Over the cap the function returns {"ok":false,"status":"busy"}.

begin;

create table if not exists sticker_admin.email_lookup_log (
  ip_hash    text,
  created_at timestamptz not null default now()
);
create index if not exists email_lookup_log_created_idx
  on sticker_admin.email_lookup_log (created_at);
create index if not exists email_lookup_log_ip_created_idx
  on sticker_admin.email_lookup_log (ip_hash, created_at);
revoke all on table sticker_admin.email_lookup_log from public, anon, authenticated;

create or replace function public.sticker_games_for_email(p_email text)
returns json
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_email text := lower(btrim(coalesce(p_email, '')));
  v_ip text;
  v_n int;
  v_games json;
begin
  -- One lookup at a time so the caps cannot be raced (lock releases at commit).
  perform pg_advisory_xact_lock(hashtext('sticker_games_for_email'));

  delete from sticker_admin.email_lookup_log
   where created_at < now() - interval '2 days';

  v_ip := sticker_admin.request_ip_hash();

  if v_ip is not null then
    select count(*) into v_n
      from sticker_admin.email_lookup_log
     where ip_hash = v_ip
       and created_at > now() - interval '1 hour';
    if v_n >= 10 then
      return json_build_object('ok', false, 'status', 'busy');
    end if;
  end if;

  select count(*) into v_n
    from sticker_admin.email_lookup_log
   where created_at > now() - interval '1 hour';
  if v_n >= 300 then
    return json_build_object('ok', false, 'status', 'busy');
  end if;

  insert into sticker_admin.email_lookup_log (ip_hash) values (v_ip);

  -- Same statement for a real email, an unknown email, and junk.
  -- Equality only: never LIKE. Empty input matches nothing.
  select coalesce(json_agg(json_build_object(
           'game_title', s.game_title,
           'bought_on', s.bought_on
         ) order by s.created_at desc), '[]'::json)
    into v_games
    from (
      select pl.game_title,
             (pl.created_at at time zone 'America/New_York')::date as bought_on,
             pl.created_at
        from public.print_logs pl
       where v_email <> ''
         and lower(pl.customer_email) = v_email
         and pl.created_at > now() - interval '90 days'
         and not exists (
           select 1 from public.sticker_claims c
            where c.print_log_id = pl.id
              and c.status <> 'Declined')
       order by pl.created_at desc
       limit 20
    ) s;

  return json_build_object('games', v_games);
end $$;

revoke all on function public.sticker_games_for_email(text) from public, anon, authenticated;
grant execute on function public.sticker_games_for_email(text) to anon, authenticated;

create or replace function sticker_admin.find_match(
  p_route text, p_game text, p_shop_key text, p_bought_on date, p_kiosk_email text,
  out result text, out print_log_id bigint, out game_title text, out shop_name text)
language plpgsql stable security definer set search_path = '' as $$
declare v_store text; v_any_claimed boolean;
begin
  result := 'no_match';
  if p_route = 'shop' then
    if p_shop_key is null or p_shop_key = 'not_listed' then
      result := 'shop_not_listed'; return;
    end if;
    select s.store_email, s.display_name into v_store, shop_name
      from public.sticker_shops s where s.shop_key = p_shop_key and s.active;
    if v_store is null or p_game is null or p_bought_on is null then return; end if;

    -- closest unclaimed print of that game at that shop within +/- 3 days (Eastern dates)
    select pl.id, pl.game_title into print_log_id, game_title
      from public.print_logs pl
     where lower(btrim(pl.game_title)) = lower(btrim(p_game))
       and lower(pl.store_email) = lower(v_store)
       and (pl.created_at at time zone 'America/New_York')::date between p_bought_on - 3 and p_bought_on + 3
       and not exists (select 1 from public.sticker_claims c
                        where c.print_log_id = pl.id and c.status <> 'Declined')
     order by abs((pl.created_at at time zone 'America/New_York')::date - p_bought_on), pl.created_at
     limit 1;
    if print_log_id is not null then result := 'match'; return; end if;

    -- was there a print in the window that is already claimed?
    select exists (select 1 from public.print_logs pl
                    where lower(btrim(pl.game_title)) = lower(btrim(p_game))
                      and lower(pl.store_email) = lower(v_store)
                      and (pl.created_at at time zone 'America/New_York')::date between p_bought_on - 3 and p_bought_on + 3)
      into v_any_claimed;
    if v_any_claimed then result := 'already_claimed'; end if;

  elsif p_route = 'email' then
    if p_kiosk_email is null or btrim(p_kiosk_email) = '' then return; end if;
    if p_game is null or btrim(p_game) = '' then return; end if;

    -- oldest unclaimed print for this email + game, and this Eastern day when a date was given
    select pl.id, pl.game_title, s.display_name into print_log_id, game_title, shop_name
      from public.print_logs pl
      left join public.sticker_shops s on lower(s.store_email) = lower(pl.store_email)
     where lower(pl.customer_email) = lower(btrim(p_kiosk_email))
       and lower(btrim(pl.game_title)) = lower(btrim(p_game))
       and (p_bought_on is null
            or (pl.created_at at time zone 'America/New_York')::date = p_bought_on)
       and not exists (select 1 from public.sticker_claims c
                        where c.print_log_id = pl.id and c.status <> 'Declined')
     order by pl.created_at
     limit 1;
    if print_log_id is not null then result := 'match'; return; end if;

    select exists (select 1 from public.print_logs pl
                    where lower(pl.customer_email) = lower(btrim(p_kiosk_email))
                      and lower(btrim(pl.game_title)) = lower(btrim(p_game))
                      and (p_bought_on is null
                           or (pl.created_at at time zone 'America/New_York')::date = p_bought_on))
      into v_any_claimed;
    if v_any_claimed then result := 'already_claimed'; end if;
  end if;
end $$;

revoke all on function sticker_admin.find_match(text,text,text,date,text) from public, anon, authenticated;

commit;
