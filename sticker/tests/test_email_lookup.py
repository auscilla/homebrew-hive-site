#!/usr/bin/env python3
"""Email-lookup checks against the LOCAL sticker_test database only.

Expects migration v2 and tests/seed_fake.sql already applied (fake rows).
Applies sticker/migration-email-lookup.sql itself. Never uses a hosted database.
"""
import json, os, sys
import psycopg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATION = os.path.join(ROOT, "sticker", "migration-email-lookup.sql")
ROLLBACK = os.path.join(ROOT, "sticker", "migration-email-lookup-rollback.sql")
DSN = os.environ.get("STICKER_DSN", "host=127.0.0.1 port=54329 user=postgres dbname=sticker_test")
results = []

def conn():
    return psycopg.connect(DSN, autocommit=True)

ADMIN = conn()

def admin(sql, params=None):
    cur = ADMIN.execute(sql, params)
    try:
        return cur.fetchall()
    except psycopg.ProgrammingError:
        return None

def as_web(sql, params=None, ip="203.0.113.10", role="anon", headers=None):
    c = conn()
    try:
        with c.transaction():
            c.execute(f"set local role {role}")
            c.execute("select set_config('request.jwt.claims', %s, true)", [json.dumps({"role": role})])
            h = headers if headers is not None else ({"cf-connecting-ip": ip} if ip else {})
            c.execute("select set_config('request.headers', %s, true)", [json.dumps(h)])
            cur = c.execute(sql, params)
            return cur.fetchall() if cur.description else None
    finally:
        c.close()

def apply_sql(path):
    sql = open(path).read()
    ADMIN.execute(sql)

def lookup_text(email, ip="203.0.113.40", role="anon", headers=None):
    rows = as_web(
        "select public.sticker_games_for_email(%s)::text",
        [email], ip=ip, role=role, headers=headers)
    return rows[0][0]

def lookup(email, **kw):
    return json.loads(lookup_text(email, **kw))

def check(route, game=None, shop=None, bought=None, kemail=None, ip="203.0.113.10"):
    rows = as_web(
        """select public.sticker_check(p_route=>%s, p_game=>%s, p_shop_key=>%s,
               p_bought_on=>%s, p_kiosk_email=>%s)""",
        [route, game, shop, bought, kemail], ip=ip)
    return rows[0][0]

ADDR = dict(name="Test Person", email="buyer@example.com", street="100 Test Ave", apt=None,
            city="Testville", state="CT", zip="06511", note=None)

def submit(route, game=None, shop=None, bought=None, kemail=None, ip="203.0.113.10", **addr):
    a = dict(ADDR)
    a.update(addr)
    rows = as_web(
        """select public.sticker_submit(p_route=>%s, p_game=>%s, p_shop_key=>%s, p_bought_on=>%s,
              p_kiosk_email=>%s, p_name=>%s, p_email=>%s, p_street=>%s, p_apt=>%s, p_city=>%s,
              p_state=>%s, p_zip=>%s, p_note=>%s, p_trap=>%s)""",
        [route, game, shop, bought, kemail, a["name"], a["email"], a["street"], a["apt"],
         a["city"], a["state"], a["zip"], a["note"], None], ip=ip)
    return rows[0][0]

def claims():
    cols = ["id", "route", "game_title", "print_log_id", "status", "review_reason", "kiosk_email"]
    rows = admin(f"select {','.join(cols)} from public.sticker_claims order by id")
    return [dict(zip(cols, r)) for r in rows]

def add_print(email, game, when_sql, params=None, store="test-shop-a@example.com"):
    return admin(
        f"""insert into public.print_logs (created_at, store_email, game_title, customer_email)
            values ({when_sql}, %s, %s, %s)
            returning id, (created_at at time zone 'America/New_York')::date::text""",
        (params or []) + [store, game, email])[0]

def reset():
    admin("truncate public.sticker_claims restart identity")
    admin("delete from sticker_admin.email_lookup_log")
    admin("delete from public.print_logs where id > 100")

def case(name):
    def deco(fn):
        reset()
        try:
            detail = fn() or ""
            results.append((name, True, detail))
        except AssertionError as e:
            results.append((name, False, f"ASSERT: {e}"))
        except Exception as e:
            results.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return deco

def eq(a, b, msg=""):
    assert a == b, f"{msg} expected {b!r}, got {a!r}"

def denied(fn):
    try:
        fn()
    except psycopg.errors.InsufficientPrivilege:
        return True
    raise AssertionError("expected permission denied, but the call succeeded")

def shop_slice(defn):
    start = defn.index("if p_route = 'shop' then")
    end = defn.index("elsif p_route = 'email' then")
    return defn[start:end]

def fn_def(sig):
    return admin("select pg_get_functiondef(%s::regprocedure)", [sig])[0][0]

FIND = "sticker_admin.find_match(text,text,text,date,text)"
SUBMIT = "public.sticker_submit(text,text,text,date,text,text,text,text,text,text,text,text,text,text)"

# Snapshot before the new migration, then apply twice (re-run safe).
shop_before = shop_slice(fn_def(FIND))
submit_before = fn_def(SUBMIT)
apply_sql(MIGRATION)
apply_sql(MIGRATION)

@case("00 migration is re-runnable and does not touch shop route or sticker_submit")
def _():
    eq(shop_slice(fn_def(FIND)), shop_before, "shop route")
    eq(fn_def(SUBMIT), submit_before, "sticker_submit")
    vol = admin("""select prosecdef, provolatile
                     from pg_proc join pg_namespace n on n.oid = pronamespace
                    where n.nspname = 'public' and proname = 'sticker_games_for_email'""")[0]
    eq(vol, (True, "v"), "security definer + volatile")
    src = fn_def("public.sticker_games_for_email(text)").lower()
    assert " like " not in src and "~~" not in src, "lookup must not use LIKE"
    return "shop block unchanged; sticker_submit unchanged; lookup is volatile security definer; no LIKE"

@case("01 exact email returns only that buyer's games, newest first")
def _():
    newer = add_print("buyer@example.com", "Sap Tapper", "now() - interval '1 day'")
    older = add_print("buyer@example.com", "Hive Runner", "now() - interval '5 days'")
    body = lookup("buyer@example.com", ip="203.0.113.41")
    eq([g["game_title"] for g in body["games"]], ["Sap Tapper", "Hive Runner"])
    eq(body["games"][0]["bought_on"], newer[1])
    eq(body["games"][1]["bought_on"], older[1])
    eq(set(body.keys()), {"games"})
    for g in body["games"]:
        eq(set(g.keys()), {"game_title", "bought_on"})
    text = lookup_text("buyer@example.com", ip="203.0.113.41")
    assert "buyer@example.com" not in text and "@" not in text
    assert str(newer[0]) not in text
    return f"newest Sap Tapper {newer[1]}, then Hive Runner {older[1]}; no email, no id"

@case("02 case and surrounding spaces match; stored mixed case matches")
def _():
    a = lookup_text("gamer.one@example.com", ip="203.0.113.42")
    b = lookup_text("  GAMER.ONE@EXAMPLE.COM ", ip="203.0.113.42")
    c = lookup_text("Gamer.One@Example.com", ip="203.0.113.42")
    eq(a, b)
    eq(a, c)
    body = json.loads(a)
    eq(body["games"], [{"game_title": "Honey Drop", "bought_on": "2026-10-20"}])
    return "three spellings, one json; Honey Drop on 2026-10-20"

@case("03 unknown, known-but-empty, and all-claimed are the same json")
def _():
    add_print("old.buyer@example.com", "Old Game", "now() - interval '91 days'")
    claimed_id, _day = add_print("claimed.buyer@example.com", "Claimed Game", "now() - interval '2 days'")
    admin("""insert into public.sticker_claims (route, name, email, status, print_log_id)
             values ('email', 'T', 't@example.com', 'Matched', %s)""", [claimed_id])
    ip = "203.0.113.43"
    unknown = lookup_text("nobody-unknown@example.com", ip=ip)
    known_empty = lookup_text("old.buyer@example.com", ip=ip)
    all_claimed = lookup_text("claimed.buyer@example.com", ip=ip)
    eq(unknown, known_empty, "unknown vs only-old")
    eq(unknown, all_claimed, "unknown vs all claimed")
    eq(json.loads(unknown), {"games": []})
    assert "Old Game" not in unknown and "Claimed Game" not in unknown
    return "three empties are byte-identical {games:[]}"

@case("04 claimed print is hidden; Declined makes it claimable again")
def _():
    keep = add_print("again@example.com", "Keep Game", "now() - interval '2 days'")
    back = add_print("again@example.com", "Back Game", "now() - interval '3 days'")
    admin("""insert into public.sticker_claims (route, name, email, status, print_log_id)
             values ('email', 'T', 't@example.com', 'Matched', %s)""", [back[0]])
    titles = [g["game_title"] for g in lookup("again@example.com", ip="203.0.113.44")["games"]]
    eq(titles, ["Keep Game"])
    admin("update public.sticker_claims set status = 'Declined' where print_log_id = %s", [back[0]])
    titles = [g["game_title"] for g in lookup("again@example.com", ip="203.0.113.44")["games"]]
    eq(titles, ["Keep Game", "Back Game"])
    eq(check("email", "Back Game", bought=back[1], kemail="again@example.com")["result"], "match")
    return "Matched row hidden; after Declined, Back Game is listed and matches"

@case("05 email A never includes email B, and junk is not a pattern")
def _():
    add_print("a.buyer@example.com", "Alpha Game", "now() - interval '1 day'")
    add_print("b.buyer@example.com", "Beta Game", "now() - interval '1 day'")
    add_print("ab@example.com", "Ab Game", "now() - interval '1 day'")
    ip = "203.0.113.45"
    text_a = lookup_text("a.buyer@example.com", ip=ip)
    assert "Alpha Game" in text_a and "Beta Game" not in text_a and "Ab Game" not in text_a
    empty = lookup_text("nobody-unknown@example.com", ip="203.0.113.46")
    for bad in (None, "", "%", "_", "a@", "a.buyer@example.com%", "b.buyer@example.co"):
        got = lookup_text(bad, ip="203.0.113.46")
        eq(got, empty, repr(bad))
        assert "Alpha Game" not in got and "Beta Game" not in got
    # quote in the local part is still an equality compare, not SQL
    eq(lookup_text("a'b@example.com", ip="203.0.113.46"), empty)
    eq(admin("select count(*) from public.sticker_claims")[0][0], 0)
    return "A's list has only Alpha Game; null/empty/%/_/a@ match the unknown json"

@case("06 no email list: anon cannot read claims or the log; wildcards stay empty")
def _():
    lookup("a.buyer@example.com", ip="203.0.113.47")
    for role in ("anon", "authenticated"):
        denied(lambda r=role: as_web("select * from public.sticker_claims", role=r))
        denied(lambda r=role: as_web("select * from sticker_admin.email_lookup_log", role=r))
        denied(lambda r=role: as_web("select * from sticker_admin.settings", role=r))
        denied(lambda r=role: as_web(
            "select sticker_admin.find_match('email','Bee Quest',null,null,'a@b.co')", role=r))
        denied(lambda r=role: as_web("select sticker_admin.request_ip_hash()", role=r))
    # login.html still reads print_logs with the anon key. This migration does not revoke that.
    n = as_web("select count(*) from public.print_logs")[0][0]
    assert n >= 7, n
    eq(as_web("select has_function_privilege('anon', 'public.sticker_games_for_email(text)', 'execute')")[0][0], True)
    eq(as_web("select has_function_privilege('authenticated', 'public.sticker_games_for_email(text)', 'execute')")[0][0], True)
    eq(admin("select has_function_privilege('anon', %s, 'execute')", [FIND])[0][0], False)
    admin("do $$ begin create role sticker_nobody nologin; exception when duplicate_object then null; end $$")
    eq(admin("select has_function_privilege('sticker_nobody', 'public.sticker_games_for_email(text)', 'execute')")[0][0], False)
    # page tables used today still readable
    as_web("select title from public.games limit 1")
    as_web("select game_title from public.game_upsells limit 1")
    return "anon/authenticated blocked from claims, log, and sticker_admin; lookup execute granted; print_logs select left as it was"

@case("07 prints older than 90 days are left out; Eastern date is the calendar day")
def _():
    recent = add_print("window@example.com", "Recent Game", "now() - interval '89 days'")
    add_print("window@example.com", "Ancient Game", "now() - interval '91 days'")
    edge_id, edge_day = add_print(
        "window@example.com", "Edge Game",
        "((date_trunc('day', (now() at time zone 'utc')) + interval '3 hours 30 minutes') at time zone 'utc')")
    titles = [g["game_title"] for g in lookup("window@example.com", ip="203.0.113.48")["games"]]
    assert "Recent Game" in titles and "Ancient Game" not in titles
    assert "Edge Game" in titles
    # 03:30 UTC is still the previous evening in New York
    utc_day = admin("select ((date_trunc('day', (now() at time zone 'utc')) + interval '3 hours 30 minutes') at time zone 'utc')::date::text")[0][0]
    assert edge_day != utc_day, (edge_day, utc_day)
    got = [g for g in lookup("window@example.com", ip="203.0.113.48")["games"] if g["game_title"] == "Edge Game"][0]
    eq(got["bought_on"], edge_day)
    assert recent[0] != edge_id
    return f"91-day print dropped; 03:30 UTC shows as Eastern {edge_day} (UTC day {utc_day})"

@case("08 at most 20 games, newest first")
def _():
    for i in range(21):
        add_print("many@example.com", f"Game {i:02d}", "now() - (%s || ' minutes')::interval", [i])
    games = lookup("many@example.com", ip="203.0.113.49")["games"]
    eq(len(games), 20)
    eq(games[0]["game_title"], "Game 00")
    eq(games[-1]["game_title"], "Game 19")
    assert all(g["game_title"] != "Game 20" for g in games)
    return "20 of 21, Game 00 first, Game 20 dropped"

@case("09 per-visitor cap trips on the 11th; global cap; null IP is global only")
def _():
    ip = "203.0.113.61"
    for _i in range(10):
        eq(lookup("nobody-cap@example.com", ip=ip), {"games": []})
    busy = lookup("nobody-cap@example.com", ip=ip)
    eq(busy, {"ok": False, "status": "busy"})
    eq(lookup("nobody-cap@example.com", ip=ip), busy)
    eq(admin("select count(*) from sticker_admin.email_lookup_log")[0][0], 10)
    # a different visitor still can
    eq("games" in lookup("nobody-cap@example.com", ip="203.0.113.62"), True)
    # rows older than an hour no longer count
    admin("""update sticker_admin.email_lookup_log
                set created_at = now() - interval '2 hours'
              where created_at > now() - interval '1 hour'""")
    eq(lookup("nobody-cap@example.com", ip=ip), {"games": []})
    # prune: older than 2 days goes away on the next call; a 1-day row stays
    admin("delete from sticker_admin.email_lookup_log")
    admin("""insert into sticker_admin.email_lookup_log (ip_hash, created_at) values
             ('stale', now() - interval '3 days'),
             ('kept', now() - interval '1 day')""")
    lookup("nobody-cap@example.com", ip="203.0.113.63")
    left = [r[0] for r in admin("select ip_hash from sticker_admin.email_lookup_log order by ip_hash")]
    assert "stale" not in left and "kept" in left, left
    # global: 300 in the last hour blocks a brand-new IP, and a null IP
    admin("delete from sticker_admin.email_lookup_log")
    admin("""insert into sticker_admin.email_lookup_log (ip_hash, created_at)
             select 'filler', now() from generate_series(1, 300)""")
    eq(lookup("nobody-cap@example.com", ip="203.0.113.64"), {"ok": False, "status": "busy"})
    eq(lookup("nobody-cap@example.com", headers={}), {"ok": False, "status": "busy"})
    eq(admin("select count(*) from sticker_admin.email_lookup_log")[0][0], 300)
    # under the global cap, a missing IP is not stopped at 11
    admin("delete from sticker_admin.email_lookup_log")
    for _i in range(11):
        body = lookup("nobody-cap@example.com", headers={})
        eq(body, {"games": []})
    eq(admin("select count(*) from sticker_admin.email_lookup_log where ip_hash is null")[0][0], 11)
    return "11th from one IP is busy (10 rows kept); 300 global is busy; null IP skips the per-visitor cap"

@case("10 shop route still matches the way it did")
def _():
    eq(check("shop", "Bee Quest", "shopa", "2026-10-11"),
       {"result": "match", "game": "Bee Quest", "shop": "Test Shop A"})
    eq(check("shop", "Bee Quest", "shopb", "2026-10-10")["result"], "no_match")
    eq(check("shop", "Bee Quest", "not_listed", "2026-10-10")["result"], "shop_not_listed")
    eq(check("shop", "Bee Quest", "shopc", "2026-10-10")["result"], "no_match")
    eq(check("shop", "  bee QUEST ", "shopa", "2026-10-10")["result"], "match")
    eq(check("shop", "Hive Runner", "shopa", "2026-10-13")["result"], "match")
    eq(check("shop", "Hive Runner", "shopa", "2026-10-14")["result"], "no_match")
    eq(submit("shop", "Bee Quest", "shopa", "2026-10-11")["status"], "received")
    c = claims()[0]
    eq(c["status"], "Matched")
    eq(c["print_log_id"], 1)
    eq(check("shop", "Bee Quest", "shopa", "2026-10-10")["result"], "already_claimed")
    return "shop match, no_match, not_listed, inactive, case, +/- 3 Eastern days, one claim"

@case("11 email submit binds the chosen print; wrong email does not")
def _():
    sap = add_print("picker@example.com", "Sap Tapper", "now() - interval '2 days'")
    hive = add_print("picker@example.com", "Hive Runner", "now() - interval '4 days'")
    # same game and same Eastern day: two times, oldest wins
    noon = add_print(
        "picker@example.com", "Same Day",
        "(date_trunc('day', now() at time zone 'America/New_York') at time zone 'America/New_York') + interval '12 hours'")
    three = add_print(
        "picker@example.com", "Same Day",
        "(date_trunc('day', now() at time zone 'America/New_York') at time zone 'America/New_York') + interval '16 hours'")
    eq(noon[1], three[1], "same Eastern day")
    other = add_print("other@example.com", "Sap Tapper", "now() - interval '2 days'")
    shared_a = add_print("shared.a@example.com", "Shared Quest", "now() - interval '3 days'")
    shared_b = add_print("shared.b@example.com", "Shared Quest", "now() - interval '3 days'")

    eq(check("email", kemail="picker@example.com")["result"], "no_match", "email alone")
    eq(check("email", "Nope", bought=sap[1], kemail="picker@example.com")["result"], "no_match", "unknown game")
    eq(check("email", "Sap Tapper", bought=hive[1], kemail="picker@example.com")["result"], "no_match", "wrong day")
    eq(check("email", "Hive Runner", bought=hive[1], kemail="other@example.com")["result"], "no_match", "wrong email")

    eq(submit("email", "Hive Runner", bought=hive[1], kemail="other@example.com",
              ip="203.0.113.71", street="1 Wrong St")["status"], "received")
    bad = claims()[0]
    eq(bad["status"], "Needs Review")
    eq(bad["review_reason"], "no_match")
    eq(bad["print_log_id"], None)
    eq(bad["kiosk_email"], "other@example.com")

    eq(submit("email", "Sap Tapper", bought=sap[1], kemail="Picker@Example.com",
              ip="203.0.113.72", street="2 Right St")["status"], "received")
    eq(submit("email", "Hive Runner", bought=hive[1], kemail="picker@example.com",
              ip="203.0.113.73", street="3 Right St")["status"], "received")
    eq(submit("email", "Same Day", bought=noon[1], kemail="picker@example.com",
              ip="203.0.113.74", street="4 Right St")["status"], "received")
    eq(submit("email", "Shared Quest", bought=shared_a[1], kemail="shared.a@example.com",
              ip="203.0.113.75", street="5 Right St")["status"], "received")

    by_game = {c["game_title"]: c for c in claims() if c["status"] == "Matched"}
    eq(by_game["Sap Tapper"]["print_log_id"], sap[0])
    eq(by_game["Hive Runner"]["print_log_id"], hive[0])
    eq(by_game["Same Day"]["print_log_id"], noon[0])
    eq(by_game["Shared Quest"]["print_log_id"], shared_a[0])
    assert shared_b[0] not in [c["print_log_id"] for c in claims()]
    assert other[0] not in [c["print_log_id"] for c in claims()]

    # second claim on the same print does not take it
    eq(submit("email", "Sap Tapper", bought=sap[1], kemail="picker@example.com",
              ip="203.0.113.76", street="9 Other St")["status"], "received")
    dup = claims()[-1]
    eq(dup["status"], "Needs Review")
    eq(dup["review_reason"], "already_claimed")
    eq(dup["print_log_id"], None)
    eq(check("email", "Sap Tapper", bought=sap[1], kemail="picker@example.com")["result"], "already_claimed")
    return "each game/date bound to its own print; wrong email and wrong day do not; oldest same-day print wins"

@case("12 household, daily, and per-IP caps are unchanged")
def _():
    for i, (st, apt) in enumerate((("12 Elm Street", "Apt 4"), ("12 elm st", "#4"), ("12 Elm St.", "Unit 4"))):
        eq(submit("shop", "Bee Quest", "not_listed", "2026-10-10",
                  ip=f"198.51.100.{i+1}", street=st, apt=apt, zip="06511")["status"], "received")
    eq(submit("shop", "Honey Drop", "shopa", "2026-10-15",
              ip="198.51.100.9", street="12  ELM   STREET", apt="apartment 4", zip="06511-1234")["status"], "received")
    c = claims()
    eq(c[3]["status"], "Needs Review")
    eq(c[3]["review_reason"], "household_limit")
    eq(c[3]["print_log_id"], None)
    reset()
    admin("""insert into public.sticker_claims (route, name, email, status, review_reason, created_at)
             select 'shop','Filler','filler@example.com','Needs Review','no_match', now() - interval '1 hour'
               from generate_series(1,24)""")
    eq(submit("shop", "Bee Quest", "shopa", "2026-10-10", ip="192.0.2.25")["status"], "received")
    eq(submit("shop", "Hive Runner", "shopa", "2026-10-10", ip="192.0.2.26"),
       {"ok": False, "status": "busy"})
    reset()
    for i in range(3):
        eq(submit("shop", "Bee Quest", "not_listed", "2026-10-10",
                  ip="192.0.2.77", street=f"{i+1} Main St")["status"], "received")
    eq(submit("shop", "Bee Quest", "not_listed", "2026-10-10", ip="192.0.2.77", street="4 Main St"),
       {"ok": False, "status": "busy"})
    eq(submit("shop", "Bee Quest", "not_listed", "2026-10-10", ip="192.0.2.78", street="5 Main St")["status"], "received")
    return "4th household -> Needs Review; 26th of the day busy; 4th from one IP busy"

@case("13 rollback restores live find_match, then the migration applies again")
def _():
    apply_sql(ROLLBACK)
    missing = admin("""select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                        where n.nspname = 'public' and p.proname = 'sticker_games_for_email'""")[0][0]
    eq(missing, 0)
    gone = admin("""select count(*) from information_schema.tables
                     where table_schema = 'sticker_admin' and table_name = 'email_lookup_log'""")[0][0]
    eq(gone, 0)
    # live email route: oldest unclaimed print for the email, no game required
    eq(check("email", kemail="gamer.one@example.com"),
       {"result": "match", "game": "Honey Drop", "shop": "Test Shop B"})
    apply_sql(ROLLBACK)  # second time is a no-op besides replacing find_match again
    apply_sql(MIGRATION)
    eq(check("email", kemail="gamer.one@example.com")["result"], "no_match")
    eq(check("email", "Honey Drop", bought="2026-10-20", kemail="gamer.one@example.com")["result"], "match")
    eq(lookup("gamer.one@example.com", ip="203.0.113.80")["games"][0]["game_title"], "Honey Drop")
    eq(shop_slice(fn_def(FIND)), shop_before)
    return "rollback drops the lookup and restores email-only match; re-apply requires game + day"

# leave the local db clean and migrated
reset()
apply_sql(MIGRATION)

passed = sum(1 for _, ok, _ in results if ok)
print(f"{passed}/{len(results)} passed")
for name, ok, detail in results:
    print(("PASS " if ok else "FAIL ") + name)
    if not ok or detail:
        print("     " + str(detail).replace("\n", " "))
sys.exit(0 if passed == len(results) else 1)
