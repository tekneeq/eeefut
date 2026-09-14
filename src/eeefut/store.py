"""Persistent game store: drives, plays, participants, and per-player box lines.

Every completed (or in-progress) game is normalised from an ESPN summary payload into a
compact JSON record under ``<cache>/games/<season>/<event_id>.json``. Team/player metrics
in :mod:`eeefut.teams` are aggregated from these records.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from eeefut.cache import cache_root, read_json, write_json

PASS_DEPTH_RE = re.compile(r"\bpass(?:es)?(?: incomplete)? (deep|short) (left|right|middle)\b", re.I)
RUSH_DIR_RE = re.compile(r"\b(left|right) (end|tackle|guard)\b|\bup the middle\b|\bscrambles\b", re.I)
SACK_RE = re.compile(r"\bsacked\b", re.I)
PASS_TEXT_RE = re.compile(r"\bpass\b", re.I)
SPIKE_KNEEL_RE = re.compile(r"\bspiked\b|\bkneels\b", re.I)

SPECIAL_TYPES = ("kickoff", "punt", "field goal", "extra point", "two-point", "two point", "onside")
ADMIN_TYPES = ("timeout", "end period", "end of half", "end of game", "two-minute", "coin toss", "official")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def classify_play(type_text: str, text: str) -> dict[str, Any]:
    """Tag a play: kind (pass/rush/special/penalty/admin/other), depth, direction, sack, turnover."""
    lt = (type_text or "").lower()
    kind = "other"
    if any(k in lt for k in ADMIN_TYPES):
        kind = "admin"
    elif lt.startswith("penalty"):
        kind = "penalty"
    elif any(k in lt for k in SPECIAL_TYPES) and "return" not in lt:
        kind = "special"
    elif "sack" in lt or SACK_RE.search(text or ""):
        kind = "pass"
    elif "pass" in lt or "interception" in lt:
        kind = "pass"
    elif "rush" in lt or "run" in lt:
        kind = "rush"
    elif "fumble" in lt:
        kind = "pass" if PASS_TEXT_RE.search(text or "") else "rush"
    elif PASS_TEXT_RE.search(text or ""):
        kind = "pass"
    elif RUSH_DIR_RE.search(text or ""):
        kind = "rush"
    if kind == "rush" and SPIKE_KNEEL_RE.search(text or ""):
        kind = "other"

    depth = direction = None
    if kind == "pass":
        m = PASS_DEPTH_RE.search(text or "")
        if m:
            depth = m.group(1).lower()
            direction = m.group(2).lower()
    elif kind == "rush":
        m = RUSH_DIR_RE.search(text or "")
        if m:
            if m.group(1):
                direction = m.group(1).lower()
            else:
                direction = "middle"
    sack = kind == "pass" and ("sack" in lt or bool(SACK_RE.search(text or "")))
    interception = "interception" in lt or bool(re.search(r"\bINTERCEPTED\b", text or ""))
    fumble_lost = "fumble recovery (opponent)" in lt or "opp fumble recovery" in lt
    return {
        "kind": kind,
        "depth": depth,
        "direction": direction,
        "sack": sack,
        "turnover": interception or fumble_lost,
        "interception": interception,
        "fumble_lost": fumble_lost,
        "touchdown": "touchdown" in lt,
    }


def _participants(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for part in raw or []:
        ath = part.get("athlete") or {}
        pos = ath.get("position") or {}
        out.append(
            {
                "id": str(ath.get("id") or ""),
                "name": str(ath.get("shortName") or ath.get("displayName") or ""),
                "position": str(pos.get("abbreviation") or "") if isinstance(pos, dict) else str(pos or ""),
                "team": str((ath.get("team") or {}).get("abbreviation") or ""),
                "role": str(part.get("type") or ""),
            }
        )
    return out


def _play(raw: dict[str, Any], side_of: dict[str, str]) -> dict[str, Any]:
    ptype = str((raw.get("type") or {}).get("text") or "")
    text = str(raw.get("text") or "")
    start = raw.get("start") or {}
    end = raw.get("end") or {}
    tags = classify_play(ptype, text)
    yards = _int(raw.get("statYardage"))
    play = {
        "id": str(raw.get("id") or ""),
        "seq": _int(raw.get("sequenceNumber")),
        "type": ptype,
        "text": text,
        "period": _int((raw.get("period") or {}).get("number")),
        "clock": str((raw.get("clock") or {}).get("displayValue") or ""),
        "down": _int(start.get("down")) or None,
        "distance": _int(start.get("distance")),
        "yard_line": start.get("yardLine"),
        "yards_to_goal": start.get("yardsToEndzone"),
        "end_yard_line": end.get("yardLine"),
        "down_distance": str(start.get("downDistanceText") or ""),
        "yards": yards,
        "scoring": bool(raw.get("scoringPlay", False)),
        "score_value": _int(raw.get("scoreValue")),
        "home_score": _int(raw.get("homeScore")),
        "away_score": _int(raw.get("awayScore")),
        "offense": side_of.get(str((start.get("team") or {}).get("id") or "")),
        "participants": _participants(raw.get("participants")),
        **tags,
    }
    play["explosive"] = play["kind"] in ("pass", "rush") and yards >= 20
    return play


def _drive(raw: dict[str, Any], side_of: dict[str, str]) -> dict[str, Any]:
    team = raw.get("team") or {}
    start = raw.get("start") or {}
    end = raw.get("end") or {}
    plays = [_play(p, side_of) for p in raw.get("plays") or []]
    side = side_of.get(str(team.get("id") or ""))
    result = str(raw.get("result") or raw.get("shortDisplayResult") or "").upper()
    points = 0
    if plays:
        first, last = plays[0], plays[-1]
        if side == "home":
            points = max(0, last["home_score"] - first["home_score"])
        elif side == "away":
            points = max(0, last["away_score"] - first["away_score"])
    return {
        "id": str(raw.get("id") or ""),
        "team": str(team.get("abbreviation") or ""),
        "side": side,
        "description": str(raw.get("description") or ""),
        "result": result,
        "is_score": bool(raw.get("isScore", False)),
        "points": points,
        "yards": _int(raw.get("yards")),
        "play_count": _int(raw.get("offensivePlays")),
        "time_elapsed": str((raw.get("timeElapsed") or {}).get("displayValue") or ""),
        "start_period": _int((start.get("period") or {}).get("number")),
        "start_clock": str((start.get("clock") or {}).get("displayValue") or ""),
        "start_yard_line": start.get("yardLine"),
        "start_text": str(start.get("text") or ""),
        "end_period": _int((end.get("period") or {}).get("number")),
        "end_clock": str((end.get("clock") or {}).get("displayValue") or ""),
        "end_yard_line": end.get("yardLine"),
        "end_text": str(end.get("text") or ""),
        "plays": plays,
    }


def _players(boxscore: dict[str, Any], side_of: dict[str, str]) -> list[dict[str, Any]]:
    """Flatten boxscore.players into one entry per athlete with all their stat lines."""
    by_id: dict[str, dict[str, Any]] = {}
    for team_block in boxscore.get("players") or []:
        team = team_block.get("team") or {}
        abbr = str(team.get("abbreviation") or "")
        side = side_of.get(str(team.get("id") or ""))
        for cat in team_block.get("statistics") or []:
            cat_name = str(cat.get("name") or "")
            labels = [str(x) for x in cat.get("labels") or []]
            for entry in cat.get("athletes") or []:
                ath = entry.get("athlete") or {}
                pid = str(ath.get("id") or "")
                if not pid:
                    continue
                player = by_id.setdefault(
                    pid,
                    {
                        "id": pid,
                        "name": str(ath.get("displayName") or ""),
                        "short_name": str(ath.get("shortName") or ""),
                        "jersey": str(ath.get("jersey") or ""),
                        "team": abbr,
                        "side": side,
                        "position": "",
                        "lines": {},
                    },
                )
                stats = [str(x) for x in entry.get("stats") or []]
                player["lines"][cat_name] = dict(zip(labels, stats))
    return list(by_id.values())


def build_game_record(summary: dict[str, Any]) -> dict[str, Any]:
    """Normalise an ESPN summary payload into the eeefut game record."""
    header = summary.get("header") or {}
    comp = (header.get("competitions") or [{}])[0]
    status = comp.get("status") or {}
    stype = status.get("type") or {}
    season = header.get("season") or {}
    competitors = comp.get("competitors") or []

    def team_block(c: dict[str, Any]) -> dict[str, Any]:
        t = c.get("team") or {}
        rec = ""
        for r in c.get("record") or []:
            if r.get("type") == "total":
                rec = str(r.get("summary") or r.get("displayValue") or "")
                break
        return {
            "id": str(t.get("id") or ""),
            "abbr": str(t.get("abbreviation") or ""),
            "name": str(t.get("nickname") or t.get("shortDisplayName") or t.get("name") or ""),
            "full_name": str(t.get("displayName") or ""),
            "location": str(t.get("location") or ""),
            "color": str(t.get("color") or "444444"),
            "logo": str(((t.get("logos") or [{}])[0]).get("href") or t.get("logo") or ""),
            "score": _int(c.get("score")),
            "record": rec,
            "winner": bool(c.get("winner", False)),
            "linescores": [_int(ls.get("displayValue", ls.get("value"))) for ls in c.get("linescores") or []],
        }

    home_raw = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
    away_raw = next((c for c in competitors if c.get("homeAway") == "away"), competitors[-1] if competitors else {})
    home, away = team_block(home_raw), team_block(away_raw)
    side_of = {home["id"]: "home", away["id"]: "away"}

    boxscore = summary.get("boxscore") or {}
    team_stats: dict[str, dict[str, str]] = {"home": {}, "away": {}}
    for tb in boxscore.get("teams") or []:
        side = side_of.get(str((tb.get("team") or {}).get("id") or ""))
        if side is None:
            side = "home" if tb.get("homeAway") == "home" else "away" if tb.get("homeAway") == "away" else None
        if side is None:
            continue
        for stat in tb.get("statistics") or []:
            name = str(stat.get("name") or "")
            if name and name not in team_stats[side]:
                team_stats[side][name] = str(stat.get("displayValue") or "")

    drives_raw = summary.get("drives") or {}
    drives = [_drive(d, side_of) for d in drives_raw.get("previous") or []]
    current = drives_raw.get("current")
    if current and not any(d["id"] == str(current.get("id") or "") for d in drives):
        drives.append(_drive(current, side_of))

    scoring = []
    for sp in summary.get("scoringPlays") or []:
        scoring.append(
            {
                "team": str((sp.get("team") or {}).get("abbreviation") or ""),
                "period": _int((sp.get("period") or {}).get("number")),
                "clock": str((sp.get("clock") or {}).get("displayValue") or ""),
                "type": str((sp.get("scoringType") or {}).get("abbreviation") or (sp.get("type") or {}).get("text") or ""),
                "text": str(sp.get("text") or ""),
                "home_score": _int(sp.get("homeScore")),
                "away_score": _int(sp.get("awayScore")),
            }
        )

    state = str(stype.get("state") or "post")
    return {
        "id": str(header.get("id") or comp.get("id") or ""),
        "season": _int(season.get("year")),
        "season_type": _int(season.get("type"), 2),
        "week": _int(header.get("week")),
        "date": str(comp.get("date") or ""),
        "state": state,
        "completed": bool(stype.get("completed", state == "post")),
        "status_detail": str(stype.get("shortDetail") or ""),
        "neutral_site": bool(comp.get("neutralSite", False)),
        "venue": str(((summary.get("gameInfo") or {}).get("venue") or {}).get("fullName") or ""),
        "home": home,
        "away": away,
        "team_stats": team_stats,
        "players": _players(boxscore, side_of),
        "drives": drives,
        "scoring_plays": scoring,
        "stored_at": int(time.time()),
    }


class GameStore:
    """Disk-backed collection of game records (one JSON file per game)."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    @property
    def root(self) -> Path:
        root = self._root or (cache_root() / "games")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _season_dir(self, season: int) -> Path:
        path = self.root / str(int(season))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def path_for(self, season: int, game_id: str) -> Path:
        return self._season_dir(season) / f"{game_id}.json"

    def save(self, record: dict[str, Any]) -> Path:
        path = self.path_for(record["season"], record["id"])
        write_json(path, record)
        with self._lock:
            self._cache[str(path)] = (path.stat().st_mtime, record)
        return path

    def save_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        record = build_game_record(summary)
        if record["id"] and record["season"]:
            self.save(record)
        return record

    def _load_path(self, path: Path) -> dict[str, Any] | None:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        with self._lock:
            hit = self._cache.get(str(path))
            if hit and hit[0] == mtime:
                return hit[1]
        data = read_json(path)
        if data is None:
            return None
        with self._lock:
            self._cache[str(path)] = (mtime, data)
        return data

    def load(self, season: int, game_id: str) -> dict[str, Any] | None:
        return self._load_path(self.path_for(season, game_id))

    def find(self, game_id: str) -> dict[str, Any] | None:
        for season in self.seasons():
            rec = self.load(season, game_id)
            if rec:
                return rec
        return None

    def seasons(self) -> list[int]:
        out = []
        for child in self.root.iterdir():
            if child.is_dir() and child.name.isdigit():
                out.append(int(child.name))
        return sorted(out)

    def state_of(self, season: int, game_id: str) -> str | None:
        rec = self.load(season, game_id)
        return rec["state"] if rec else None

    def games(self, season: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        season_dir = self.root / str(int(season))
        if not season_dir.is_dir():
            return out
        for path in sorted(season_dir.glob("*.json")):
            if path.name.startswith("_"):
                continue
            rec = self._load_path(path)
            if rec:
                out.append(rec)
        out.sort(key=lambda g: (g.get("week", 0), g.get("date", ""), g.get("id", "")))
        return out

    def game_ids(self, season: int) -> set[str]:
        season_dir = self.root / str(int(season))
        if not season_dir.is_dir():
            return set()
        return {p.stem for p in season_dir.glob("*.json") if not p.name.startswith("_")}

    def count(self, season: int) -> int:
        return len(self.game_ids(season))

    # ---- rosters (positions) -------------------------------------------------------

    def roster_path(self, season: int) -> Path:
        return self._season_dir(season) / "_rosters.json"

    def load_rosters(self, season: int) -> dict[str, dict[str, Any]]:
        data = read_json(self.roster_path(season))
        return data.get("players", {}) if isinstance(data, dict) else {}

    def save_rosters(self, season: int, players: dict[str, dict[str, Any]], teams: Iterable[dict[str, Any]]) -> None:
        write_json(
            self.roster_path(season),
            {"season": season, "players": players, "teams": list(teams), "stored_at": int(time.time())},
        )

    def load_teams(self, season: int) -> list[dict[str, Any]]:
        data = read_json(self.roster_path(season))
        return list(data.get("teams", [])) if isinstance(data, dict) else []
