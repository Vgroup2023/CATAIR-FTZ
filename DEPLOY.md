# Hosting the FTZ system

The app has its own sign-in page with accounts stored in the database (passwords hashed with PBKDF2, sessions in an HttpOnly cookie).
There is one shared database, so run a **single instance**, and always serve over **HTTPS** (both options below do).

| Variable | Meaning |
|---|---|
| `FTZ_AUTH_USER` / `FTZ_AUTH_PASSWORD` | Creates the first administrator **if that user does not exist yet**. Changing these later does not change an existing password: use *My Account* or *forgot password*. |
| `FTZ_AUTH_EMAIL` | Email for that administrator (used for emailed reset links) |
| `FTZ_USERS` | Extra starter accounts: `alice:pw1;bob:pw2` |
| `FTZ_DEMO_MODE` | `true` shows the public demo login (`demo-admin`) on the front page and loads sample data. **Never use with real records.** |
| `FTZ_DEMO_PASSWORD` | Optional custom demo password (default `Demo-FTZ-2026!`) |
| `FTZ_TRUST_PROXY` | `1` when behind Render/Caddy so secure cookies and rate limits work |
| `FTZ_BASE_URL`, `FTZ_SMTP_HOST`, `FTZ_SMTP_PORT`, `FTZ_SMTP_USER`, `FTZ_SMTP_PASSWORD`, `FTZ_SMTP_FROM` | Turn on emailed reset links (STARTTLS SMTP). `FTZ_BASE_URL` is your public address, e.g. `https://ftz.example.com` |
| `FTZ_DB` | Database path (must be on a persistent disk) |
| `FTZ_RECON_AUTO`, `FTZ_RECON_HOURS` | Daily reconciliation job: `0` turns it off; interval in hours (default 24) |

If no account exists at start-up, the server creates `admin` with a one-time password and prints it in the log (Render: **Logs** tab).

Existing databases are upgraded automatically at start-up (new columns and safeguards are added; old history is kept).

## Demo login and forgotten passwords
* **Demo:** with `FTZ_DEMO_MODE=true` the front page shows the `demo-admin` credentials. That account sees sample data, cannot change its password, manage users or download backups. Anyone on the internet can use it, so keep real data off any site that has demo mode on.
* **Forgot password:** *Forgot your password?* on the front page issues a single-use link valid for 30 minutes. With SMTP configured it is emailed to the account's address. **Without SMTP the link is written to the server log** (Render **Logs** tab), so only the site owner can retrieve and pass it on. Reset links are never shown on the web page, and the page always answers the same way whether or not the account exists.
* Setting a new password signs that user out everywhere. Sign-in is limited to 5 wrong tries per 15 minutes.

## Option A — Render (least work)
`render.yaml` is the **free test plan**: no disk, so data is wiped on every restart/redeploy/sleep — sample data only. For real records, rename `render.paid.yaml` to `render.yaml` (paid plan + 1 GB disk) before step 1.

1. Render dashboard → **New → Blueprint** → select this repo (it reads `render.yaml`).
2. When prompted, enter `FTZ_AUTH_PASSWORD` (long, unique). Change `FTZ_AUTH_USER` if you like. This becomes the administrator account; the blueprint also turns demo mode on for testing.
3. Deploy. Your URL is `https://ftz-system.onrender.com` (or similar) and opens the front page.
4. Free plan: the service sleeps after ~15 minutes idle (first visit then takes about a minute). The 1 GB disk in `render.paid.yaml` needs a paid plan.

## Option B — VPS (Ubuntu, any $5 host) with automatic HTTPS
Point a DNS `A` record (e.g. `ftz.example.com`) at the server, then:
```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/Vgroup2023/CATAIR-FTZ.git && cd CATAIR-FTZ
docker build -t ftz .
docker volume create ftz-data
docker run -d --name ftz --restart unless-stopped -p 127.0.0.1:8214:8214 \
  -v ftz-data:/data -e FTZ_TRUST_PROXY=1 -e FTZ_AUTH_USER=alice -e FTZ_AUTH_PASSWORD='CHANGE-ME-long-passphrase' ftz
# HTTPS reverse proxy (obtains and renews a certificate itself)
docker run -d --name caddy --restart unless-stopped --network host \
  -v caddy-data:/data caddy caddy reverse-proxy --from ftz.example.com --to 127.0.0.1:8214
```
Open ports 80 and 443 only. Update later with `git pull && docker build -t ftz . && docker rm -f ftz` and re-run the `docker run` line.

## Backups (do this — it is your compliance record)
Signed in as an administrator, download **Audit & Export → Full backup** regularly (it contains password hashes: keep it private), or on a VPS run daily via cron:
```bash
docker exec ftz python -c "import sqlite3;s=sqlite3.connect('/data/ftz.db');d=sqlite3.connect('/data/backup.db');s.backup(d)" \
 && docker cp ftz:/data/backup.db ./ftz-$(date +%F).db
```
CBP recordkeeping periods apply to these records; keep backups for the period your broker specifies.

## Before real use
Set the IT/TE/IE transit days for your port (Setup → Settings), use strong unique passwords, and have your broker confirm the rules encoded in the app.
