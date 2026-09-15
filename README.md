# eeefut

NFL **Matches + Similar** dashboard with a dark Revenant look.

Freeze an in-play snapshot (cut minute / game clock, scoring times, yards / first downs) and rank last-season lookalikes. History is cached under `~/.eeefut/cache` (or `./data/cache` on EC2).

## Quick start

```bash
uv sync --extra dev
uv run eeefut --dashboard --port 8081 --warm NFL:2025
```

Open http://127.0.0.1:8081 — use **Chiefs 28′** for the demo preset (`14'/28' · 245/14 vs 168/9`).

The **Live** tab (`/#live`) shows today's slate as chiclets: score, drive on a football field, last play, box stats, leaders, and a **Similar N′** button that freezes the live snapshot into lookalikes. Data comes from ESPN's public scoreboard (`/api/live`, 20s cache; the box needs outbound HTTPS to `site.api.espn.com`).

The **Teams** tab (`/#teams`, `/#teams/KC`) opens with a **rank-by-week graph**: X is Week 1…n, Y is league rank 1 (top) through 32, and the team name sits at whatever rank it held after that week (hover a name to trace it; click to open the team). The grid below is ordered by **power number** — how many points better (+) or worse (−) than an average team each side is on a neutral field. Every chiclet shows the rank, power, the change since last week (▲/▼ points and rank places), and a season sparkline; the toolbar re-orders by Record or by biggest Movers. Opening a team shows the weekly power history as a chart (preseason baseline, then a point per week with the rank at each step) plus a week-by-week table. The number is re-rated after each week's games from both the result and how the game was played: the rating update uses a blended margin of 70 % score and 30 % box-score margin (yardage edge at 15 yards/point, ±4 points per turnover), so a fluky win barely moves you and a dominant one moves you more. It also aggregates every stored game into offense / defense metrics with league ranks (yards for and allowed, rush/pass splits, points per drive, 3-and-outs, turnovers…), tendencies (run/pass mix, deep vs short shots, rush/pass direction), top players by category, and a game log that drills into every drive and play. Games are stored under `<cache>/games/<season>/<event_id>.json` with drives → plays (tagged pass/rush, depth, direction, sack, turnover, explosive, participants when ESPN provides them) and each player's box lines; `_rosters.json` holds positions. Finals are persisted automatically by the Live poller; use **Sync games** (or `--ingest`) to backfill a season.

The **WinProb** tab (`/#winprob`) runs a margin-of-victory Elo (K=20, ~1.9 pt home field, ⅓ regression between seasons) over the full nflverse schedule history and shows, for every game of the current season, each team's pre-game win probability, the model's expected margin ("KC by 4.5"), and the market line for comparison. A **right-by-week bar chart** sits above the games: X is the week, each bar is how many picks were correct out of the games that finished (labelled `10 / 15`). Click a bar to jump to that week. Below the games: the season and weekly pick record (with Brier score and how Vegas favourites did), a calibration panel of favourites' actual record per probability bucket (50–55, 55–60, 60–65, 65–70, >70 — combined, with the home/away split underneath), per-team records when favoured, and the power ratings table (power, weekly delta, rank movement, sparkline — click a row to open the team). The same ratings drive both tabs, so the win probabilities and the power numbers always agree. Probabilities are computed as of kickoff (ratings replayed chronologically), and scores the schedule file hasn't posted yet are filled from the game store.

## CLI

```bash
# Warm current + previous season into ~/.eeefut/cache
uv run eeefut --warm NFL:2025

# Backfill drives / plays / players for completed games (skips ones already stored)
uv run eeefut --ingest NFL:2026

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

Nginx on this box: **:80 / :443 → 127.0.0.1:8082** with `server_name eeefut.com www.eeefut.com`.

```bash
cd ~/eeefut
./scripts/install-nginx-80.sh          # HTTP + ACME webroot
./scripts/enable-https.sh              # Let's Encrypt, then HTTPS
# CERTBOT_EMAIL=you@example.com ./scripts/enable-https.sh --www
```

Browsers type `eeefut.com` as **https://** first. Until `:443` has a cert, the domain looks “down” while `http://<public-ip>/` still works.

Checklist if the domain fails in a browser:

1. Route53 **A** for `eeefut.com` = this instance’s **public** IPv4 (`curl -4 https://checkip.amazonaws.com` on the box). `www` needs an A or CNAME too.
2. Security group inbound **80** and **443**.
3. Run `enable-https.sh` so nginx listens on 443.

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
