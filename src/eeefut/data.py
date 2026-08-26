"""Warm and load NFL game corpora from nflverse."""

from __future__ import annotations

import csv
import gzip
import io
import re
import urllib.error
import urllib.request
from typing import Iterable

from eeefut.cache import cache_path, read_json, write_json
from eeefut.models import GAME_LENGTH, Game, ScoreEvent
from eeefut.timeline import build_timelines

GAMES_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/stats_team/"
    "stats_team_week_{year}.csv.gz"
)
USER_AGENT = "eeefut/0.1 (+https://github.com/tekneeq/eeefut)"

_SEASON_RE = re.compile(r"^NFL:(\d{4})$", re.I)

TEAM_NAMES = {
    "ARI": "Arizona",
    "ATL": "Atlanta",
    "BAL": "Baltimore",
    "BUF": "Buffalo",
    "CAR": "Carolina",
    "CHI": "Chicago",
    "CIN": "Cincinnati",
    "CLE": "Cleveland",
    "DAL": "Dallas",
    "DEN": "Denver",
    "DET": "Detroit",
    "GB": "Green Bay",
    "HOU": "Houston",
    "IND": "Indianapolis",
    "JAX": "Jacksonville",
    "KC": "Kansas City",
    "LA": "LA Rams",
    "LAC": "LA Chargers",
    "LV": "Las Vegas",
    "MIA": "Miami",
    "MIN": "Minnesota",
    "NE": "New England",
    "NO": "New Orleans",
    "NYG": "NY Giants",
    "NYJ": "NY Jets",
    "PHI": "Philadelphia",
    "PIT": "Pittsburgh",
    "SEA": "Seattle",
    "SF": "San Francisco",
    "TB": "Tampa Bay",
    "TEN": "Tennessee",
    "WAS": "Washington",
}


def parse_warm_spec(spec: str) -> tuple[str, int]:
    """Return (label, year). NFL:2025 → ('NFL:2025', 2025)."""
    m = _SEASON_RE.match(spec.strip())
    if not m:
        raise ValueError(f"Unsupported warm spec {spec!r}; expected NFL:YYYY")
    year = int(m.group(1))
    return f"NFL:{year}", year


def previous_season_label(label: str) -> str:
    m = _SEASON_RE.match(label)
    if not m:
        raise ValueError(label)
    year = int(m.group(1)) - 1
    return f"NFL:{year}"


def team_name(abbr: str) -> str:
    key = (abbr or "").strip().upper()
    return TEAM_NAMES.get(key, abbr.strip())


def _fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Failed to download {url}: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to download {url}: {exc.reason}") from exc


def _fetch_text(url: str) -> str:
    raw = _fetch_bytes(url)
    if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def _safe_int(value: str | None, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def _stats_index(text: str) -> dict[tuple[str, str], dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    out: dict[tuple[str, str], dict[str, str]] = {}
    for row in reader:
        gid = (row.get("game_id") or "").strip()
        team = (row.get("team") or "").strip().upper()
        if gid and team:
            out[(gid, team)] = row
    return out


def _box(row: dict[str, str] | None) -> tuple[int, int]:
    if not row:
        return 0, 0
    yards = _safe_int(row.get("passing_yards")) + _safe_int(row.get("rushing_yards"))
    fd = _safe_int(row.get("passing_first_downs")) + _safe_int(row.get("rushing_first_downs"))
    return max(0, yards), max(0, fd)


def matches_from_csv(games_text: str, stats_text: str, season_label: str) -> list[Game]:
    _, year = parse_warm_spec(season_label)
    stats = _stats_index(stats_text)
    reader = csv.DictReader(io.StringIO(games_text))
    out: list[Game] = []
    for row in reader:
        if _safe_int(row.get("season"), default=-1) != year:
            continue
        home_abbr = (row.get("home_team") or "").strip().upper()
        away_abbr = (row.get("away_team") or "").strip().upper()
        if not home_abbr or not away_abbr:
            continue
        if row.get("home_score") in (None, "") or row.get("away_score") in (None, ""):
            continue
        gid = (row.get("game_id") or "").strip()
        if not gid:
            continue
        home_ft = _safe_int(row.get("home_score"))
        away_ft = _safe_int(row.get("away_score"))
        hy, hfd = _box(stats.get((gid, home_abbr)))
        ay, afd = _box(stats.get((gid, away_abbr)))
        if hy == 0 and hfd == 0 and ay == 0 and afd == 0:
            # Estimate a box if team-week stats are missing
            hy = 240 + home_ft * 6
            ay = 240 + away_ft * 6
            hfd = 14 + home_ft // 7
            afd = 14 + away_ft // 7
        match_id = f"{season_label}:{gid}"
        tl = build_timelines(match_id, home_ft, away_ft, hy, ay, hfd, afd)
        out.append(
            Game(
                match_id=match_id,
                season=season_label,
                date=(row.get("gameday") or "").strip(),
                week=_safe_int(row.get("week")),
                game_type=(row.get("game_type") or "REG").strip() or "REG",
                home=team_name(home_abbr),
                away=team_name(away_abbr),
                home_score_ft=home_ft,
                away_score_ft=away_ft,
                home_yards_ft=hy,
                away_yards_ft=ay,
                home_fd_ft=hfd,
                away_fd_ft=afd,
                scores=tl["scores"],
                home_yards_by_min=tl["home_yards_by_min"],
                away_yards_by_min=tl["away_yards_by_min"],
                home_fd_by_min=tl["home_fd_by_min"],
                away_fd_by_min=tl["away_fd_by_min"],
            )
        )
    out.sort(key=lambda g: (g.date, g.week, g.match_id))
    return out


def inject_chiefs_preset(matches: list[Game], season_label: str) -> list[Game]:
    """
    Ensure a Chiefs 28' demo fixture exists:
    14'/28' · 245/14 vs 168/9
    """
    preset_id = f"{season_label}:preset:Chiefs:28"
    matches = [m for m in matches if m.match_id != preset_id]
    series_len = GAME_LENGTH + 1

    def ramp(targets: dict[int, tuple[int, int]], length: int = series_len) -> tuple[list[int], list[int]]:
        yards = [0] * length
        fd = [0] * length
        y = f = 0
        checkpoints = sorted(targets)
        last = 0
        for minute in checkpoints:
            ty, tf = targets[minute]
            span = max(1, minute - last)
            for m in range(last + 1, minute + 1):
                progress = (m - last) / span
                yards[m] = y + int(round((ty - y) * progress))
                fd[m] = f + int(round((tf - f) * progress))
            yards[minute], fd[minute] = ty, tf
            y, f = ty, tf
            last = minute
        for m in range(last + 1, GAME_LENGTH + 1):
            yards[m], fd[m] = y, f
        return yards, fd

    hy, hfd = ramp({14: (120, 7), 28: (245, 14), 60: (380, 22)})
    ay, afd = ramp({14: (90, 5), 28: (168, 9), 60: (290, 16)})
    scores = [
        ScoreEvent(minute=14, team="home", points=7),
        ScoreEvent(minute=28, team="home", points=7),
    ]
    preset = Game(
        match_id=preset_id,
        season=season_label,
        date="2026-01-01",
        week=18,
        game_type="REG",
        home="Kansas City",
        away="Demo Bills",
        home_score_ft=24,
        away_score_ft=10,
        home_yards_ft=380,
        away_yards_ft=290,
        home_fd_ft=22,
        away_fd_ft=16,
        scores=scores,
        home_yards_by_min=hy,
        away_yards_by_min=ay,
        home_fd_by_min=hfd,
        away_fd_by_min=afd,
    )
    return [preset, *matches]


def season_cache_file(season_label: str):
    safe = season_label.replace(":", "_")
    return cache_path("seasons", f"{safe}.json")


def save_season(season_label: str, matches: Iterable[Game]) -> None:
    payload = {
        "season": season_label,
        "matches": [m.to_dict() for m in matches],
    }
    write_json(season_cache_file(season_label), payload)


def load_season(season_label: str) -> list[Game]:
    data = read_json(season_cache_file(season_label))
    if not data:
        return []
    return [Game.from_dict(m) for m in data.get("matches", [])]


def warm(spec: str, *, include_previous: bool = True) -> dict[str, int]:
    """Download and cache season (and previous season for Similar)."""
    label, year = parse_warm_spec(spec)
    counts: dict[str, int] = {}

    games_text = _fetch_text(GAMES_URL)
    years = [year]
    if include_previous:
        years.append(year - 1)

    for y in years:
        lab = f"NFL:{y}"
        stats_text = _fetch_text(STATS_URL.format(year=y))
        matches = matches_from_csv(games_text, stats_text, lab)
        if y == year:
            matches = inject_chiefs_preset(matches, lab)
        save_season(lab, matches)
        counts[lab] = len(matches)

    return counts


def list_cached_seasons() -> list[str]:
    from eeefut.cache import cache_root

    seasons_dir = cache_root() / "seasons"
    if not seasons_dir.is_dir():
        return []
    out: list[str] = []
    for path in sorted(seasons_dir.glob("*.json")):
        out.append(path.stem.replace("_", ":", 1))
    return out
