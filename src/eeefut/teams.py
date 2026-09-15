"""Team offense / defense metrics and player totals aggregated from stored game records."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

# (key, label, higher_is_better for the offense view). Defense flips the sign unless
# the metric is a tendency (rate) where neither direction is "better".
METRICS: list[tuple[str, str, bool | None]] = [
    ("points_pg", "Points / g", True),
    ("yards_pg", "Total yds / g", True),
    ("rush_yards_pg", "Rush yds / g", True),
    ("pass_yards_pg", "Pass yds / g", True),
    ("yards_per_play", "Yds / play", True),
    ("yards_per_rush", "Yds / rush", True),
    ("yards_per_pass_att", "Yds / pass att", True),
    ("first_downs_pg", "1st downs / g", True),
    ("third_down_pct", "3rd down %", True),
    ("red_zone_pct", "Red zone TD %", True),
    ("explosive_pg", "Explosive (20+) / g", True),
    ("points_per_drive", "Pts / drive", True),
    ("td_drive_pct", "TD drive %", True),
    ("three_and_out_pct", "3-and-out %", False),
    ("turnovers_pg", "Turnovers / g", False),
    ("sacks_pg", "Sacks / g", False),
    ("plays_pg", "Plays / g", None),
    ("rush_rate", "Rush rate", None),
    ("deep_rate", "Deep shot rate", None),
    ("short_rate", "Short pass rate", None),
]

# Defense labels that read naturally as "allowed".
DEFENSE_LABELS = {
    "points_pg": "Points allowed / g",
    "yards_pg": "Yds allowed / g",
    "rush_yards_pg": "Rush yds allowed / g",
    "pass_yards_pg": "Pass yds allowed / g",
    "yards_per_play": "Yds / play allowed",
    "yards_per_rush": "Yds / rush allowed",
    "yards_per_pass_att": "Yds / pass att allowed",
    "first_downs_pg": "1st downs allowed / g",
    "third_down_pct": "3rd down % allowed",
    "red_zone_pct": "Red zone TD % allowed",
    "explosive_pg": "Explosive allowed / g",
    "points_per_drive": "Pts / drive allowed",
    "td_drive_pct": "TD drive % allowed",
    "three_and_out_pct": "3-and-outs forced %",
    "turnovers_pg": "Takeaways / g",
    "sacks_pg": "Sacks / g",
    "plays_pg": "Plays faced / g",
    "rush_rate": "Rush rate faced",
    "deep_rate": "Deep shots faced rate",
    "short_rate": "Short pass faced rate",
}

# Defense: for these keys "higher is better" from the defence's point of view
DEFENSE_HIGHER_BETTER = {"three_and_out_pct", "turnovers_pg", "sacks_pg"}

TOTAL_KEYS = [
    "games",
    "points",
    "yards",
    "rush_yards",
    "pass_yards",
    "plays",
    "rush_att",
    "pass_att",
    "completions",
    "dropbacks",
    "first_downs",
    "third_made",
    "third_att",
    "red_zone_made",
    "red_zone_att",
    "turnovers",
    "sacks",
    "explosive",
    "deep_att",
    "short_att",
    "drives",
    "scoring_drives",
    "td_drives",
    "three_and_outs",
    "drive_points",
    "rush_left",
    "rush_middle",
    "rush_right",
    "pass_left",
    "pass_middle",
    "pass_right",
]

_PAIR_RE = re.compile(r"^\s*(-?\d+)\s*[-/]\s*(-?\d+)\s*$")


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pair(value: Any) -> tuple[int, int]:
    m = _PAIR_RE.match(str(value or ""))
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2))


def _empty_totals() -> dict[str, float]:
    return {k: 0.0 for k in TOTAL_KEYS}


def _side_totals(game: dict[str, Any], side: str) -> dict[str, float]:
    """Offensive totals for one side of a game (box score + play-derived)."""
    t = _empty_totals()
    stats = (game.get("team_stats") or {}).get(side) or {}
    t["games"] = 1
    t["points"] = game[side]["score"]
    t["yards"] = _num(stats.get("totalYards"))
    t["rush_yards"] = _num(stats.get("rushingYards"))
    t["pass_yards"] = _num(stats.get("netPassingYards"))
    t["plays"] = _num(stats.get("totalOffensivePlays"))
    t["rush_att"] = _num(stats.get("rushingAttempts"))
    comp, att = _pair(stats.get("completionAttempts"))
    t["completions"], t["pass_att"] = comp, att
    t["first_downs"] = _num(stats.get("firstDowns"))
    t["third_made"], t["third_att"] = _pair(stats.get("thirdDownEff"))
    t["red_zone_made"], t["red_zone_att"] = _pair(stats.get("redZoneAttempts"))
    t["turnovers"] = _num(stats.get("turnovers"))
    sacks, _ = _pair(stats.get("sacksYardsLost"))
    t["sacks"] = sacks

    for drive in game.get("drives") or []:
        if drive.get("side") != side:
            continue
        real_plays = [p for p in drive.get("plays") or [] if p.get("kind") in ("pass", "rush")]
        if not real_plays and drive.get("result") in ("END OF HALF", "END OF GAME", ""):
            continue
        t["drives"] += 1
        t["drive_points"] += drive.get("points", 0)
        result = str(drive.get("result") or "")
        if drive.get("is_score") or result in ("TD", "FG"):
            t["scoring_drives"] += 1
        if result == "TD" or drive.get("points", 0) >= 6:
            t["td_drives"] += 1
        if result == "PUNT" and len(real_plays) <= 3:
            t["three_and_outs"] += 1
        for play in real_plays:
            if play.get("explosive"):
                t["explosive"] += 1
            if play["kind"] == "pass":
                t["dropbacks"] += 1
                if play.get("depth") == "deep":
                    t["deep_att"] += 1
                elif play.get("depth") == "short":
                    t["short_att"] += 1
                d = play.get("direction")
                if d in ("left", "middle", "right"):
                    t[f"pass_{d}"] += 1
            else:
                d = play.get("direction")
                if d in ("left", "middle", "right"):
                    t[f"rush_{d}"] += 1
    return t


def _add(into: dict[str, float], other: dict[str, float]) -> None:
    for k, v in other.items():
        into[k] = into.get(k, 0.0) + v


def _rates(t: dict[str, float]) -> dict[str, float | None]:
    g = t["games"] or 1
    pass_att = t["pass_att"] or t["dropbacks"]
    attempts = t["rush_att"] + pass_att
    depth_known = t["deep_att"] + t["short_att"]

    def div(a: float, b: float, scale: float = 1.0, digits: int = 1) -> float | None:
        return round(a / b * scale, digits) if b else None

    return {
        "points_pg": div(t["points"], g),
        "yards_pg": div(t["yards"], g),
        "rush_yards_pg": div(t["rush_yards"], g),
        "pass_yards_pg": div(t["pass_yards"], g),
        "yards_per_play": div(t["yards"], t["plays"], 1, 2),
        "yards_per_rush": div(t["rush_yards"], t["rush_att"], 1, 2),
        "yards_per_pass_att": div(t["pass_yards"], pass_att, 1, 2),
        "first_downs_pg": div(t["first_downs"], g),
        "third_down_pct": div(t["third_made"], t["third_att"], 100),
        "red_zone_pct": div(t["red_zone_made"], t["red_zone_att"], 100),
        "explosive_pg": div(t["explosive"], g),
        "points_per_drive": div(t["drive_points"], t["drives"], 1, 2),
        "td_drive_pct": div(t["td_drives"], t["drives"], 100),
        "three_and_out_pct": div(t["three_and_outs"], t["drives"], 100),
        "turnovers_pg": div(t["turnovers"], g, 1, 2),
        "sacks_pg": div(t["sacks"], g, 1, 2),
        "plays_pg": div(t["plays"], g),
        "rush_rate": div(t["rush_att"], attempts, 100),
        "deep_rate": div(t["deep_att"], depth_known, 100),
        "short_rate": div(t["short_att"], depth_known, 100),
    }


def _rank(teams: list[dict[str, Any]], view: str, key: str, higher_better: bool) -> None:
    scored = [(t[view].get(key), t) for t in teams if t[view].get(key) is not None]
    scored.sort(key=lambda x: x[0], reverse=higher_better)
    rank = 0
    prev = object()
    for i, (val, team) in enumerate(scored, start=1):
        if val != prev:
            rank = i
            prev = val
        team["ranks"][view][key] = rank


YARDS_PER_POINT = 15.0
TURNOVER_POINTS = 4.0
BOX_WEIGHT = 0.3


def _blended_ppg(points: float, yards: float, turnovers: float, games: float) -> float | None:
    """Score mixed with box-score value, per game — same blend as the Elo update."""
    if not games:
        return None
    box = yards / YARDS_PER_POINT - TURNOVER_POINTS * turnovers
    return ((1 - BOX_WEIGHT) * points + BOX_WEIGHT * box) / games


def attach_side_power(table: list[dict[str, Any]]) -> None:
    """Offense / defense power: points better (+) or worse (−) than a league-average unit.

    Offense uses points scored + yardage/turnovers; defense uses the same numbers
    allowed. Both are centered so the league mean is 0. Higher is better on defense
    too (a +4 defense is four points stingier than average).
    """
    empty = {"offense": None, "defense": None, "combined": None, "offense_rank": None, "defense_rank": None}
    played = [t for t in table if t["games"] > 0]
    for t in table:
        t["side_power"] = dict(empty)
    if not played:
        return

    off_raw: dict[int, float] = {}
    def_raw: dict[int, float] = {}
    for t in played:
        ot, dt = t["offense_totals"], t["defense_totals"]
        off_raw[id(t)] = _blended_ppg(t["points_for"], ot["yards"], ot["turnovers"], t["games"]) or 0.0
        def_raw[id(t)] = _blended_ppg(t["points_against"], dt["yards"], dt["turnovers"], t["games"]) or 0.0
    league = (sum(off_raw.values()) + sum(def_raw.values())) / (2 * len(played))

    for t in played:
        off_p = round(off_raw[id(t)] - league, 1)
        def_p = round(league - def_raw[id(t)], 1)
        t["side_power"] = {
            "offense": off_p,
            "defense": def_p,
            "combined": round(off_p + def_p, 1),
            "offense_rank": None,
            "defense_rank": None,
        }

    for key in ("offense", "defense"):
        ranked = sorted(played, key=lambda t: (-(t["side_power"][key] if t["side_power"][key] is not None else -999), t["abbr"]))
        rank, prev = 0, object()
        for i, t in enumerate(ranked, start=1):
            val = t["side_power"][key]
            if val != prev:
                rank, prev = i, val
            t["side_power"][f"{key}_rank"] = rank


def _team_shell(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": block.get("id", ""),
        "abbr": block.get("abbr", ""),
        "name": block.get("name", ""),
        "full_name": block.get("full_name", ""),
        "logo": block.get("logo", ""),
        "color": block.get("color", "444444"),
        "games": 0,
        "wins": 0,
        "losses": 0,
        "ties": 0,
        "record": "0-0",
        "points_for": 0,
        "points_against": 0,
        "offense": {},
        "defense": {},
        "offense_totals": _empty_totals(),
        "defense_totals": _empty_totals(),
        "ranks": {"offense": {}, "defense": {}},
        "side_power": {"offense": None, "defense": None, "combined": None, "offense_rank": None, "defense_rank": None},
        "game_ids": [],
        "live_game_id": None,
    }


def build_team_table(games: Iterable[dict[str, Any]], known_teams: Iterable[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """Aggregate completed games into per-team offense/defense metrics with league ranks."""
    teams: dict[str, dict[str, Any]] = {}
    for kt in known_teams:
        if kt.get("abbr"):
            teams[kt["abbr"]] = _team_shell(kt)

    for game in games:
        for side, opp in (("home", "away"), ("away", "home")):
            block = game[side]
            team = teams.setdefault(block["abbr"], _team_shell(block))
            if not team.get("logo") and block.get("logo"):
                team["logo"] = block["logo"]
            if game.get("state") != "post":
                team["live_game_id"] = game["id"]
                continue
            team["games"] += 1
            team["game_ids"].append(game["id"])
            team["points_for"] += block["score"]
            team["points_against"] += game[opp]["score"]
            if block["score"] > game[opp]["score"]:
                team["wins"] += 1
            elif block["score"] < game[opp]["score"]:
                team["losses"] += 1
            else:
                team["ties"] += 1
            _add(team["offense_totals"], _side_totals(game, side))
            _add(team["defense_totals"], _side_totals(game, opp))

    table = list(teams.values())
    for team in table:
        team["record"] = f"{team['wins']}-{team['losses']}" + (f"-{team['ties']}" if team["ties"] else "")
        team["offense"] = _rates(team["offense_totals"])
        team["defense"] = _rates(team["defense_totals"])
        team["point_diff"] = team["points_for"] - team["points_against"]

    attach_side_power(table)
    played = [t for t in table if t["games"] > 0]
    for key, _label, higher in METRICS:
        if higher is None:
            continue
        _rank(played, "offense", key, higher)
        _rank(played, "defense", key, key in DEFENSE_HIGHER_BETTER)

    table.sort(key=lambda t: (-t["wins"], t["losses"], -t["point_diff"], t["abbr"]))
    return table


def metric_specs() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": label,
            "defense_label": DEFENSE_LABELS.get(key, label),
            "ranked": higher is not None,
            "pct": key.endswith("_pct") or key.endswith("_rate"),
        }
        for key, label, higher in METRICS
    ]


# ---------------------------------------------------------------- players

PLAYER_SUMS = {
    "passing": {"YDS": "pass_yds", "TD": "pass_td", "INT": "pass_int"},
    "rushing": {"CAR": "rush_att", "YDS": "rush_yds", "TD": "rush_td"},
    "receiving": {"REC": "rec", "YDS": "rec_yds", "TD": "rec_td", "TGTS": "targets"},
    "defensive": {"TOT": "tackles", "SOLO": "solo", "SACKS": "sacks", "TFL": "tfl", "PD": "pass_def", "QB HTS": "qb_hits", "TD": "def_td"},
    "interceptions": {"INT": "int", "YDS": "int_yds"},
    "fumbles": {"FUM": "fum", "LOST": "fum_lost", "REC": "fum_rec"},
    "kicking": {"XP": "xp", "PTS": "kick_pts"},
    "kickReturns": {"NO": "kr", "YDS": "kr_yds"},
    "puntReturns": {"NO": "pr", "YDS": "pr_yds"},
}


def build_player_table(
    games: Iterable[dict[str, Any]], rosters: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    rosters = rosters or {}
    players: dict[str, dict[str, Any]] = {}
    for game in games:
        if game.get("state") != "post":
            continue
        for p in game.get("players") or []:
            entry = players.setdefault(
                p["id"],
                {
                    "id": p["id"],
                    "name": p.get("name", ""),
                    "short_name": p.get("short_name", ""),
                    "team": p.get("team", ""),
                    "position": p.get("position") or rosters.get(p["id"], {}).get("position", ""),
                    "jersey": p.get("jersey") or rosters.get(p["id"], {}).get("jersey", ""),
                    "games": 0,
                    "game_ids": [],
                    "totals": defaultdict(float),
                },
            )
            entry["games"] += 1
            entry["game_ids"].append(game["id"])
            entry["team"] = p.get("team") or entry["team"]
            lines = p.get("lines") or {}
            for cat, mapping in PLAYER_SUMS.items():
                line = lines.get(cat)
                if not line:
                    continue
                for label, key in mapping.items():
                    if label in line:
                        entry["totals"][key] += _num(line[label])
                if cat == "passing" and "C/ATT" in line:
                    c, a = _pair(line["C/ATT"])
                    entry["totals"]["completions"] += c
                    entry["totals"]["pass_att"] += a
                if cat == "kicking" and "FG" in line:
                    m, a = _pair(line["FG"])
                    entry["totals"]["fg_made"] += m
                    entry["totals"]["fg_att"] += a
    out = []
    for entry in players.values():
        entry["totals"] = {k: (int(v) if float(v).is_integer() else round(v, 1)) for k, v in entry["totals"].items()}
        out.append(entry)
    return out


def top_players(players: list[dict[str, Any]], team_abbr: str) -> dict[str, list[dict[str, Any]]]:
    mine = [p for p in players if p["team"] == team_abbr]

    def pick(key: str, n: int = 3) -> list[dict[str, Any]]:
        ranked = sorted((p for p in mine if p["totals"].get(key)), key=lambda p: -p["totals"][key])
        return [
            {
                "id": p["id"],
                "name": p["name"],
                "position": p["position"],
                "games": p["games"],
                "value": p["totals"][key],
                "totals": p["totals"],
            }
            for p in ranked[:n]
        ]

    return {
        "passing": pick("pass_yds", 2),
        "rushing": pick("rush_yds"),
        "receiving": pick("rec_yds", 4),
        "tackles": pick("tackles", 4),
        "sacks": pick("sacks"),
        "pass_defense": pick("pass_def"),
        "interceptions": pick("int"),
    }


def game_log(games: Iterable[dict[str, Any]], team_abbr: str) -> list[dict[str, Any]]:
    log = []
    for g in games:
        side = "home" if g["home"]["abbr"] == team_abbr else "away" if g["away"]["abbr"] == team_abbr else None
        if side is None:
            continue
        opp = "away" if side == "home" else "home"
        mine, theirs = g[side], g[opp]
        st_mine = (g.get("team_stats") or {}).get(side) or {}
        st_opp = (g.get("team_stats") or {}).get(opp) or {}
        result = ""
        if g.get("state") == "post":
            result = "W" if mine["score"] > theirs["score"] else "L" if mine["score"] < theirs["score"] else "T"
        log.append(
            {
                "id": g["id"],
                "week": g.get("week"),
                "date": g.get("date", ""),
                "state": g.get("state"),
                "status_detail": g.get("status_detail", ""),
                "home_away": side,
                "opponent": theirs["abbr"],
                "opponent_name": theirs.get("name", ""),
                "opponent_logo": theirs.get("logo", ""),
                "result": result,
                "score": f"{mine['score']}-{theirs['score']}",
                "points_for": mine["score"],
                "points_against": theirs["score"],
                "yards_for": _num(st_mine.get("totalYards")),
                "yards_against": _num(st_opp.get("totalYards")),
                "rush_for": _num(st_mine.get("rushingYards")),
                "rush_against": _num(st_opp.get("rushingYards")),
                "pass_for": _num(st_mine.get("netPassingYards")),
                "pass_against": _num(st_opp.get("netPassingYards")),
                "turnovers": _num(st_mine.get("turnovers")),
                "takeaways": _num(st_opp.get("turnovers")),
            }
        )
    log.sort(key=lambda r: (r["week"] or 0, r["date"]))
    return log


def play_mix(totals: dict[str, float]) -> dict[str, Any]:
    pass_att = totals["pass_att"] or totals["dropbacks"]
    attempts = totals["rush_att"] + pass_att
    depth_known = totals["deep_att"] + totals["short_att"]
    return {
        "rush": int(totals["rush_att"]),
        "pass": int(pass_att),
        "rush_pct": round(totals["rush_att"] / attempts * 100) if attempts else None,
        "pass_pct": round(pass_att / attempts * 100) if attempts else None,
        "deep": int(totals["deep_att"]),
        "short": int(totals["short_att"]),
        "deep_pct": round(totals["deep_att"] / depth_known * 100) if depth_known else None,
        "short_pct": round(totals["short_att"] / depth_known * 100) if depth_known else None,
        "rush_dir": {k: int(totals[f"rush_{k}"]) for k in ("left", "middle", "right")},
        "pass_dir": {k: int(totals[f"pass_{k}"]) for k in ("left", "middle", "right")},
        "explosive": int(totals["explosive"]),
        "sacks": int(totals["sacks"]),
        "drives": int(totals["drives"]),
        "three_and_outs": int(totals["three_and_outs"]),
        "td_drives": int(totals["td_drives"]),
        "scoring_drives": int(totals["scoring_drives"]),
    }


def team_summary_row(team: dict[str, Any]) -> dict[str, Any]:
    return {
        k: team[k]
        for k in (
            "id",
            "abbr",
            "name",
            "full_name",
            "logo",
            "color",
            "games",
            "wins",
            "losses",
            "ties",
            "record",
            "points_for",
            "points_against",
            "point_diff",
            "offense",
            "defense",
            "ranks",
            "side_power",
            "live_game_id",
        )
    }


def team_detail(
    team: dict[str, Any],
    games: list[dict[str, Any]],
    players: list[dict[str, Any]],
) -> dict[str, Any]:
    detail = team_summary_row(team)
    detail["offense_mix"] = play_mix(team["offense_totals"])
    detail["defense_mix"] = play_mix(team["defense_totals"])
    detail["offense_totals"] = {k: int(v) for k, v in team["offense_totals"].items()}
    detail["defense_totals"] = {k: int(v) for k, v in team["defense_totals"].items()}
    detail["game_log"] = game_log(games, team["abbr"])
    detail["top_players"] = top_players(players, team["abbr"])
    detail["roster_size"] = sum(1 for p in players if p["team"] == team["abbr"])
    return detail
