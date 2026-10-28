# Running on Supabase (database and security) with the app on Fly.io or Railway

Supabase supplies the **database** (PostgreSQL, encrypted at rest, daily backups, point-in-time recovery on paid plans). It cannot run this Python
server, so the app container runs on **Fly.io** or **Railway** and connects to Supabase over TLS.

```
browser ──https──▶ app container (Fly / Railway) ──TLS, role ftz_app──▶ Supabase PostgreSQL
```

The code is the same as before. When `DATABASE_URL` is set it uses PostgreSQL; when it is not, it uses the SQLite file exactly as it did.

## 1. Supabase project
Create a project (region `us-east-1` is closest to most U.S. ports). The free plan allows two active projects per organization; pause one or upgrade if Supabase refuses.

## 2. Create the tables and lock the database
The SQL is in `supabase/migrations/` and is generated from the same schema the app uses (`python3 tools/make_pg_migration.py` rebuilds it).

* `…_ftz_schema.sql` creates the tables and the database safeguards: negative stock is refused, and the audit log and the inventory ledger cannot be updated, deleted or truncated.
* `…_ftz_security.sql` turns on row level security for every table, removes all access for Supabase's public `anon` / `authenticated` roles (so the REST API cannot see these tables), and creates the role `ftz_app` the app connects as: no superuser, no DDL, no `UPDATE` / `DELETE` on the audit log and ledger.

Apply them with the Supabase CLI (`supabase db push`), the SQL editor, or by asking Claude to run them through the Supabase connector. Then set the app role's password yourself (it is never stored in the repo):

```sql
ALTER ROLE ftz_app PASSWORD 'a-long-random-password';
```

## 3. Connection string
Dashboard → *Project Settings → Database → Connection string → Session pooler* (it works over IPv4; the direct host is IPv6-only on most plans). Use the role `ftz_app`, written `ftz_app.<project-ref>`:

```
postgresql://ftz_app.<project-ref>:<password>@aws-0-us-east-1.pooler.supabase.com:5432/postgres
```
TLS is added automatically (`sslmode=require`). Keep it secret: it goes into the host's secret store, never into git.

## 4. Deploy the app
**Fly.io** (`fly.toml` is included):
```
fly launch --no-deploy --copy-config
fly secrets set DATABASE_URL='postgresql://…' FTZ_AUTH_PASSWORD='a long unique password'
fly deploy
```
**Railway** (`railway.json` is included): new project from the GitHub repo, then add the variables `DATABASE_URL`, `FTZ_AUTH_PASSWORD`, `FTZ_HOST=0.0.0.0`, `FTZ_TRUST_PROXY=1`, `FTZ_COOKIE_SECURE=1`, `FTZ_SKIP_DDL=1`.

`FTZ_SKIP_DDL=1` tells the app not to create tables at start-up, because the migrations already did and `ftz_app` has no DDL rights. For a quick start without the migrations, leave it off and connect with the owner role instead (less secure).

Leave `FTZ_DEMO_MODE` off for real records.

## What is protected, and how
| Risk | Control |
|---|---|
| Public database API | Row level security on, no policy for `anon` / `authenticated`, privileges revoked |
| Over-powered app account | `ftz_app`: no superuser, no DDL, no `BYPASSRLS`, cannot change or delete audit / ledger rows |
| History edited or erased | Triggers refuse `UPDATE` / `DELETE` / `TRUNCATE`; the SHA-256 hash chains still reveal tampering by an administrator |
| Negative inventory | Trigger refuses it even if app code has a bug |
| Two users at once | One writer at a time (advisory lock), so FIFO, counters and hash chains stay consistent |
| Data in transit | TLS required to Supabase; HTTPS at the host |
| Secrets | Connection string and passwords only in the host's secret store; the ABI password is never stored |
| Backups | Supabase daily backups (point-in-time recovery on paid plans); `/backup` (administrators) downloads a JSON copy of every table |

## Testing against PostgreSQL
`FTZ_TEST_PG_URL=postgresql://postgres@127.0.0.1:5433/postgres python3 -m unittest discover -s tests` runs the whole suite on a throw-away schema in a local PostgreSQL (with the `citext` extension). Without that variable the suite runs on SQLite as before.

## Known differences from SQLite mode
* The weekly report groups by ISO week (`2026-W43`) instead of SQLite's `%W`.
* `/backup` returns JSON rather than a database file.
