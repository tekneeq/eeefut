# eeefut

NFL **Matches + Similar** dashboard with a dark Revenant look.

Freeze an in-play snapshot (cut minute / game clock, scoring times, yards / first downs) and rank last-season lookalikes. History is cached under `~/.eeefut/cache`.

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
