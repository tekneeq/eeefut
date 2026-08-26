"""Local HTTP dashboard: Matches + Similar."""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from eeefut.data import load_season, previous_season_label
from eeefut.models import Game, GameSnapshot
from eeefut.similar import find_similar

STATIC_DIR = Path(__file__).resolve().parent / "static"

CHIEFS_PRESET_RE = re.compile(r"preset:Chiefs:28")


class DashboardState:
    def __init__(self, season: str) -> None:
        self.season = season
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
            self.wfile.write(body)

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
