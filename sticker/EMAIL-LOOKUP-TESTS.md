# Email lookup tests

Local Postgres only (127.0.0.1:54329, database sticker_test, fake rows). Nothing here talks to a hosted project.

## SQL

Existing suite on migration v2, before this change: 21/21 passed.

`sticker/tests/test_email_lookup.py` after `sticker/migration-email-lookup.sql`: 14/14 passed.

Same existing suite run again after the new migration: 19/21. The two failures are the old email-only match (cases 05 and 14). Shop route, household cap, daily cap, and per-IP cap still pass.

## Page

`sticker/tests/page_email_lookup.js` (Playwright, Chromium, RPCs mocked): 58/58 passed.

Screenshots are in `sticker/email-lookup-screenshots/` for 390x844, 375x667, and 320x568. No horizontal scroll on those screens.

## Decisions kept in the migration

- The 90-day window is `created_at > now() - interval '90 days'`. A print from exactly 90 days ago is excluded. The cutoff is an instant, not an Eastern calendar day. The date shown to the buyer is the America/New_York day.
- Lookup caps: 10 per visitor IP per rolling hour, and 300 per rolling hour overall. Over either cap the function returns `{"ok":false,"status":"busy"}`. Rows older than 2 days are deleted inside the function. If the IP hash is null, only the global cap applies.
- Email match uses the exact Eastern day when a date is sent. On that day the oldest print wins. The shop route is still plus or minus 3 days.
- After a game is picked the page still calls `sticker_check`, then `sticker_submit`. The thank-you line is the same for a match and a review.

## One thing left as it was

anon can still select `print_logs`. `login.html` reads that table with the anon key, and this change does not edit `login.html`, so the migration does not revoke that select. `customer_email` can already be listed that way. The new function does not return emails, and anon cannot read `sticker_claims` or the lookup log.
