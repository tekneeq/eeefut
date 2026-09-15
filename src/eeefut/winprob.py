"""Win probability model: margin-aware Elo replayed over nflverse history.

Every game of the target season gets its *pre-game* probability (ratings as they stood
before kickoff), so the record dashboard and calibration buckets are honest. Unplayed
games use the current ratings. Market lines (spread / moneyline) ride along for reference.
"""

from __future__ import annotations

import csv
import io
import math
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from eeefut.cache import cache_root
from eeefut.data import GAMES_URL, TEAM_NAMES, _fetch_text

TEAM_ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA", "WSH": "WAS", "JAC": "JAX"}
ESPN_LOGO_SLUG = {"LA": "lar", "WAS": "wsh"}
GAMES_CSV_TTL = 6 * 3600.0

BUCKETS: list[tuple[str, float, float]] = [
    ("50-55", 0.50, 0.55),
    ("55-60", 0.55, 0.60),
    ("60-65", 0.60, 0.65),
    ("65-70", 0.65, 0.70),
    (">70", 0.70, 1.01),
]


@dataclass(frozen=True)
class EloConfig:
    k: float = 20.0
    hfa: float = 48.0  # ~1.9 points of home-field advantage
    mean: float = 1505.0
    regress: float = 1 / 3  # pull toward the mean between seasons
    points_per_elo: float = 25.0  # expected margin = elo diff / 25


def norm_team(abbr: str) -> str:
    a = (abbr or "").strip().upper()
    return TEAM_ALIASES.get(a, a)


def team_logo(abbr: str) -> str:
    slug = ESPN_LOGO_SLUG.get(abbr, abbr.lower())
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/scoreboard/{slug}.png"


def elo_win_prob(diff: float) -> float:
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def moneyline_prob(ml: Any) -> float | None:
    try:
        v = float(ml)
    except (TypeError, ValueError):
        return None
    if v == 0:
        return None
    return (-v) / (-v + 100.0) if v < 0 else 100.0 / (v + 100.0)


def bucket_for(prob: float) -> str:
    p = max(prob, 1 - prob)
    for key, lo, hi in BUCKETS:
        if lo <= p < hi:
            return key
    return BUCKETS[-1][0]


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    v = _float(value)
    return None if v is None else int(v)


def _round_half(x: float) -> float:
    return round(x * 2) / 2


def load_games_csv(fetch_text: Callable[[str], str] = _fetch_text, *, ttl: float = GAMES_CSV_TTL) -> list[dict[str, str]]:
    """nflverse schedules/games.csv (all seasons) with a disk cache."""
    path = cache_root() / "nflverse" / "games.csv"
    text: str | None = None
    if path.is_file() and time.time() - path.stat().st_mtime < ttl:
        text = path.read_text(encoding="utf-8")
    if text is None:
        try:
            text = fetch_text(GAMES_URL)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except Exception:
            if path.is_file():
                text = path.read_text(encoding="utf-8")
            else:
                raise
    return list(csv.DictReader(io.StringIO(text)))


def _sort_key(row: dict[str, str]) -> tuple:
    return (
        _int(row.get("season")) or 0,
        row.get("gameday") or "",
        row.get("gametime") or "",
        row.get("game_id") or "",
    )


def power_of(elo: float, config: EloConfig = EloConfig()) -> float:
    """Power number: points better (+) or worse (-) than an average team on a neutral field."""
    return round((elo - config.mean) / config.points_per_elo, 1)


def score_100(point_power: float | None, config: EloConfig = EloConfig()) -> float | None:
    """Map a point-differential power number onto 0–100 (50 = league average).

    Same curve as Elo win probability vs an average unit: about 7 points ≈ 75.
    """
    if point_power is None:
        return None
    return round(100.0 * elo_win_prob(float(point_power) * config.points_per_elo), 1)


def performance_margin(
    home_yards: float, away_yards: float, home_turnovers: float, away_turnovers: float, *, yards_per_point: float = 15.0, points_per_turnover: float = 4.0
) -> float:
    """Box-score view of the margin: yardage edge in points plus the turnover swing."""
    return (home_yards - away_yards) / yards_per_point + (away_turnovers - home_turnovers) * points_per_turnover


def blended_margin(mov: int, perf: float | None, *, weight: float = 0.3, cap: float = 35.0) -> float:
    """Effective margin used for the rating update: mostly the score, partly how the game was played.

    Keeps the sign of the actual result (a win is still a win) but a fluky win over a team
    that outgained you shrinks toward the minimum, and a dominant win grows.
    """
    if perf is None or mov == 0:
        return float(mov)
    eff = (1 - weight) * mov + weight * perf
    if (eff > 0) != (mov > 0):
        eff = 1.0 if mov > 0 else -1.0
    return max(-cap, min(cap, eff))


def run_model(
    rows: Iterable[dict[str, str]],
    season: int,
    *,
    extra_results: dict[str, tuple[int, int]] | None = None,
    extra_perf: dict[str, float] | None = None,
    config: EloConfig = EloConfig(),
) -> dict[str, Any]:
    """Replay Elo over all rows; return ratings, weekly power history, and predictions for `season`.

    `extra_results` maps ESPN event id -> (home_score, away_score) for games nflverse has
    not scored yet (e.g. pulled from the live GameStore). `extra_perf` maps ESPN event id
    -> box-score performance margin (home minus away, in points) used to blend the update.
    """
    extra_results = extra_results or {}
    extra_perf = extra_perf or {}
    ratings: dict[str, float] = {}
    last_season: int | None = None
    predictions: list[dict[str, Any]] = []
    records: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    snapshots: list[tuple[int, dict[str, float]]] = []  # (week, ratings after that week)
    current_week: int | None = None
    week_played = False

    def rating(team: str) -> float:
        return ratings.setdefault(team, config.mean)

    def snapshot(week: int) -> None:
        snapshots.append((week, {t: e for t, e in ratings.items() if t in TEAM_NAMES}))

    for row in sorted(rows, key=_sort_key):
        row_season = _int(row.get("season"))
        if row_season is None or row_season > season:
            continue
        if last_season is not None and row_season != last_season:
            for team in list(ratings):
                ratings[team] = config.mean + (ratings[team] - config.mean) * (1 - config.regress)
        if row_season == season and last_season != season:
            snapshot(0)  # preseason baseline after regression
        last_season = row_season

        home = norm_team(row.get("home_team", ""))
        away = norm_team(row.get("away_team", ""))
        if not home or not away:
            continue
        row_week = _int(row.get("week")) or 0
        if row_season == season and row_week != current_week:
            if current_week is not None and week_played:
                snapshot(current_week)
            current_week, week_played = row_week, False

        neutral = str(row.get("location") or "").strip().lower() == "neutral"
        hfa = 0.0 if neutral else config.hfa
        home_elo, away_elo = rating(home), rating(away)
        diff = home_elo + hfa - away_elo
        p_home = elo_win_prob(diff)

        espn_id = str(row.get("espn") or "")
        home_score = _int(row.get("home_score"))
        away_score = _int(row.get("away_score"))
        if home_score is None or away_score is None:
            extra = extra_results.get(espn_id)
            if extra:
                home_score, away_score = extra
        played = home_score is not None and away_score is not None

        if row_season == season:
            predictions.append(
                _prediction(row, home, away, home_elo, away_elo, p_home, diff, config, home_score, away_score, neutral)
            )

        if not played:
            continue
        mov = home_score - away_score
        perf = extra_perf.get(espn_id)
        eff = blended_margin(mov, perf)
        if mov > 0:
            outcome, winner_diff = 1.0, diff
        elif mov < 0:
            outcome, winner_diff = 0.0, -diff
        else:
            outcome, winner_diff = 0.5, 0.0
        mult = math.log(abs(eff) + 1) * (2.2 / (winner_diff * 0.001 + 2.2))
        shift = config.k * mult * (outcome - p_home)
        ratings[home] = home_elo + shift
        ratings[away] = away_elo - shift
        if row_season == season:
            week_played = True
            if predictions and predictions[-1]["id"] == row.get("game_id"):
                predictions[-1]["performance_margin"] = None if perf is None else round(perf, 1)
                predictions[-1]["effective_margin"] = round(eff, 1)
                predictions[-1]["home_shift"] = round(shift / config.points_per_elo, 2)
            rec_home, rec_away = records[home], records[away]
            if mov > 0:
                rec_home[0] += 1
                rec_away[1] += 1
            elif mov < 0:
                rec_home[1] += 1
                rec_away[0] += 1
            else:
                rec_home[2] += 1
                rec_away[2] += 1

    if current_week is not None and week_played:
        snapshot(current_week)
    if not snapshots:
        snapshot(0)

    # Rank within each snapshot so history carries rank movement too.
    ranked_snapshots: list[tuple[int, dict[str, tuple[float, int]]]] = []
    for week, snap in snapshots:
        order = sorted(snap.items(), key=lambda kv: -kv[1])
        ranked_snapshots.append((week, {t: (e, i) for i, (t, e) in enumerate(order, start=1)}))

    table = []
    for team, elo in ratings.items():
        if team not in TEAM_NAMES:
            continue
        w, l, t = records.get(team, [0, 0, 0])
        history = [
            {
                "week": week,
                "elo": round(snap[team][0], 1),
                "power": power_of(snap[team][0], config),
                "score": score_100(power_of(snap[team][0], config), config),
                "rank": snap[team][1],
            }
            for week, snap in ranked_snapshots
            if team in snap
        ]
        prev = history[-2] if len(history) >= 2 else None
        now = power_of(elo, config)
        now_score = score_100(now, config)
        table.append(
            {
                "team": team,
                "name": TEAM_NAMES.get(team, team),
                "logo": team_logo(team),
                "elo": round(elo, 1),
                "power": now,
                "score": now_score,
                "prev_power": prev["power"] if prev else None,
                "prev_score": prev["score"] if prev else None,
                "power_delta": round(now - prev["power"], 1) if prev else None,
                "score_delta": round(now_score - prev["score"], 1) if prev and now_score is not None else None,
                "prev_rank": prev["rank"] if prev else None,
                "history": history,
                "record": f"{w}-{l}" + (f"-{t}" if t else ""),
                "wins": w,
                "losses": l,
                "ties": t,
            }
        )
    table.sort(key=lambda r: -r["elo"])
    for i, r in enumerate(table, start=1):
        r["rank"] = i
        r["rank_change"] = (r["prev_rank"] - i) if r["prev_rank"] else None
    return {
        "season": season,
        "ratings": table,
        "games": predictions,
        "power_weeks": [w for w, _ in ranked_snapshots],
        "through_week": ranked_snapshots[-1][0] if ranked_snapshots else 0,
        "rank_ladder": rank_ladder(table),
    }


def rank_ladder(ratings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One column per completed week (1…n): the team sitting at each rank 1–32.

    Week 0 (preseason baseline) is omitted so the x-axis is the played schedule.
    """
    weeks = sorted(
        {int(h["week"]) for r in ratings for h in r.get("history") or [] if int(h.get("week") or 0) > 0}
    )
    columns: list[dict[str, Any]] = []
    for week in weeks:
        by_rank: dict[int, dict[str, Any]] = {}
        for row in ratings:
            hit = next((h for h in (row.get("history") or []) if int(h.get("week") or 0) == week), None)
            if not hit:
                continue
            rank = int(hit["rank"])
            by_rank[rank] = {
                "rank": rank,
                "team": row["team"],
                "name": row.get("name") or row["team"],
                "logo": row.get("logo") or team_logo(row["team"]),
                "power": hit.get("power"),
                "score": hit.get("score"),
            }
        columns.append({"week": week, "ranks": [by_rank[i] for i in range(1, 33) if i in by_rank]})
    return columns


def _prediction(
    row: dict[str, str],
    home: str,
    away: str,
    home_elo: float,
    away_elo: float,
    p_home: float,
    diff: float,
    config: EloConfig,
    home_score: int | None,
    away_score: int | None,
    neutral: bool,
) -> dict[str, Any]:
    expected_margin = _round_half(diff / config.points_per_elo)
    favorite = home if p_home >= 0.5 else away
    fav_prob = max(p_home, 1 - p_home)
    market = None
    spread = _float(row.get("spread_line"))
    mh, ma = moneyline_prob(row.get("home_moneyline")), moneyline_prob(row.get("away_moneyline"))
    if spread is not None or (mh is not None and ma is not None):
        mkt_home = mh / (mh + ma) if mh is not None and ma is not None else None
        market = {
            "spread": spread,  # positive = home favoured by that many points
            "home_prob": round(mkt_home, 4) if mkt_home is not None else None,
            "away_prob": round(1 - mkt_home, 4) if mkt_home is not None else None,
            "favorite": (home if spread > 0 else away if spread < 0 else None) if spread is not None else None,
        }
    game: dict[str, Any] = {
        "id": row.get("game_id", ""),
        "espn_id": str(row.get("espn") or ""),
        "week": _int(row.get("week")) or 0,
        "game_type": row.get("game_type") or "REG",
        "date": row.get("gameday") or "",
        "time": row.get("gametime") or "",
        "weekday": row.get("weekday") or "",
        "neutral": neutral,
        "home": home,
        "away": away,
        "home_name": TEAM_NAMES.get(home, home),
        "away_name": TEAM_NAMES.get(away, away),
        "home_logo": team_logo(home),
        "away_logo": team_logo(away),
        "home_elo": round(home_elo, 1),
        "away_elo": round(away_elo, 1),
        "home_prob": round(p_home, 4),
        "away_prob": round(1 - p_home, 4),
        "favorite": favorite,
        "favorite_prob": round(fav_prob, 4),
        "favorite_home": favorite == home,
        "expected_margin": expected_margin,  # home minus away
        "favorite_margin": abs(expected_margin),
        "bucket": bucket_for(p_home),
        "market": market,
        "played": home_score is not None and away_score is not None,
        "home_score": home_score,
        "away_score": away_score,
        "result": None,
    }
    if game["played"]:
        mov = home_score - away_score
        winner = home if mov > 0 else away if mov < 0 else None
        correct = None if winner is None else winner == favorite
        market_correct = None
        if market and market.get("favorite") and winner is not None:
            market_correct = market["favorite"] == winner
        outcome = 1.0 if mov > 0 else 0.0 if mov < 0 else 0.5
        game["result"] = {
            "margin": mov,
            "winner": winner,
            "correct": correct,
            "market_correct": market_correct,
            "brier": round((p_home - outcome) ** 2, 4),
            "margin_error": abs(expected_margin - mov),
            "covered": (mov - expected_margin > 0) if mov != expected_margin else None,
        }
    return game


# ------------------------------------------------------------------ dashboards


def _record_block() -> dict[str, Any]:
    return {"games": 0, "decided": 0, "correct": 0, "wrong": 0, "ties": 0, "brier_sum": 0.0, "margin_err_sum": 0.0, "market_correct": 0, "market_decided": 0, "pending": 0}


def _finish(block: dict[str, Any]) -> dict[str, Any]:
    decided = block["decided"]
    out = dict(block)
    out["pct"] = round(block["correct"] / decided * 100, 1) if decided else None
    out["brier"] = round(block["brier_sum"] / decided, 3) if decided else None
    out["avg_margin_error"] = round(block["margin_err_sum"] / decided, 1) if decided else None
    out["market_pct"] = round(block["market_correct"] / block["market_decided"] * 100, 1) if block["market_decided"] else None
    out["record"] = f"{block['correct']}-{block['wrong']}" + (f"-{block['ties']}" if block["ties"] else "")
    for k in ("brier_sum", "margin_err_sum"):
        out.pop(k)
    return out


def _tally(block: dict[str, Any], game: dict[str, Any]) -> None:
    block["games"] += 1
    res = game.get("result")
    if not res:
        block["pending"] += 1
        return
    if res["correct"] is None:
        block["ties"] += 1
        return
    block["decided"] += 1
    block["correct"] += int(res["correct"])
    block["wrong"] += int(not res["correct"])
    block["brier_sum"] += res["brier"]
    block["margin_err_sum"] += res["margin_error"]
    if res["market_correct"] is not None:
        block["market_decided"] += 1
        block["market_correct"] += int(res["market_correct"])


def weekly_record(games: list[dict[str, Any]]) -> dict[str, Any]:
    weeks: dict[int, dict[str, Any]] = {}
    total = _record_block()
    for g in games:
        block = weeks.setdefault(g["week"], _record_block())
        _tally(block, g)
        _tally(total, g)
    weekly = [{"week": w, **_finish(b)} for w, b in sorted(weeks.items())]
    return {"weekly": weekly, "total": _finish(total)}


def _bucket_block(key: str, lo: float, hi: float) -> dict[str, Any]:
    side = lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0}  # noqa: E731
    return {"key": key, "label": key + "%", "lo": lo, "hi": hi, "games": 0, "wins": 0, "losses": 0, "ties": 0, "pending": 0, "prob_sum": 0.0, "home": side(), "away": side()}


def bucket_record(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Favourite's record by pre-game probability bucket, split home/away underneath."""
    blocks = {key: _bucket_block(key, lo, hi) for key, lo, hi in BUCKETS}
    for g in games:
        b = blocks[g["bucket"]]
        res = g.get("result")
        if not res:
            b["pending"] += 1
            continue
        side = b["home" if g["favorite_home"] else "away"]
        b["games"] += 1
        side["games"] += 1
        b["prob_sum"] += g["favorite_prob"]
        if res["correct"] is None:
            b["ties"] += 1
            side["ties"] += 1
        elif res["correct"]:
            b["wins"] += 1
            side["wins"] += 1
        else:
            b["losses"] += 1
            side["losses"] += 1
    out = []
    for key, _lo, _hi in BUCKETS:
        b = blocks[key]
        decided = b["wins"] + b["losses"]
        b["pct"] = round(b["wins"] / decided * 100, 1) if decided else None
        b["expected_pct"] = round(b["prob_sum"] / b["games"] * 100, 1) if b["games"] else None
        b["record"] = f"{b['wins']}-{b['losses']}" + (f"-{b['ties']}" if b["ties"] else "")
        for s in ("home", "away"):
            sd = b[s]
            sd["record"] = f"{sd['wins']}-{sd['losses']}" + (f"-{sd['ties']}" if sd["ties"] else "")
            d = sd["wins"] + sd["losses"]
            sd["pct"] = round(sd["wins"] / d * 100, 1) if d else None
        b.pop("prob_sum")
        out.append(b)
    return out


def team_bucket_records(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per team: record when favoured (by bucket) and when the underdog."""
    teams: dict[str, dict[str, Any]] = {}

    def shell(team: str) -> dict[str, Any]:
        return {
            "team": team,
            "name": TEAM_NAMES.get(team, team),
            "logo": team_logo(team),
            "favored": {"wins": 0, "losses": 0, "ties": 0},
            "underdog": {"wins": 0, "losses": 0, "ties": 0},
            "buckets": {key: {"wins": 0, "losses": 0, "ties": 0} for key, _, _ in BUCKETS},
        }

    for g in games:
        res = g.get("result")
        if not res:
            continue
        fav, dog = g["favorite"], (g["away"] if g["favorite"] == g["home"] else g["home"])
        tf, td = teams.setdefault(fav, shell(fav)), teams.setdefault(dog, shell(dog))
        if res["winner"] is None:
            tf["favored"]["ties"] += 1
            td["underdog"]["ties"] += 1
            tf["buckets"][g["bucket"]]["ties"] += 1
        elif res["winner"] == fav:
            tf["favored"]["wins"] += 1
            td["underdog"]["losses"] += 1
            tf["buckets"][g["bucket"]]["wins"] += 1
        else:
            tf["favored"]["losses"] += 1
            td["underdog"]["wins"] += 1
            tf["buckets"][g["bucket"]]["losses"] += 1
    out = []
    for t in teams.values():
        for blk in (t["favored"], t["underdog"], *t["buckets"].values()):
            blk["record"] = f"{blk['wins']}-{blk['losses']}" + (f"-{blk['ties']}" if blk["ties"] else "")
        out.append(t)
    out.sort(key=lambda t: (-(t["favored"]["wins"] + t["favored"]["losses"] + t["favored"]["ties"]), t["team"]))
    return out


def current_week(games: list[dict[str, Any]]) -> int:
    pending = [g["week"] for g in games if not g["played"]]
    if pending:
        return min(pending)
    return max((g["week"] for g in games), default=1)


def build_dashboard(model: dict[str, Any]) -> dict[str, Any]:
    games = model["games"]
    weeks = sorted({g["week"] for g in games})
    return {
        "season": model["season"],
        "model": {
            "name": "Elo + margin of victory",
            "hfa_points": round(EloConfig().hfa / EloConfig().points_per_elo, 1),
            "k": EloConfig().k,
            "power_scale": "0–100 (50 = an average team on a neutral field)",
        },
        "weeks": weeks,
        "current_week": current_week(games),
        "power_weeks": model.get("power_weeks", []),
        "through_week": model.get("through_week", 0),
        "games": games,
        "record": weekly_record(games),
        "buckets": bucket_record(games),
        "team_buckets": team_bucket_records(games),
        "ratings": model["ratings"],
        "rank_ladder": model.get("rank_ladder") or rank_ladder(model.get("ratings") or []),
        "generated_at": int(time.time()),
    }


class WinProbService:
    """Caches the nflverse schedule + model output; recomputes when inputs change."""

    def __init__(
        self,
        *,
        fetch_text: Callable[[str], str] = _fetch_text,
        results_provider: Callable[[int], dict[str, tuple[int, int]]] | None = None,
        perf_provider: Callable[[int], dict[str, float]] | None = None,
        ttl: float = 300.0,
        rows: list[dict[str, str]] | None = None,
    ) -> None:
        self._fetch_text = fetch_text
        self._results_provider = results_provider
        self._perf_provider = perf_provider
        self._ttl = ttl
        self._rows = rows
        self._lock = threading.Lock()
        self._cache: dict[int, tuple[float, Any, dict[str, Any]]] = {}

    def rows(self) -> list[dict[str, str]]:
        if self._rows is None:
            self._rows = load_games_csv(self._fetch_text)
        return self._rows

    def refresh_rows(self) -> None:
        path = cache_root() / "nflverse" / "games.csv"
        if path.is_file():
            path.unlink()
        self._rows = None

    def latest_season(self) -> int:
        return max((_int(r.get("season")) or 0) for r in self.rows())

    def get(self, season: int | None = None, *, force: bool = False) -> dict[str, Any]:
        if force:
            self.refresh_rows()
        season = season or self.latest_season()
        extra = self._results_provider(season) if self._results_provider else {}
        perf = self._perf_provider(season) if self._perf_provider else {}
        sig = (tuple(sorted(extra.items())), tuple(sorted(perf.items())))
        now = time.time()
        with self._lock:
            hit = self._cache.get(season)
            if hit and not force and now - hit[0] < self._ttl and hit[1] == sig:
                return hit[2]
        model = run_model(self.rows(), season, extra_results=extra, extra_perf=perf)
        board = build_dashboard(model)
        with self._lock:
            self._cache[season] = (now, sig, board)
        return board


def store_results(store: Any, season: int) -> dict[str, tuple[int, int]]:
    """Final scores from the GameStore keyed by ESPN event id (fills nflverse lag)."""
    out: dict[str, tuple[int, int]] = {}
    try:
        games = store.games(season)
    except Exception:  # noqa: BLE001
        return out
    for g in games:
        if g.get("state") == "post":
            out[str(g["id"])] = (int(g["home"]["score"]), int(g["away"]["score"]))
    return out


def store_performance(store: Any, season: int) -> dict[str, float]:
    """Box-score performance margin (home minus away, points) per finished game in the store."""
    out: dict[str, float] = {}
    try:
        games = store.games(season)
    except Exception:  # noqa: BLE001
        return out
    for g in games:
        if g.get("state") != "post":
            continue
        ts = g.get("team_stats") or {}
        hs, as_ = ts.get("home") or {}, ts.get("away") or {}
        hy, ay = _float(hs.get("totalYards")), _float(as_.get("totalYards"))
        if hy is None or ay is None:
            continue
        ht, at = _float(hs.get("turnovers")) or 0.0, _float(as_.get("turnovers")) or 0.0
        out[str(g["id"])] = round(performance_margin(hy, ay, ht, at), 2)
    return out
