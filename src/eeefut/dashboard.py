"""Local HTTP dashboard: Matches + Similar."""

from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from eeefut.data import load_season, previous_season_label
from eeefut.ingest import Ingestor
from eeefut.live import LiveFeed
from eeefut.models import Game, GameSnapshot
from eeefut.similar import find_similar
from eeefut.store import GameStore
from eeefut.teams import build_player_table, build_team_table, metric_specs, team_detail, team_summary_row
from eeefut.winprob import WinProbService, norm_team, store_performance, store_results

STATIC_DIR = Path(__file__).resolve().parent / "static"

CHIEFS_PRESET_RE = re.compile(r"preset:Chiefs:28")
TEAM_PATH_RE = re.compile(r"^/api/teams/([A-Za-z]{2,4})$")
GAME_PATH_RE = re.compile(r"^/api/games/(\d+)$")


class DashboardState:
    def __init__(
        self,
        season: str,
        live: LiveFeed | None = None,
        store: GameStore | None = None,
        ingestor: Ingestor | None = None,
        winprob: WinProbService | None = None,
    ) -> None:
        self.season = season
        self.store = store or GameStore()
        self.live = live or LiveFeed(on_summary=self._persist_live_summary)
        self.ingestor = ingestor or Ingestor(self.store)
        self.winprob = winprob or WinProbService(
            results_provider=lambda yr: store_results(self.store, yr),
            perf_provider=lambda yr: store_performance(self.store, yr),
        )
        self._teams_lock = threading.Lock()
        self._teams_cache: dict[int, tuple[tuple, dict[str, Any]]] = {}
        self.reload()

    def reload(self) -> None:
        self.matches: list[Game] = load_season(self.season)
        self.by_id = {m.match_id: m for m in self.matches}
        try:
            prev = previous_season_label(self.season)
            self.history: list[Game] = load_season(prev)
        except ValueError:
            self.history = []
        self.corpus = [*self.history, *self.matches]

    def _persist_live_summary(self, game: dict[str, Any], summary: dict[str, Any]) -> None:
        """Called by LiveFeed for every freshly fetched box score; keeps finals once."""
        if game.get("state") not in ("in", "post"):
            return
        season = int(((summary.get("header") or {}).get("season") or {}).get("year") or 0)
        if game["state"] == "post" and season and self.store.state_of(season, game["id"]) == "post":
            return
        self.store.save_summary(summary)

    def teams_season(self, requested: str | None) -> int:
        if requested and requested.isdigit():
            return int(requested)
        seasons = self.store.seasons()
        if seasons:
            return seasons[-1]
        m = re.search(r"(\d{4})", self.season)
        return int(m.group(1)) + 1 if m else 0

    def _store_signature(self, season: int) -> tuple:
        season_dir = self.store.root / str(season)
        if not season_dir.is_dir():
            return ()
        return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in season_dir.glob("*.json")))

    def teams_bundle(self, season: int) -> dict[str, Any]:
        sig = self._store_signature(season)
        with self._teams_lock:
            hit = self._teams_cache.get(season)
            if hit and hit[0] == sig:
                return hit[1]
        games = self.store.games(season)
        rosters = self.store.load_rosters(season)
        known = self.store.load_teams(season)
        table = build_team_table(games, known)
        players = build_player_table(games, rosters)
        bundle = {
            "season": season,
            "games": games,
            "table": table,
            "by_abbr": {t["abbr"]: t for t in table},
            "players": players,
            "completed": sum(1 for g in games if g.get("state") == "post"),
            "live": sum(1 for g in games if g.get("state") == "in"),
        }
        with self._teams_lock:
            self._teams_cache[season] = (sig, bundle)
        return bundle

    def power_index(self, season: int) -> dict[str, Any]:
        """Power ratings (with weekly history) keyed by team abbr; empty if the model is unavailable."""
        try:
            board = self.winprob.get(season)
        except Exception:  # noqa: BLE001 - schedule download failure must not break the Teams tab
            return {"by_team": {}, "weeks": [], "through_week": 0}
        keys = ("power", "power_delta", "prev_power", "rank", "prev_rank", "rank_change", "elo", "history")
        return {
            "by_team": {r["team"]: {k: r.get(k) for k in keys} for r in board.get("ratings", [])},
            "weeks": board.get("power_weeks", []),
            "through_week": board.get("through_week", 0),
        }


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


def make_handler(state: DashboardState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # quieter
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if getattr(self, "_omit_body", False):
                return
            self.wfile.write(body)

        def do_HEAD(self) -> None:  # noqa: N802
            self._omit_body = True
            try:
                self.do_GET()
            finally:
                self._omit_body = False

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            if parsed.path == "/api/ingest":
                season = state.teams_season((qs.get("season") or [None])[0])
                include_live = (qs.get("live") or ["0"])[0] in ("1", "true")
                started = state.ingestor.start(season, include_live=include_live)
                status = state.ingestor.status()
                status["started"] = started
                status["season"] = season
                return self._send(202 if started else 200, _json_bytes(status), "application/json")
            return self._send(404, b"not found", "text/plain; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path in ("/", "/index.html"):
                html = (STATIC_DIR / "index.html").read_bytes()
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/static/app.css":
                return self._send(200, (STATIC_DIR / "app.css").read_bytes(), "text/css; charset=utf-8")
            if path == "/static/app.js":
                return self._send(
                    200, (STATIC_DIR / "app.js").read_bytes(), "application/javascript; charset=utf-8"
                )

            if path == "/api/meta":
                preset = next((m for m in state.matches if CHIEFS_PRESET_RE.search(m.match_id)), None)
                return self._send(
                    200,
                    _json_bytes(
                        {
                            "season": state.season,
                            "match_count": len(state.matches),
                            "history_count": len(state.history),
                            "chiefs_preset_id": preset.match_id if preset else None,
                        }
                    ),
                    "application/json",
                )

            if path == "/health":
                return self._send(200, b"ok\n", "text/plain; charset=utf-8")

            if path == "/api/live":
                force = (qs.get("force") or ["0"])[0] in ("1", "true")
                try:
                    board = state.live.get(force=force)
                except Exception as exc:  # noqa: BLE001 - surface feed outages to the UI
                    return self._send(502, _json_bytes({"error": str(exc), "games": []}), "application/json")
                return self._send(200, _json_bytes(board), "application/json")

            if path == "/api/teams":
                season = state.teams_season((qs.get("season") or [None])[0])
                bundle = state.teams_bundle(season)
                power = state.power_index(season)
                rows = []
                for t in bundle["table"]:
                    row = team_summary_row(t)
                    row["power"] = power["by_team"].get(norm_team(t["abbr"]))
                    rows.append(row)
                return self._send(
                    200,
                    _json_bytes(
                        {
                            "season": season,
                            "seasons": state.store.seasons(),
                            "games_stored": len(bundle["games"]),
                            "completed": bundle["completed"],
                            "live": bundle["live"],
                            "players": len(bundle["players"]),
                            "metrics": metric_specs(),
                            "teams": rows,
                            "power_weeks": power["weeks"],
                            "power_through_week": power["through_week"],
                            "ingest": state.ingestor.status(),
                        }
                    ),
                    "application/json",
                )

            team_match = TEAM_PATH_RE.match(path)
            if team_match:
                season = state.teams_season((qs.get("season") or [None])[0])
                bundle = state.teams_bundle(season)
                wanted = team_match.group(1).upper()
                team = bundle["by_abbr"].get(wanted)
                if not team:  # accept nflverse spellings (LA, WAS) for ESPN teams (LAR, WSH)
                    team = next((t for t in bundle["by_abbr"].values() if norm_team(t["abbr"]) == norm_team(wanted)), None)
                if not team:
                    return self._send(404, _json_bytes({"error": "team not found"}), "application/json")
                detail = team_detail(team, bundle["games"], bundle["players"])
                detail["season"] = season
                detail["metrics"] = metric_specs()
                power = state.power_index(season)
                detail["power"] = power["by_team"].get(norm_team(team["abbr"]))
                detail["power_weeks"] = power["weeks"]
                detail["power_through_week"] = power["through_week"]
                return self._send(200, _json_bytes(detail), "application/json")

            game_match = GAME_PATH_RE.match(path)
            if game_match:
                record = state.store.find(game_match.group(1))
                if not record:
                    return self._send(404, _json_bytes({"error": "game not stored"}), "application/json")
                return self._send(200, _json_bytes(record), "application/json")

            if path == "/api/ingest":
                return self._send(200, _json_bytes(state.ingestor.status()), "application/json")

            if path == "/api/winprob":
                season_q = (qs.get("season") or [None])[0]
                force = (qs.get("force") or ["0"])[0] in ("1", "true")
                try:
                    board = state.winprob.get(int(season_q) if season_q and season_q.isdigit() else None, force=force)
                except Exception as exc:  # noqa: BLE001 - schedule download failures surface to the UI
                    return self._send(502, _json_bytes({"error": str(exc), "games": []}), "application/json")
                return self._send(200, _json_bytes(board), "application/json")

            if path == "/api/matches":
                rows = [
                    {
                        "match_id": m.match_id,
                        "date": m.date,
                        "week": m.week,
                        "game_type": m.game_type,
                        "home": m.home,
                        "away": m.away,
                        "ft": f"{m.home_score_ft}-{m.away_score_ft}",
                        "box": f"{m.home_yards_ft}/{m.home_fd_ft} vs {m.away_yards_ft}/{m.away_fd_ft}",
                        "is_preset": bool(CHIEFS_PRESET_RE.search(m.match_id)),
                    }
                    for m in state.matches
                ]
                return self._send(200, _json_bytes({"matches": rows}), "application/json")

            if path == "/api/snapshot":
                mid = (qs.get("match_id") or [None])[0]
                minute = int((qs.get("minute") or ["28"])[0])
                match = state.by_id.get(mid or "")
                if not match:
                    return self._send(404, _json_bytes({"error": "match not found"}), "application/json")
                snap = match.snapshot_at(minute)
                return self._send(
                    200,
                    _json_bytes(
                        {
                            "match_id": match.match_id,
                            "home": match.home,
                            "away": match.away,
                            "snapshot": snap.to_dict(),
                            "label": snap.label(),
                            "clock": snap.clock(),
                        }
                    ),
                    "application/json",
                )

            if path == "/api/similar":
                mid = (qs.get("match_id") or [None])[0]
                minute = int((qs.get("minute") or ["28"])[0])
                limit = int((qs.get("limit") or ["12"])[0])
                match = state.by_id.get(mid or "")
                if match:
                    snap = match.snapshot_at(minute)
                    exclude = {match.match_id}
                else:
                    try:
                        raw_times = (qs.get("scores") or [""])[0]
                        score_minutes = tuple(
                            int(x)
                            for x in (raw_times.split(",") if raw_times else [])
                            if x.strip().isdigit()
                        )
                        snap = GameSnapshot(
                            minute=minute,
                            home_score=int((qs.get("hs") or ["0"])[0]),
                            away_score=int((qs.get("as") or ["0"])[0]),
                            home_yards=int((qs.get("hy") or ["0"])[0]),
                            away_yards=int((qs.get("ay") or ["0"])[0]),
                            home_fd=int((qs.get("hfd") or ["0"])[0]),
                            away_fd=int((qs.get("afd") or ["0"])[0]),
                            score_minutes=score_minutes,
                        )
                        exclude = set()
                    except (TypeError, ValueError):
                        return self._send(400, _json_bytes({"error": "bad query"}), "application/json")
                hits = find_similar(snap, state.corpus, limit=limit, exclude_ids=exclude)
                return self._send(
                    200,
                    _json_bytes(
                        {"query": snap.to_dict(), "label": snap.label(), "hits": [h.to_dict() for h in hits]}
                    ),
                    "application/json",
                )

            return self._send(404, b"not found", "text/plain; charset=utf-8")

    return Handler


def serve(*, port: int, season: str, host: str = "127.0.0.1") -> None:
    state = DashboardState(season)
    handler = make_handler(state)
    httpd = ThreadingHTTPServer((host, port), handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
