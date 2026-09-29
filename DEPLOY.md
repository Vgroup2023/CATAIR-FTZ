# Hosting the FTZ system

The server refuses to listen on a public interface unless a login is configured. Accounts come from environment variables:

| Variable | Meaning |
|---|---|
| `FTZ_AUTH_USER` / `FTZ_AUTH_PASSWORD` | One login |
| `FTZ_USERS` | Several logins: `alice:pw1;bob:pw2` (passwords cannot contain `;`) |
| `FTZ_DB` | Database path (must be on a persistent disk) |
| `FTZ_HOST` / `PORT` | Bind address / port (the Dockerfile binds `0.0.0.0`) |

Login is HTTP Basic auth, so **always serve over HTTPS** — the options below do. The signed-in name is written to the audit log and cannot be spoofed. There is one shared database, so run a **single instance**.

## Option A — Render (least work)
1. Render dashboard → **New → Blueprint** → select this repo (it reads `render.yaml`).
2. When prompted, enter `FTZ_AUTH_PASSWORD` (long, unique). Change `FTZ_AUTH_USER` if you like.
3. Deploy. Your URL is `https://ftz-system.onrender.com` (or similar). Browser asks for the login.
4. The 1 GB disk needs a paid plan; without a disk the database is wiped on every deploy.

## Option B — VPS (Ubuntu, any $5 host) with automatic HTTPS
Point a DNS `A` record (e.g. `ftz.example.com`) at the server, then:
```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/Vgroup2023/CATAIR-FTZ.git && cd CATAIR-FTZ
docker build -t ftz .
docker volume create ftz-data
docker run -d --name ftz --restart unless-stopped -p 127.0.0.1:8214:8214 \
  -v ftz-data:/data -e FTZ_USERS='alice:CHANGE-ME;bob:CHANGE-ME-TOO' ftz
# HTTPS reverse proxy (obtains and renews a certificate itself)
docker run -d --name caddy --restart unless-stopped --network host \
  -v caddy-data:/data caddy caddy reverse-proxy --from ftz.example.com --to 127.0.0.1:8214
```
Open ports 80 and 443 only. Update later with `git pull && docker build -t ftz . && docker rm -f ftz` and re-run the `docker run` line.

## Backups (do this — it is your compliance record)
Signed in, download **Audit & Export → Full backup** regularly, or on a VPS run daily via cron:
```bash
docker exec ftz python -c "import sqlite3;s=sqlite3.connect('/data/ftz.db');d=sqlite3.connect('/data/backup.db');s.backup(d)" \
 && docker cp ftz:/data/backup.db ./ftz-$(date +%F).db
```
CBP recordkeeping periods apply to these records; keep backups for the period your broker specifies.

## Before real use
Set the IT/TE/IE transit days for your port (Setup → Settings), use strong unique passwords, and have your broker confirm the rules encoded in the app.
