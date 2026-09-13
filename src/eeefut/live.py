"""Live NFL scoreboard feed (ESPN public API) normalised for the Live tab."""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from eeefut.models import GAME_LENGTH, GameSnapshot, clock_label

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event_id}"
USER_AGENT = "eeefut/0.1 (+https://github.com/tekneeq/eeefut)"

SCOREBOARD_TTL = 20.0  # seconds
SUMMARY_TTL_LIVE = 20.0
SUMMARY_TTL_FINAL = 15 * 60.0
FORCE_MIN_INTERVAL = 5.0

# boxscore statistic name -> compact key shown on a chiclet
TEAM_STAT_KEYS = {
    "totalYards": "total_yards",
    "firstDowns": "first_downs",
    "netPassingYards": "passing_yards",
    "rushingYards": "rushing_yards",
    "turnovers": "turnovers",
    "possessionTime": "possession",
    "thirdDownEff": "third_down",
    "totalPenaltiesYards": "penalties",
    "yardsPerPlay": "yards_per_play",
    "totalOffensivePlays": "plays",
    "sacksYardsLost": "sacks",
    "redZoneAttempts": "red_zone",
}

LEADER_KEYS = {"passingYards": "passing", "rushingYards": "rushing", "receivingYards": "receiving"}

FetchJson = Callable[[str], dict[str, Any]]


def fetch_json(url: str, timeout: float = 15.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Live feed HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Live feed unreachable: {exc.reason}") from exc


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def elapsed_minute(period: int, clock_seconds: float | None) -> int:
    """ESPN period + seconds remaining -> elapsed game minute 1..60 (OT clamps to 60)."""
    period = max(1, _int(period, 1))
    if period > 4:
        return GAME_LENGTH
    remaining = 0.0 if clock_seconds is None else max(0.0, min(900.0, float(clock_seconds)))
    elapsed = (period - 1) * 15 + (15 - remaining / 60.0)
    # Minute N is "the Nth minute in progress": Q2 2:00 -> 28, Q4 14:59 -> 46.
    return max(1, min(GAME_LENGTH, math.ceil(elapsed - 1e-9)))


def _period_label(status: dict[str, Any], state: str) -> str:
    stype = status.get("type") or {}
    name = str(stype.get("name") or "")
    period = _int(status.get("period"))
    clock = str(status.get("displayClock") or "")
    if state == "post":
        return "Final/OT" if period > 4 else "Final"
    if state != "in":
        return str(stype.get("shortDetail") or "Scheduled")
    if "HALFTIME" in name:
        return "Halftime"
    if "END_PERIOD" in name:
        return f"End Q{period}" if period <= 4 else "End OT"
    if period > 4:
        return f"OT {clock}".strip()
    return f"Q{period} {clock}".strip()


def _team_side(team_id: str | None, home_id: str, away_id: str) -> str | None:
    if not team_id:
        return None
    if str(team_id) == str(home_id):
        return "home"
    if str(team_id) == str(away_id):
        return "away"
    return None


def _leaders(raw: list[dict[str, Any]] | None, team_id: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for cat in raw or []:
        key = LEADER_KEYS.get(str(cat.get("name") or ""))
        if not key:
            continue
        for leader in cat.get("leaders") or []:
            athlete = leader.get("athlete") or {}
            lid = str((leader.get("team") or {}).get("id") or (athlete.get("team") or {}).get("id") or "")
            if lid and lid != str(team_id):
                continue
            pos = athlete.get("position") or {}
            out[key] = {
                "name": str(athlete.get("shortName") or athlete.get("displayName") or ""),
                "position": str(pos.get("abbreviation") or "") if isinstance(pos, dict) else str(pos),
                "line": str(leader.get("displayValue") or ""),
            }
            break
    return out


def _competitor(comp: dict[str, Any], competition_leaders: list[dict[str, Any]] | None) -> dict[str, Any]:
    team = comp.get("team") or {}
    record = ""
    for rec in comp.get("records") or []:
        if rec.get("type") == "total" or rec.get("name") == "overall":
            record = str(rec.get("summary") or "")
            break
    linescores = [_int(ls.get("value", ls.get("displayValue"))) for ls in comp.get("linescores") or []]
    return {
        "id": str(team.get("id") or ""),
        "abbr": str(team.get("abbreviation") or ""),
        "name": str(team.get("shortDisplayName") or team.get("name") or ""),
        "full_name": str(team.get("displayName") or ""),
        "location": str(team.get("location") or ""),
        "color": str(team.get("color") or "444444"),
        "alt_color": str(team.get("alternateColor") or "aaaaaa"),
        "logo": str(team.get("logo") or ""),
        "score": _int(comp.get("score")),
        "record": record,
        "linescores": linescores,
        "winner": bool(comp.get("winner", False)),
        "leaders": _leaders(comp.get("leaders") or competition_leaders, str(team.get("id") or "")),
        "stats": {},
    }


def _situation(raw: dict[str, Any] | None, home_id: str, away_id: str) -> dict[str, Any] | None:
    if not raw:
        return None
    last = raw.get("lastPlay") or {}
    prob = last.get("probability") or {}
    last_play = None
    if last:
        last_play = {
            "text": str(last.get("text") or ""),
            "type": str((last.get("type") or {}).get("text") or ""),
            "team": _team_side((last.get("team") or {}).get("id"), home_id, away_id),
            "score_value": _int(last.get("scoreValue")),
            "yards": _int(last.get("statYardage")),
            "start": _int((last.get("start") or {}).get("yardLine"), -1),
            "end": _int((last.get("end") or {}).get("yardLine"), -1),
            "drive": str((last.get("drive") or {}).get("description") or ""),
        }
        if last_play["start"] < 0:
            last_play["start"] = None
        if last_play["end"] < 0:
            last_play["end"] = None
    down = _int(raw.get("down"), 0) or None
    yard_line = raw.get("yardLine")
    return {
        "down": down,
        "distance": _int(raw.get("distance"), 0),
        "yard_line": None if yard_line is None else max(0, min(100, _int(yard_line))),
        "possession": _team_side(raw.get("possession"), home_id, away_id),
        "down_distance_text": str(raw.get("downDistanceText") or ""),
        "short_down_distance": str(raw.get("shortDownDistanceText") or ""),
        "possession_text": str(raw.get("possessionText") or ""),
        "is_red_zone": bool(raw.get("isRedZone", False)),
        "home_timeouts": _int(raw.get("homeTimeouts"), 3),
        "away_timeouts": _int(raw.get("awayTimeouts"), 3),
        "win_prob": (
            {"home": round(float(prob["homeWinPercentage"]), 4), "away": round(float(prob["awayWinPercentage"]), 4)}
            if prob.get("homeWinPercentage") is not None and prob.get("awayWinPercentage") is not None
            else None
        ),
        "last_play": last_play,
    }


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    comp = (event.get("competitions") or [{}])[0]
    status = comp.get("status") or event.get("status") or {}
    stype = status.get("type") or {}
    state = str(stype.get("state") or "pre")
    competitors = comp.get("competitors") or []
    home_raw = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
    away_raw = next((c for c in competitors if c.get("homeAway") == "away"), competitors[-1] if competitors else {})
    home = _competitor(home_raw, comp.get("leaders"))
    away = _competitor(away_raw, comp.get("leaders"))
    if state == "post" and not home["winner"] and not away["winner"]:
        if home["score"] > away["score"]:
            home["winner"] = True
        elif away["score"] > home["score"]:
            away["winner"] = True
    broadcasts = comp.get("broadcasts") or []
    broadcast = ""
    if broadcasts:
        names = broadcasts[0].get("names") or []
        broadcast = str(names[0]) if names else ""
    period = _int(status.get("period"))
    clock_seconds = _float(status.get("clock"))
    game = {
        "id": str(event.get("id") or comp.get("id") or ""),
        "name": str(event.get("name") or ""),
        "short_name": str(event.get("shortName") or f"{away['abbr']} @ {home['abbr']}"),
        "date": str(event.get("date") or comp.get("date") or ""),
        "state": state,
        "completed": bool(stype.get("completed", state == "post")),
        "status_detail": str(stype.get("shortDetail") or stype.get("detail") or ""),
        "period": period,
        "clock": str(status.get("displayClock") or ""),
        "period_label": _period_label(status, state),
        "venue": str((comp.get("venue") or {}).get("fullName") or ""),
        "broadcast": broadcast,
        "home": home,
        "away": away,
        "situation": _situation(comp.get("situation"), home["id"], away["id"]) if state == "in" else None,
        "scoring_minutes": [],
        "snapshot": None,
    }
    if state in ("in", "post"):
        game["minute"] = GAME_LENGTH if state == "post" else elapsed_minute(period, clock_seconds)
    else:
        game["minute"] = None
    return game


def normalize_scoreboard(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events") or []
    games = [normalize_event(e) for e in events]
    order = {"in": 0, "pre": 1, "post": 2}
    games.sort(key=lambda g: (order.get(g["state"], 3), g["date"], g["short_name"]))
    season = payload.get("season") or {}
    week = payload.get("week") or {}
    return {
        "season": _int(season.get("year")),
        "season_type": _int(season.get("type")),
        "week": _int(week.get("number")),
        "games": games,
    }


def apply_summary(game: dict[str, Any], summary: dict[str, Any]) -> None:
    """Merge team box stats, scoring minutes, and leaders from a summary payload into a game."""
    home_id, away_id = game["home"]["id"], game["away"]["id"]
    for team_box in (summary.get("boxscore") or {}).get("teams") or []:
        side = _team_side((team_box.get("team") or {}).get("id"), home_id, away_id)
        if side is None:
            side = "home" if team_box.get("homeAway") == "home" else "away" if team_box.get("homeAway") == "away" else None
        if side is None:
            continue
        stats: dict[str, Any] = {}
        for stat in team_box.get("statistics") or []:
            key = TEAM_STAT_KEYS.get(str(stat.get("name") or ""))
            if key and key not in stats:
                stats[key] = str(stat.get("displayValue") or "")
        game[side]["stats"] = stats

    minutes: list[int] = []
    for play in summary.get("scoringPlays") or []:
        period = _int((play.get("period") or {}).get("number"), 1)
        clock = _float((play.get("clock") or {}).get("value"))
        minutes.append(elapsed_minute(period, clock))
    game["scoring_minutes"] = sorted(minutes)

    for team_leaders in summary.get("leaders") or []:
        side = _team_side((team_leaders.get("team") or {}).get("id"), home_id, away_id)
        if side is None:
            continue
        merged = _leaders(team_leaders.get("leaders"), game[side]["id"])
        if merged:
            game[side]["leaders"] = {**game[side]["leaders"], **merged}

    if game["situation"] and game["situation"].get("win_prob") is None:
        wp = summary.get("winprobability") or []
        if wp:
            last = wp[-1]
            hp = _float(last.get("homeWinPercentage"))
            if hp is not None:
                game["situation"]["win_prob"] = {"home": round(hp, 4), "away": round(1 - hp, 4)}


def attach_snapshot(game: dict[str, Any]) -> None:
    """Freeze an eeefut-style snapshot (minute, score, yards / first downs) from live stats."""
    minute = game.get("minute")
    if not minute:
        return
    hs, as_ = game["home"]["stats"], game["away"]["stats"]
    snap = GameSnapshot(
        minute=int(minute),
        home_score=game["home"]["score"],
        away_score=game["away"]["score"],
        home_yards=_int(hs.get("total_yards")),
        away_yards=_int(as_.get("total_yards")),
        home_fd=_int(hs.get("first_downs")),
        away_fd=_int(as_.get("first_downs")),
        score_minutes=tuple(int(m) for m in game.get("scoring_minutes") or ()),
    )
    data = snap.to_dict()
    data["label"] = snap.label()
    data["has_box"] = bool(hs.get("total_yards") or as_.get("total_yards"))
    game["snapshot"] = data
    game["clock_label"] = clock_label(int(minute))


class LiveFeed:
    """Cached scoreboard + per-game summaries; safe to call from many request threads."""

    def __init__(self, fetch: FetchJson = fetch_json, *, ttl: float = SCOREBOARD_TTL, workers: int = 8) -> None:
        self._fetch = fetch
        self._ttl = ttl
        self._workers = max(1, workers)
        self._lock = threading.Lock()
        self._payload: dict[str, Any] | None = None
        self._fetched_at = 0.0
        self._summaries: dict[str, tuple[float, dict[str, Any], str]] = {}
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _summary_for(self, game: dict[str, Any], now: float) -> dict[str, Any] | None:
        gid = game["id"]
        cached = self._summaries.get(gid)
        ttl = SUMMARY_TTL_FINAL if game["state"] == "post" else SUMMARY_TTL_LIVE
        if cached and now - cached[0] < ttl and cached[2] == game["state"]:
            return cached[1]
        try:
            summary = self._fetch(SUMMARY_URL.format(event_id=gid))
        except Exception:  # noqa: BLE001 - a missing box score must not break the board
            return cached[1] if cached else None
        self._summaries[gid] = (now, summary, game["state"])
        return summary

    def _build(self, now: float) -> dict[str, Any]:
        board = normalize_scoreboard(self._fetch(SCOREBOARD_URL))
        games = board["games"]
        with_box = [g for g in games if g["state"] in ("in", "post")]
        if with_box:
            with ThreadPoolExecutor(max_workers=min(self._workers, len(with_box))) as pool:
                summaries = list(pool.map(lambda g: self._summary_for(g, now), with_box))
            for game, summary in zip(with_box, summaries):
                if summary:
                    apply_summary(game, summary)
        for game in games:
            attach_snapshot(game)
        live_ids = {g["id"] for g in games}
        for gid in list(self._summaries):
            if gid not in live_ids:
                del self._summaries[gid]
        board["fetched_at"] = int(now)
        board["ttl"] = int(self._ttl)
        board["counts"] = {
            "live": sum(1 for g in games if g["state"] == "in"),
            "final": sum(1 for g in games if g["state"] == "post"),
            "upcoming": sum(1 for g in games if g["state"] == "pre"),
        }
        return board

    def get(self, *, force: bool = False) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            if self._payload is not None:
                age = now - self._fetched_at
                if age < (FORCE_MIN_INTERVAL if force else self._ttl):
                    return self._payload
            try:
                payload = self._build(now)
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                if self._payload is not None:
                    stale = dict(self._payload)
                    stale["stale"] = True
                    stale["error"] = self._last_error
                    return stale
                raise
            self._last_error = None
            self._payload = payload
            self._fetched_at = now
            return payload
