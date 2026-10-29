-- Undo sticker/migration-email-lookup.sql. Safe to re-run.
-- Restores the live find_match from before the email+game change.
-- Drops sticker_games_for_email and the lookup log. Does not touch sticker_submit.

begin;

CREATE OR REPLACE FUNCTION sticker_admin.find_match(p_route text, p_game text, p_shop_key text, p_bought_on date, p_kiosk_email text, OUT result text, OUT print_log_id bigint, OUT game_title text, OUT shop_name text)
 RETURNS record
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
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
    select pl.id, pl.game_title, s.display_name into print_log_id, game_title, shop_name
      from public.print_logs pl
      left join public.sticker_shops s on lower(s.store_email) = lower(pl.store_email)
     where lower(pl.customer_email) = lower(btrim(p_kiosk_email))
       and not exists (select 1 from public.sticker_claims c
                        where c.print_log_id = pl.id and c.status <> 'Declined')
     order by pl.created_at
     limit 1;
    if print_log_id is not null then result := 'match'; return; end if;
    if exists (select 1 from public.print_logs pl
                where lower(pl.customer_email) = lower(btrim(p_kiosk_email))) then
      result := 'already_claimed';
    end if;
  end if;
end $function$;

revoke all on function sticker_admin.find_match(text,text,text,date,text) from public, anon, authenticated;

drop function if exists public.sticker_games_for_email(text);
drop table if exists sticker_admin.email_lookup_log;

commit;
