# eeefut

NFL **Matches + Similar** dashboard with a dark Revenant look.

Freeze an in-play snapshot (cut minute / game clock, scoring times, yards / first downs) and rank last-season lookalikes. History is cached under `~/.eeefut/cache` (or `./data/cache` on EC2).

## Quick start

```bash
uv sync --extra dev
uv run eeefut --dashboard --port 8081 --warm NFL:2025
```

Open http://127.0.0.1:8081 — use **Chiefs 28′** for the demo preset (`14'/28' · 245/14 vs 168/9`).

## CLI

```bash
# Warm current + previous season into ~/.eeefut/cache
uv run eeefut --warm NFL:2025

# Similar lookalikes for a game at elapsed minute 28 (Q2 2:00)
uv run eeefut --similar Chiefs --minute 28

uv run pytest
```

## Notes

- Season schedules and box-score yards / first downs are loaded from [nflverse](https://github.com/nflverse/nflverse-data) (`schedules/games.csv` + `stats_team_week_YYYY.csv.gz`).
- Minute-level yard ramps are reconstructed from full-game box scores (deterministic per game). Scoring plays are placed from the final score.
- Cut minutes are elapsed game clock 1–60 (four 15-minute quarters). Minute 28 is Q2 2:00.
- The Chiefs 28′ fixture is injected as an explicit demo snapshot for Similar.
- Docker binds `0.0.0.0:8082` and mounts `./data/cache` so warm data survives rebuilds.

## Deploy on EC2 (same pattern as `tekneeq/julia` and `tekneeq/eeesoc`)

eeefut, eeesoc, and julia each have **their own instance**. This repo only SSHes into the eeefut box.

Flow on every push/merge to `main`:

1. GitHub Actions workflow `.github/workflows/deploy-ec2.yml` SSHes into the box
2. `git pull --ff-only origin main`
3. `./deploy.sh` → `./restart.sh` (docker rebuild + `docker run --restart unless-stopped`)
4. Container entrypoint warms `NFL:2025` cache (no-op when already warm) and serves `:8082`

### One-time bootstrap on the EC2 host

```bash
git clone https://github.com/tekneeq/eeefut.git ~/eeefut
cd ~/eeefut
chmod +x deploy.sh restart.sh scripts/docker-entrypoint.sh
./deploy.sh
```

Nginx on this box is julia-style: **:80 → 127.0.0.1:8082**.

```bash
cd ~/eeefut
./scripts/install-nginx-80.sh
```

The installer comments Amazon Linux’s stock `server { listen 80; }` out of `/etc/nginx/nginx.conf` and starts nginx if the unit is inactive. Open the security group inbound for **80**.

### Auto-deploy on push

Pushes to `main` trigger `.github/workflows/deploy-ec2.yml`, which SSHes in and runs `./deploy.sh`.

One-time GitHub setup (repo → **Settings → Secrets and variables → Actions**):

| Secret | Example | Notes |
| --- | --- | --- |
| `EC2_HOST` | `54.91.65.71` | This instance only (not julia / eeesoc) |
| `EC2_USER` | `ec2-user` | |
| `EC2_SSH_PRIVATE_KEY` | full `.pem` contents | Include `BEGIN`/`END` lines |
| `EC2_SSH_PORT` | `22` | Optional |
| `EC2_APP_DIR` | `/home/ec2-user/eeefut` | Optional |

Manual redeploy / diagnostics: Actions → **Deploy to EC2** / **EC2 status** → Run workflow.

Local-on-box redeploy anytime:

```bash
cd ~/eeefut && ./deploy.sh
```
