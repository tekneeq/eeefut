"""Backfill completed games (drives / plays / players) and rosters into the GameStore."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from eeefut.live import SUMMARY_URL, fetch_json
from eeefut.store import GameStore

SCOREBOARD_WEEK_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    "?dates={year}&seasontype={season_type}&week={week}"
)
TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/roster"

REGULAR_SEASON_WEEKS = 18
ROSTER_TTL = 7 * 24 * 3600.0

FetchJson = Callable[[str], dict[str, Any]]


def _event_state(event: dict[str, Any]) -> str:
    comp = (event.get("competitions") or [{}])[0]
    status = comp.get("status") or event.get("status") or {}
    return str(((status.get("type") or {}).get("state")) or "pre")


def fetch_rosters(fetch: FetchJson = fetch_json, workers: int = 6) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return (players by athlete id, team list) from the ESPN teams + roster endpoints."""
    payload = fetch(TEAMS_URL)
    teams_raw = []
    for sport in payload.get("sports") or []:
        for league in sport.get("leagues") or []:
            teams_raw.extend(t.get("team") or {} for t in league.get("teams") or [])
    teams = [
        {
            "id": str(t.get("id") or ""),
            "abbr": str(t.get("abbreviation") or ""),
            "name": str(t.get("nickname") or t.get("shortDisplayName") or t.get("name") or ""),
            "full_name": str(t.get("displayName") or ""),
            "location": str(t.get("location") or ""),
            "color": str(t.get("color") or "444444"),
            "logo": str(((t.get("logos") or [{}])[0]).get("href") or ""),
        }
        for t in teams_raw
        if t.get("id")
    ]

    def one(team: dict[str, Any]) -> dict[str, dict[str, Any]]:
        try:
            roster = fetch(ROSTER_URL.format(team_id=team["id"]))
        except Exception:  # noqa: BLE001 - one missing roster shouldn't sink the batch
            return {}
        out: dict[str, dict[str, Any]] = {}
        for group in roster.get("athletes") or []:
            group_name = str(group.get("position") or "")
            for ath in group.get("items") or []:
                pid = str(ath.get("id") or "")
                if not pid:
                    continue
                pos = ath.get("position") or {}
                out[pid] = {
                    "id": pid,
                    "name": str(ath.get("displayName") or ""),
                    "short_name": str(ath.get("shortName") or ""),
                    "position": str(pos.get("abbreviation") or "") if isinstance(pos, dict) else str(pos or ""),
                    "group": group_name,
                    "jersey": str(ath.get("jersey") or ""),
                    "age": ath.get("age"),
                    "experience": ((ath.get("experience") or {}).get("years")),
                    "team": team["abbr"],
                    "status": str(((ath.get("status") or {}).get("type")) or ""),
                }
        return out

    players: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(teams) or 1))) as pool:
        for chunk in pool.map(one, teams):
            players.update(chunk)
    return players, teams


class Ingestor:
    """Run season backfills (sync or in a background thread) and report progress."""

    def __init__(self, store: GameStore, fetch: FetchJson = fetch_json, workers: int = 6) -> None:
        self.store = store
        self._fetch = fetch
        self._workers = max(1, workers)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {
            "running": False,
            "season": None,
            "message": "idle",
            "weeks_done": 0,
            "fetched": 0,
            "skipped": 0,
            "errors": [],
            "started_at": None,
            "finished_at": None,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _set(self, **kwargs: Any) -> None:
        with self._lock:
            self._status.update(kwargs)

    def ensure_rosters(self, season: int, *, force: bool = False) -> int:
        path = self.store.roster_path(season)
        if not force and path.is_file() and time.time() - path.stat().st_mtime < ROSTER_TTL:
            return len(self.store.load_rosters(season))
        players, teams = fetch_rosters(self._fetch, self._workers)
        if players or teams:
            self.store.save_rosters(season, players, teams)
        return len(players)

    def ingest_week(self, season: int, week: int, *, season_type: int = 2, include_live: bool = False) -> dict[str, int]:
        board = self._fetch(SCOREBOARD_WEEK_URL.format(year=season, season_type=season_type, week=week))
        events = board.get("events") or []
        todo: list[str] = []
        skipped = 0
        states = {"pre": 0, "in": 0, "post": 0}
        for ev in events:
            state = _event_state(ev)
            states[state] = states.get(state, 0) + 1
            gid = str(ev.get("id") or "")
            if not gid:
                continue
            if state == "post":
                if self.store.state_of(season, gid) == "post":
                    skipped += 1
                    continue
                todo.append(gid)
            elif state == "in" and include_live:
                todo.append(gid)

        def one(gid: str) -> bool:
            try:
                summary = self._fetch(SUMMARY_URL.format(event_id=gid))
                self.store.save_summary(summary)
                return True
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._status["errors"].append(f"{gid}: {exc}")
                return False

        fetched = 0
        if todo:
            with ThreadPoolExecutor(max_workers=min(self._workers, len(todo))) as pool:
                fetched = sum(1 for ok in pool.map(one, todo) if ok)
        return {"events": len(events), "fetched": fetched, "skipped": skipped, **states}

    def run(self, season: int, *, weeks: list[int] | None = None, include_live: bool = False, rosters: bool = True) -> dict[str, Any]:
        self._set(
            running=True,
            season=season,
            message="starting",
            weeks_done=0,
            fetched=0,
            skipped=0,
            errors=[],
            started_at=int(time.time()),
            finished_at=None,
        )
        try:
            if rosters:
                self._set(message="rosters")
                try:
                    self.ensure_rosters(season)
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        self._status["errors"].append(f"rosters: {exc}")
            week_list = weeks or list(range(1, REGULAR_SEASON_WEEKS + 1))
            idle_weeks = 0
            for week in week_list:
                self._set(message=f"week {week}")
                try:
                    res = self.ingest_week(season, week, include_live=include_live)
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        self._status["errors"].append(f"week {week}: {exc}")
                    continue
                with self._lock:
                    self._status["weeks_done"] += 1
                    self._status["fetched"] += res["fetched"]
                    self._status["skipped"] += res["skipped"]
                if weeks is None:
                    if res["events"] and res.get("post", 0) == 0 and res.get("in", 0) == 0:
                        idle_weeks += 1
                        if idle_weeks >= 2:
                            break
                    else:
                        idle_weeks = 0
            self._set(message="done")
        finally:
            self._set(running=False, finished_at=int(time.time()))
        return self.status()

    def start(self, season: int, **kwargs: Any) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._thread = threading.Thread(target=self.run, args=(season,), kwargs=kwargs, daemon=True)
            self._thread.start()
            return True

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread:
            thread.join(timeout)
