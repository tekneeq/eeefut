"""Tests for eeefut Matches + Similar."""

from __future__ import annotations

from eeefut.cache import cache_root, read_json, write_json
from eeefut.data import (
    inject_chiefs_preset,
    matches_from_csv,
    parse_warm_spec,
    previous_season_label,
    save_season,
    load_season,
)
from eeefut.models import Game, GameSnapshot, ScoreEvent, clock_label
from eeefut.similar import find_similar, snapshot_distance
from eeefut.timeline import build_timelines, cumulative_yards, decompose_score, place_scores


SAMPLE_GAMES = """game_id,season,game_type,week,gameday,away_team,away_score,home_team,home_score
2024_01_BAL_KC,2024,REG,1,2024-09-05,BAL,20,KC,27
2024_01_ARI_BUF,2024,REG,1,2024-09-08,ARI,28,BUF,34
2025_01_BAL_KC,2025,REG,1,2025-09-04,BAL,20,KC,27
"""

SAMPLE_STATS = """game_id,team,passing_yards,rushing_yards,passing_first_downs,rushing_first_downs
2024_01_BAL_KC,KC,260,129,16,8
2024_01_BAL_KC,BAL,237,104,14,6
2024_01_ARI_BUF,BUF,232,131,15,9
2024_01_ARI_BUF,ARI,162,124,10,7
"""


def test_parse_warm_spec_nfl_2025():
    label, year = parse_warm_spec("NFL:2025")
    assert label == "NFL:2025"
    assert year == 2025


def test_previous_season_label():
    assert previous_season_label("NFL:2025") == "NFL:2024"


def test_clock_label_q2_two_minute():
    assert clock_label(28) == "Q2 2:00"
    assert clock_label(15) == "Q1 0:00"
    assert clock_label(60) == "Final"


def test_decompose_score_common_totals():
    assert sum(decompose_score(24)) == 24
    assert sum(decompose_score(27)) == 27
    assert sum(decompose_score(3)) == 3
    assert decompose_score(0) == []


def test_place_scores_match_ft():
    events = place_scores(home_ft=24, away_ft=10, seed=42)
    assert sum(e.points for e in events if e.team == "home") == 24
    assert sum(e.points for e in events if e.team == "away") == 10
    assert all(1 <= e.minute <= 60 for e in events)


def test_cumulative_yards_end_totals():
    yards, fd = cumulative_yards(245, 14, seed=7)
    assert yards[60] == 245
    assert fd[60] == 14
    assert yards[28] <= 245
    assert fd[28] <= yards[28] or fd[28] <= 14


def test_chiefs_28_preset_label(tmp_path, monkeypatch):
    monkeypatch.setenv("EEEFUT_CACHE", str(tmp_path))
    matches = inject_chiefs_preset([], "NFL:2025")
    chiefs = matches[0]
    snap = chiefs.snapshot_at(28)
    assert snap.home_yards == 245
    assert snap.home_fd == 14
    assert snap.away_yards == 168
    assert snap.away_fd == 9
    assert snap.score_minutes == (14, 28)
    assert snap.home_score == 14
    assert snap.away_score == 0
    assert snap.label() == "14'/28' · 245/14 vs 168/9"
    assert snap.clock() == "Q2 2:00"


def test_snapshot_distance_identical_is_zero():
    a = GameSnapshot(28, 14, 0, 245, 168, 14, 9, (14, 28))
    assert snapshot_distance(a, a) == 0.0


def test_find_similar_ranks_closer_first():
    query = GameSnapshot(28, 14, 0, 245, 168, 14, 9, (14, 28))
    near = Game(
        match_id="near",
        season="NFL:2024",
        date="2024-09-08",
        week=1,
        game_type="REG",
        home="A",
        away="B",
        home_score_ft=21,
        away_score_ft=3,
        home_yards_ft=250,
        away_yards_ft=170,
        home_fd_ft=14,
        away_fd_ft=9,
        scores=[ScoreEvent(14, "home", 7), ScoreEvent(28, "home", 7)],
        home_yards_by_min=[0] + [245] * 60,
        away_yards_by_min=[0] + [168] * 60,
        home_fd_by_min=[0] + [14] * 60,
        away_fd_by_min=[0] + [9] * 60,
    )
    far = Game(
        match_id="far",
        season="NFL:2024",
        date="2024-09-09",
        week=1,
        game_type="REG",
        home="C",
        away="D",
        home_score_ft=3,
        away_score_ft=41,
        home_yards_ft=80,
        away_yards_ft=480,
        home_fd_ft=4,
        away_fd_ft=28,
        scores=[
            ScoreEvent(8, "away", 7),
            ScoreEvent(16, "away", 7),
            ScoreEvent(24, "away", 7),
            ScoreEvent(40, "away", 7),
            ScoreEvent(52, "away", 7),
            ScoreEvent(58, "away", 6),
        ],
        home_yards_by_min=[0] + [80] * 60,
        away_yards_by_min=[0] + [480] * 60,
        home_fd_by_min=[0] + [4] * 60,
        away_fd_by_min=[0] + [28] * 60,
    )
    hits = find_similar(query, [far, near], limit=2)
    assert hits[0].match.match_id == "near"
    assert hits[0].distance < hits[1].distance


def test_matches_from_csv_and_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("EEEFUT_CACHE", str(tmp_path))
    matches = matches_from_csv(SAMPLE_GAMES, SAMPLE_STATS, "NFL:2024")
    assert len(matches) == 2
    assert matches[0].home == "Kansas City"
    assert matches[0].away == "Baltimore"
    assert matches[0].home_yards_ft == 389
    assert matches[0].home_fd_ft == 24
    tl = build_timelines("x", 27, 20, 389, 341, 24, 20)
    assert sum(e.points for e in tl["scores"] if e.team == "home") == 27
    assert tl["home_yards_by_min"][60] == 389
    save_season("NFL:2024", matches)
    loaded = load_season("NFL:2024")
    assert len(loaded) == 2
    assert loaded[0].snapshot_at(60).home_yards == matches[0].home_yards_ft


def test_cache_json_helpers(tmp_path, monkeypatch):
    monkeypatch.setenv("EEEFUT_CACHE", str(tmp_path))
    root = cache_root()
    assert root == tmp_path
    path = root / "hello.json"
    write_json(path, {"ok": True})
    assert read_json(path) == {"ok": True}
    assert path.read_text().endswith("\n")


def test_dashboard_preset_similar_api(tmp_path, monkeypatch):
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from eeefut.dashboard import DashboardState, make_handler

    monkeypatch.setenv("EEEFUT_CACHE", str(tmp_path))
    current = inject_chiefs_preset(matches_from_csv(SAMPLE_GAMES, SAMPLE_STATS, "NFL:2025"), "NFL:2025")
    history = matches_from_csv(SAMPLE_GAMES, SAMPLE_STATS, "NFL:2024")
    save_season("NFL:2025", current)
    save_season("NFL:2024", history)

    state = DashboardState("NFL:2025")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        base = f"http://127.0.0.1:{port}"
        meta = json.loads(urllib.request.urlopen(base + "/api/meta", timeout=5).read())
        assert meta["season"] == "NFL:2025"
        assert meta["chiefs_preset_id"]
        assert meta["history_count"] == 2
        games = json.loads(urllib.request.urlopen(base + "/api/matches", timeout=5).read())
        assert any(g["is_preset"] for g in games["matches"])
        pid = meta["chiefs_preset_id"]
        snap = json.loads(
            urllib.request.urlopen(f"{base}/api/snapshot?match_id={pid}&minute=28", timeout=5).read()
        )
        assert snap["label"] == "14'/28' · 245/14 vs 168/9"
        assert snap["clock"] == "Q2 2:00"
        sim = json.loads(
            urllib.request.urlopen(f"{base}/api/similar?match_id={pid}&minute=28", timeout=5).read()
        )
        assert snap["snapshot"]["home_score"] == 14
        assert len(sim["hits"]) >= 1
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "eeefut" in html
        assert "Chiefs 28" in html
        health = urllib.request.urlopen(base + "/health", timeout=5).read()
        assert health == b"ok\n"
        head_req = urllib.request.Request(base + "/health", method="HEAD")
        with urllib.request.urlopen(head_req, timeout=5) as resp:
            assert resp.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


LIVE_SCOREBOARD = {
    "season": {"type": 2, "year": 2026},
    "week": {"number": 1},
    "events": [
        {
            "id": "401",
            "name": "Miami Dolphins at Las Vegas Raiders",
            "shortName": "MIA @ LV",
            "date": "2026-09-13T20:25Z",
            "competitions": [
                {
                    "id": "401",
                    "venue": {"fullName": "Allegiant Stadium"},
                    "broadcasts": [{"market": "national", "names": ["FOX"]}],
                    "status": {
                        "clock": 120.0,
                        "displayClock": "2:00",
                        "period": 2,
                        "type": {"state": "in", "name": "STATUS_IN_PROGRESS", "completed": False, "shortDetail": "2:00 - 2nd"},
                    },
                    "competitors": [
                        {
                            "homeAway": "home",
                            "score": "14",
                            "team": {"id": "13", "abbreviation": "LV", "shortDisplayName": "Raiders", "displayName": "Las Vegas Raiders", "color": "000000", "logo": "https://x/lv.png"},
                            "records": [{"type": "total", "summary": "1-0"}],
                            "linescores": [{"value": 7.0}, {"value": 7.0}],
                        },
                        {
                            "homeAway": "away",
                            "score": "3",
                            "team": {"id": "15", "abbreviation": "MIA", "shortDisplayName": "Dolphins", "displayName": "Miami Dolphins", "color": "008e97", "logo": "https://x/mia.png"},
                            "records": [{"type": "total", "summary": "0-1"}],
                            "linescores": [{"value": 0.0}, {"value": 3.0}],
                        },
                    ],
                    "situation": {
                        "down": 3,
                        "distance": 10,
                        "yardLine": 31,
                        "possession": "13",
                        "downDistanceText": "3rd & 10 at LV 31",
                        "shortDownDistanceText": "3rd & 10",
                        "possessionText": "LV 31",
                        "isRedZone": False,
                        "homeTimeouts": 3,
                        "awayTimeouts": 2,
                        "lastPlay": {
                            "type": {"text": "Pass Incompletion"},
                            "text": "(Shotgun) K.Cousins pass incomplete deep right to J.Nailor.",
                            "team": {"id": "13"},
                            "scoreValue": 0,
                            "statYardage": 0,
                            "start": {"yardLine": 31},
                            "end": {"yardLine": 31},
                            "drive": {"description": "2 plays, 0 yards, 0:07"},
                            "probability": {"homeWinPercentage": 0.9, "awayWinPercentage": 0.1},
                        },
                    },
                    "leaders": [
                        {
                            "name": "passingYards",
                            "leaders": [
                                {"displayValue": "11/17, 157 YDS", "team": {"id": "15"}, "athlete": {"shortName": "M. Willis", "position": {"abbreviation": "QB"}}}
                            ],
                        }
                    ],
                }
            ],
        },
        {
            "id": "402",
            "shortName": "TB @ CIN",
            "date": "2026-09-13T17:00Z",
            "competitions": [
                {
                    "status": {"clock": 0.0, "displayClock": "0:00", "period": 4, "type": {"state": "post", "completed": True, "shortDetail": "Final"}},
                    "competitors": [
                        {"homeAway": "home", "score": "33", "winner": True, "team": {"id": "4", "abbreviation": "CIN"}},
                        {"homeAway": "away", "score": "27", "winner": False, "team": {"id": "27", "abbreviation": "TB"}},
                    ],
                }
            ],
        },
        {
            "id": "403",
            "shortName": "DAL @ NYG",
            "date": "2026-09-14T00:20Z",
            "competitions": [
                {
                    "status": {"clock": 0.0, "displayClock": "0:00", "period": 0, "type": {"state": "pre", "completed": False, "shortDetail": "9/13 - 8:20 PM EDT"}},
                    "competitors": [
                        {"homeAway": "home", "score": "0", "team": {"id": "19", "abbreviation": "NYG"}},
                        {"homeAway": "away", "score": "0", "team": {"id": "6", "abbreviation": "DAL"}},
                    ],
                }
            ],
        },
    ],
}

LIVE_SUMMARY_401 = {
    "boxscore": {
        "teams": [
            {
                "team": {"id": "15", "abbreviation": "MIA"},
                "homeAway": "away",
                "statistics": [
                    {"name": "firstDowns", "displayValue": "9"},
                    {"name": "totalYards", "displayValue": "168"},
                    {"name": "netPassingYards", "displayValue": "120"},
                    {"name": "rushingYards", "displayValue": "48"},
                    {"name": "turnovers", "displayValue": "1"},
                    {"name": "possessionTime", "displayValue": "12:10"},
                ],
            },
            {
                "team": {"id": "13", "abbreviation": "LV"},
                "homeAway": "home",
                "statistics": [
                    {"name": "firstDowns", "displayValue": "14"},
                    {"name": "totalYards", "displayValue": "245"},
                    {"name": "netPassingYards", "displayValue": "150"},
                    {"name": "rushingYards", "displayValue": "95"},
                    {"name": "turnovers", "displayValue": "0"},
                    {"name": "possessionTime", "displayValue": "17:50"},
                ],
            },
        ]
    },
    "scoringPlays": [
        {"period": {"number": 1}, "clock": {"value": 60.0}, "team": {"id": "13"}},
        {"period": {"number": 2}, "clock": {"value": 120.0}, "team": {"id": "13"}},
        {"period": {"number": 2}, "clock": {"value": 400.0}, "team": {"id": "15"}},
    ],
    "leaders": [
        {
            "team": {"id": "13"},
            "leaders": [
                {"name": "rushingYards", "leaders": [{"displayValue": "17 CAR, 102 YDS", "athlete": {"shortName": "A. Jeanty", "position": {"abbreviation": "RB"}}}]},
                {"name": "sacks", "leaders": [{"displayValue": "1", "athlete": {"shortName": "N. Dean"}}]},
            ],
        }
    ],
}


def _fake_live_fetch(calls: list[str]):
    def fetch(url: str) -> dict:
        calls.append(url)
        if url.endswith("/scoreboard"):
            return LIVE_SCOREBOARD
        if "event=401" in url:
            return LIVE_SUMMARY_401
        if "event=402" in url:
            return {"boxscore": {"teams": []}, "scoringPlays": []}
        raise RuntimeError(f"unexpected {url}")

    return fetch


def test_live_elapsed_minute_mapping():
    from eeefut.live import elapsed_minute

    assert elapsed_minute(1, 900) == 1
    assert elapsed_minute(2, 120) == 28  # Q2 2:00
    assert elapsed_minute(2, 0) == 30  # halftime
    assert elapsed_minute(4, 899) == 46
    assert elapsed_minute(4, 0) == 60
    assert elapsed_minute(5, 300) == 60  # OT clamps


def test_live_normalize_scoreboard_orders_live_first():
    from eeefut.live import normalize_scoreboard

    board = normalize_scoreboard(LIVE_SCOREBOARD)
    assert board["season"] == 2026
    assert board["week"] == 1
    assert [g["state"] for g in board["games"]] == ["in", "pre", "post"]

    live = board["games"][0]
    assert live["short_name"] == "MIA @ LV"
    assert live["period_label"] == "Q2 2:00"
    assert live["minute"] == 28
    assert live["broadcast"] == "FOX"
    assert live["home"]["abbr"] == "LV"
    assert live["home"]["record"] == "1-0"
    assert live["home"]["linescores"] == [7, 7]
    assert live["away"]["leaders"]["passing"]["name"] == "M. Willis"
    assert "passing" not in live["home"]["leaders"]

    sit = live["situation"]
    assert sit["possession"] == "home"
    assert sit["yard_line"] == 31
    assert sit["down"] == 3 and sit["distance"] == 10
    assert sit["away_timeouts"] == 2
    assert sit["win_prob"] == {"home": 0.9, "away": 0.1}
    assert sit["last_play"]["type"] == "Pass Incompletion"
    assert sit["last_play"]["team"] == "home"
    assert "K.Cousins" in sit["last_play"]["text"]

    final = board["games"][2]
    assert final["period_label"] == "Final"
    assert final["home"]["winner"] and not final["away"]["winner"]
    assert final["situation"] is None
    assert final["minute"] == 60

    upcoming = board["games"][1]
    assert upcoming["minute"] is None
    assert upcoming["period_label"] == "9/13 - 8:20 PM EDT"


def test_live_apply_summary_and_snapshot():
    from eeefut.live import apply_summary, attach_snapshot, normalize_scoreboard

    game = normalize_scoreboard(LIVE_SCOREBOARD)["games"][0]
    apply_summary(game, LIVE_SUMMARY_401)
    attach_snapshot(game)

    assert game["home"]["stats"]["total_yards"] == "245"
    assert game["home"]["stats"]["first_downs"] == "14"
    assert game["away"]["stats"]["possession"] == "12:10"
    assert game["scoring_minutes"] == [14, 24, 28]
    assert game["home"]["leaders"]["rushing"]["name"] == "A. Jeanty"
    assert "sacks" not in game["home"]["leaders"]

    snap = game["snapshot"]
    assert snap["has_box"] is True
    assert snap["minute"] == 28
    assert snap["home_score"] == 14 and snap["away_score"] == 3
    assert snap["label"] == "14'/24'/28' · 245/14 vs 168/9"
    assert snap["clock"] == "Q2 2:00"


def test_live_feed_caches_and_falls_back_to_stale(monkeypatch):
    from eeefut import live as live_mod
    from eeefut.live import LiveFeed

    calls: list[str] = []
    feed = LiveFeed(_fake_live_fetch(calls), ttl=100)
    board = feed.get()
    assert board["counts"] == {"live": 1, "final": 1, "upcoming": 1}
    # scoreboard + summaries for the in-progress and final game only (no fetch for pre)
    assert sum(u.endswith("/scoreboard") for u in calls) == 1
    assert sum("event=" in u for u in calls) == 2

    feed.get()
    assert sum(u.endswith("/scoreboard") for u in calls) == 1  # served from cache

    monkeypatch.setattr(live_mod, "FORCE_MIN_INTERVAL", 0.0)
    feed.get(force=True)
    assert sum(u.endswith("/scoreboard") for u in calls) == 2

    def boom(url: str) -> dict:
        raise RuntimeError("espn down")

    feed._fetch = boom  # noqa: SLF001
    feed._fetched_at = 0.0  # noqa: SLF001 - expire cache
    stale = feed.get()
    assert stale["stale"] is True
    assert "espn down" in stale["error"]
    assert stale["counts"]["live"] == 1

    empty = LiveFeed(boom)
    try:
        empty.get()
    except RuntimeError as exc:
        assert "espn down" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected first-fetch failure to raise")


def test_dashboard_live_api(tmp_path, monkeypatch):
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from eeefut.dashboard import DashboardState, make_handler
    from eeefut.live import LiveFeed

    monkeypatch.setenv("EEEFUT_CACHE", str(tmp_path))
    save_season("NFL:2025", inject_chiefs_preset([], "NFL:2025"))

    state = DashboardState("NFL:2025", live=LiveFeed(_fake_live_fetch([])))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        board = json.loads(urllib.request.urlopen(base + "/api/live", timeout=5).read())
        assert board["week"] == 1
        assert len(board["games"]) == 3
        live = board["games"][0]
        assert live["situation"]["last_play"]["type"] == "Pass Incompletion"
        assert live["snapshot"]["label"] == "14'/24'/28' · 245/14 vs 168/9"
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert 'data-tab="live"' in html
        assert 'id="liveGrid"' in html

        def boom(url: str) -> dict:
            raise RuntimeError("espn down")

        broken = DashboardState("NFL:2025", live=LiveFeed(boom))
        httpd2 = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(broken))
        threading.Thread(target=httpd2.serve_forever, daemon=True).start()
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{httpd2.server_address[1]}/api/live", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 502
            assert "espn down" in json.loads(exc.read())["error"]
        else:  # pragma: no cover
            raise AssertionError("expected 502")
        finally:
            httpd2.shutdown()
            httpd2.server_close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _summary_fixture(event_id: str, home: tuple[str, str, int], away: tuple[str, str, int], state: str = "post") -> dict:
    """Compact ESPN-shaped summary: header, boxscore (teams + players), drives with plays."""
    (hid, habbr, hscore), (aid, aabbr, ascore) = home, away
    play = lambda pid, text, ptype, yards, down=1, dist=10, tid=hid, scoring=False, hs=0, as_=0: {  # noqa: E731
        "id": pid,
        "sequenceNumber": pid[-3:],
        "type": {"text": ptype},
        "text": text,
        "period": {"number": 1},
        "clock": {"displayValue": "10:00"},
        "start": {"down": down, "distance": dist, "yardLine": 30, "yardsToEndzone": 70, "downDistanceText": f"{down} & {dist}", "team": {"id": tid}},
        "end": {"yardLine": 30 + yards},
        "statYardage": yards,
        "scoringPlay": scoring,
        "scoreValue": 7 if scoring else 0,
        "homeScore": hs,
        "awayScore": as_,
        "participants": [{"athlete": {"id": f"{event_id}p1", "shortName": "Q. Back", "position": {"abbreviation": "QB"}, "team": {"abbreviation": habbr}}}],
    }
    return {
        "header": {
            "id": event_id,
            "season": {"year": 2026, "type": 2},
            "week": 1,
            "competitions": [
                {
                    "id": event_id,
                    "date": "2026-09-13T17:00Z",
                    "status": {"type": {"state": state, "completed": state == "post", "shortDetail": "Final" if state == "post" else "Q2 5:00"}},
                    "competitors": [
                        {"homeAway": "home", "score": str(hscore), "winner": hscore > ascore, "team": {"id": hid, "abbreviation": habbr, "nickname": habbr, "displayName": f"{habbr} Team", "logos": [{"href": f"https://x/{habbr}.png"}], "color": "112233"}, "record": [{"type": "total", "summary": "1-0"}]},
                        {"homeAway": "away", "score": str(ascore), "winner": ascore > hscore, "team": {"id": aid, "abbreviation": aabbr, "nickname": aabbr, "displayName": f"{aabbr} Team", "logos": [{"href": f"https://x/{aabbr}.png"}]}},
                    ],
                }
            ],
        },
        "gameInfo": {"venue": {"fullName": "Test Field"}},
        "boxscore": {
            "teams": [
                {"team": {"id": hid, "abbreviation": habbr}, "homeAway": "home", "statistics": [
                    {"name": "totalYards", "displayValue": "400"}, {"name": "rushingYards", "displayValue": "150"},
                    {"name": "netPassingYards", "displayValue": "250"}, {"name": "rushingAttempts", "displayValue": "30"},
                    {"name": "completionAttempts", "displayValue": "20/30"}, {"name": "firstDowns", "displayValue": "22"},
                    {"name": "thirdDownEff", "displayValue": "6-12"}, {"name": "redZoneAttempts", "displayValue": "2-3"},
                    {"name": "turnovers", "displayValue": "1"}, {"name": "sacksYardsLost", "displayValue": "2-15"},
                    {"name": "totalOffensivePlays", "displayValue": "60"},
                ]},
                {"team": {"id": aid, "abbreviation": aabbr}, "homeAway": "away", "statistics": [
                    {"name": "totalYards", "displayValue": "300"}, {"name": "rushingYards", "displayValue": "80"},
                    {"name": "netPassingYards", "displayValue": "220"}, {"name": "rushingAttempts", "displayValue": "20"},
                    {"name": "completionAttempts", "displayValue": "25/40"}, {"name": "firstDowns", "displayValue": "18"},
                    {"name": "thirdDownEff", "displayValue": "4-12"}, {"name": "redZoneAttempts", "displayValue": "1-2"},
                    {"name": "turnovers", "displayValue": "2"}, {"name": "sacksYardsLost", "displayValue": "1-8"},
                    {"name": "totalOffensivePlays", "displayValue": "60"},
                ]},
            ],
            "players": [
                {"team": {"id": hid, "abbreviation": habbr}, "statistics": [
                    {"name": "passing", "labels": ["C/ATT", "YDS", "AVG", "TD", "INT"], "athletes": [{"athlete": {"id": f"{event_id}p1", "displayName": "Quarter Back", "shortName": "Q. Back", "jersey": "9"}, "stats": ["20/30", "250", "8.3", "2", "0"]}]},
                    {"name": "rushing", "labels": ["CAR", "YDS", "AVG", "TD", "LONG"], "athletes": [{"athlete": {"id": f"{event_id}p2", "displayName": "Run Ner", "shortName": "R. Ner"}, "stats": ["18", "110", "6.1", "1", "40"]}]},
                    {"name": "defensive", "labels": ["TOT", "SOLO", "SACKS", "TFL", "PD", "QB HTS", "TD"], "athletes": [{"athlete": {"id": f"{event_id}p3", "displayName": "Corner Back", "shortName": "C. Back"}, "stats": ["6", "5", "0", "1", "3", "0", "0"]}]},
                ]},
                {"team": {"id": aid, "abbreviation": aabbr}, "statistics": [
                    {"name": "receiving", "labels": ["REC", "YDS", "AVG", "TD", "LONG", "TGTS"], "athletes": [{"athlete": {"id": f"{event_id}p4", "displayName": "Wide Out", "shortName": "W. Out"}, "stats": ["7", "120", "17.1", "1", "45", "9"]}]},
                ]},
            ],
        },
        "drives": {
            "previous": [
                {
                    "id": f"{event_id}d1", "team": {"id": hid, "abbreviation": habbr}, "description": "4 plays, 70 yards, 2:10",
                    "start": {"period": {"number": 1}, "clock": {"displayValue": "12:00"}, "yardLine": 30, "text": f"{habbr} 30"},
                    "end": {"period": {"number": 1}, "clock": {"displayValue": "9:50"}, "yardLine": 100, "text": "End zone"},
                    "timeElapsed": {"displayValue": "2:10"}, "yards": 70, "isScore": True, "offensivePlays": 4, "result": "TD",
                    "plays": [
                        play("1001", "(Shotgun) Q.Back pass deep right to W.Rec for 35 yards", "Pass Reception", 35),
                        play("1002", "R.Ner left end for 8 yards", "Rush", 8, 1, 10),
                        play("1003", "Q.Back pass short middle to T.End for 22 yards", "Pass Reception", 22, 2, 2),
                        play("1004", "R.Ner up the middle for 5 yards, TOUCHDOWN", "Rushing Touchdown", 5, 1, 5, scoring=True, hs=7),
                    ],
                },
                {
                    "id": f"{event_id}d2", "team": {"id": aid, "abbreviation": aabbr}, "description": "3 plays, 2 yards, 1:30",
                    "start": {"period": {"number": 1}, "clock": {"displayValue": "9:50"}, "yardLine": 75, "text": f"{aabbr} 25"},
                    "end": {"period": {"number": 1}, "clock": {"displayValue": "8:20"}, "yardLine": 73, "text": f"{aabbr} 27"},
                    "timeElapsed": {"displayValue": "1:30"}, "yards": 2, "isScore": False, "offensivePlays": 3, "result": "PUNT",
                    "plays": [
                        play("2001", "A.Way right guard for 2 yards", "Rush", 2, 1, 10, tid=aid, hs=7),
                        play("2002", "A.Way pass incomplete short left to X.Y", "Pass Incompletion", 0, 2, 8, tid=aid, hs=7),
                        play("2003", "(Shotgun) A.Way sacked at 27 for -3 yards", "Sack", -3, 3, 8, tid=aid, hs=7),
                        play("2004", "P.Unter punts 45 yards", "Punt", 45, 4, 11, tid=aid, hs=7),
                        play("2005", "Timeout #1 by HOME", "Timeout", 0, tid=aid, hs=7),
                    ],
                },
            ]
        },
        "scoringPlays": [{"team": {"abbreviation": habbr}, "period": {"number": 1}, "clock": {"displayValue": "9:50"}, "scoringType": {"abbreviation": "TD"}, "text": "R.Ner 5 yd run", "homeScore": 7, "awayScore": 0}],
    }


def test_classify_play_tags():
    from eeefut.store import classify_play

    deep = classify_play("Pass Reception", "Q.Back pass deep right to W.Rec for 35 yards")
    assert deep["kind"] == "pass" and deep["depth"] == "deep" and deep["direction"] == "right"
    inc = classify_play("Pass Incompletion", "A.Way pass incomplete short left to X.Y")
    assert inc["kind"] == "pass" and inc["depth"] == "short" and inc["direction"] == "left"
    rush = classify_play("Rush", "R.Ner left end for 8 yards")
    assert rush["kind"] == "rush" and rush["direction"] == "left"
    middle = classify_play("Rushing Touchdown", "R.Ner up the middle for 5 yards, TOUCHDOWN")
    assert middle["kind"] == "rush" and middle["direction"] == "middle" and middle["touchdown"]
    sack = classify_play("Sack", "(Shotgun) A.Way sacked at 27 for -3 yards")
    assert sack["kind"] == "pass" and sack["sack"]
    assert classify_play("Punt", "P.Unter punts 45 yards")["kind"] == "special"
    assert classify_play("Timeout", "Timeout #1")["kind"] == "admin"
    assert classify_play("Penalty", "PENALTY on X, False Start")["kind"] == "penalty"
    assert classify_play("Fumble Recovery (Opponent)", "R.Ner right tackle FUMBLES, recovered by DEF")["turnover"]
    assert classify_play("Rush", "Q.Back kneels to the 30 for -1 yards")["kind"] == "other"


def test_store_roundtrip_and_record_shape(tmp_path, monkeypatch):
    from eeefut.store import GameStore, build_game_record

    monkeypatch.setenv("EEEFUT_CACHE", str(tmp_path))
    rec = build_game_record(_summary_fixture("900", ("1", "HOM", 27), ("2", "AWY", 10)))
    assert rec["id"] == "900" and rec["season"] == 2026 and rec["week"] == 1 and rec["state"] == "post"
    assert rec["home"]["abbr"] == "HOM" and rec["home"]["winner"] and rec["home"]["record"] == "1-0"
    assert rec["team_stats"]["home"]["totalYards"] == "400"
    assert len(rec["drives"]) == 2
    d1, d2 = rec["drives"]
    assert d1["side"] == "home" and d1["result"] == "TD" and d1["points"] == 7
    assert [p["kind"] for p in d1["plays"]] == ["pass", "rush", "pass", "rush"]
    assert d1["plays"][0]["explosive"] and d1["plays"][0]["depth"] == "deep"
    assert d1["plays"][0]["participants"][0]["position"] == "QB"
    assert d2["side"] == "away" and [p["kind"] for p in d2["plays"]] == ["rush", "pass", "pass", "special", "admin"]
    assert {p["id"] for p in rec["players"]} == {"900p1", "900p2", "900p3", "900p4"}
    p1 = next(p for p in rec["players"] if p["id"] == "900p1")
    assert p1["team"] == "HOM" and p1["lines"]["passing"]["YDS"] == "250"

    store = GameStore()
    store.save(rec)
    assert store.count(2026) == 1
    assert store.state_of(2026, "900") == "post"
    assert store.find("900")["home"]["abbr"] == "HOM"
    store.save_rosters(2026, {"900p3": {"position": "CB"}}, [{"id": "1", "abbr": "HOM"}])
    assert store.game_ids(2026) == {"900"}  # rosters file is not a game
    assert store.load_rosters(2026)["900p3"]["position"] == "CB"


def test_team_table_ranks_and_detail(tmp_path, monkeypatch):
    from eeefut.store import build_game_record
    from eeefut.teams import build_player_table, build_team_table, team_detail

    games = [
        build_game_record(_summary_fixture("901", ("1", "HOM", 27), ("2", "AWY", 10))),
        build_game_record(_summary_fixture("902", ("3", "THR", 20), ("4", "FOR", 24))),
        build_game_record(_summary_fixture("903", ("1", "HOM", 14), ("4", "FOR", 14), state="in")),
    ]
    table = build_team_table(games)
    by = {t["abbr"]: t for t in table}
    assert set(by) == {"HOM", "AWY", "THR", "FOR"}
    hom = by["HOM"]
    assert hom["record"] == "1-0" and hom["games"] == 1  # in-progress game not counted
    assert hom["live_game_id"] == "903"
    assert hom["offense"]["yards_pg"] == 400 and hom["defense"]["yards_pg"] == 300
    assert hom["offense"]["rush_rate"] == 50.0  # 30 rush att vs 30 pass att
    assert hom["offense"]["deep_rate"] == 50.0  # 1 deep + 1 short tagged
    assert hom["offense"]["points_per_drive"] == 7.0 and hom["offense"]["td_drive_pct"] == 100.0
    assert hom["defense"]["three_and_out_pct"] == 100.0  # forced AWY 3-and-out
    # Home teams both had 400 yds; away teams 300 -> ranks tie at 1 and 3
    assert hom["ranks"]["offense"]["yards_pg"] == 1 and by["AWY"]["ranks"]["offense"]["yards_pg"] == 3
    # Defense: fewer yards allowed is better -> home sides (allowed 300) rank 1
    assert hom["ranks"]["defense"]["yards_pg"] == 1 and by["AWY"]["ranks"]["defense"]["yards_pg"] == 3
    # Turnovers: offense lower is better (HOM 1 vs AWY 2); defense takeaways higher is better
    assert hom["ranks"]["offense"]["turnovers_pg"] == 1
    assert hom["ranks"]["defense"]["turnovers_pg"] == 1
    assert table[0]["abbr"] in ("HOM", "FOR")  # winners sort first

    players = build_player_table(games, {"901p3": {"position": "CB"}})
    p3 = next(p for p in players if p["id"] == "901p3")
    assert p3["position"] == "CB" and p3["games"] == 1 and p3["totals"]["pass_def"] == 3
    p1 = next(p for p in players if p["id"] == "901p1")
    assert p1["totals"]["pass_yds"] == 250 and p1["totals"]["completions"] == 20 and p1["totals"]["pass_att"] == 30

    detail = team_detail(hom, games, players)
    assert detail["offense_mix"]["rush_pct"] == 50 and detail["offense_mix"]["deep"] == 1
    assert detail["offense_mix"]["rush_dir"] == {"left": 1, "middle": 1, "right": 0}
    assert detail["defense_mix"]["sacks"] == 1
    assert [g["result"] for g in detail["game_log"]] == ["W", ""]
    assert detail["top_players"]["passing"][0]["name"] == "Quarter Back"
    assert detail["top_players"]["pass_defense"][0]["position"] == "CB"


def test_ingestor_backfills_and_skips_stored(tmp_path, monkeypatch):
    from eeefut.ingest import Ingestor
    from eeefut.store import GameStore

    monkeypatch.setenv("EEEFUT_CACHE", str(tmp_path))
    calls: list[str] = []

    def fetch(url: str) -> dict:
        calls.append(url)
        if url.endswith("/teams"):
            return {"sports": [{"leagues": [{"teams": [{"team": {"id": "1", "abbreviation": "HOM", "nickname": "Homers", "displayName": "Home Team", "logos": [{"href": "l"}]}}]}]}]}
        if "/roster" in url:
            return {"athletes": [{"position": "defense", "items": [{"id": "p3", "displayName": "Corner Back", "position": {"abbreviation": "CB"}, "jersey": "24"}]}]}
        if "week=1" in url:
            return {"events": [
                {"id": "901", "competitions": [{"status": {"type": {"state": "post"}}}]},
                {"id": "903", "competitions": [{"status": {"type": {"state": "in"}}}]},
            ]}
        if "week=" in url:
            return {"events": [{"id": "999", "competitions": [{"status": {"type": {"state": "pre"}}}]}]}
        if "event=901" in url:
            return _summary_fixture("901", ("1", "HOM", 27), ("2", "AWY", 10))
        raise RuntimeError(f"unexpected {url}")

    store = GameStore()
    ing = Ingestor(store, fetch)
    status = ing.run(2026)
    assert status["fetched"] == 1 and status["skipped"] == 0 and not status["errors"]
    assert status["weeks_done"] == 3  # week 1 + two idle future weeks, then stop
    assert store.count(2026) == 1
    assert store.load_rosters(2026)["p3"]["position"] == "CB"
    assert store.load_teams(2026)[0]["abbr"] == "HOM"

    again = ing.run(2026)
    assert again["fetched"] == 0 and again["skipped"] == 1
    assert sum("/roster" in u for u in calls) == 1  # rosters cached


def test_dashboard_teams_api(tmp_path, monkeypatch):
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from eeefut.dashboard import DashboardState, make_handler
    from eeefut.ingest import Ingestor
    from eeefut.live import LiveFeed
    from eeefut.store import GameStore

    monkeypatch.setenv("EEEFUT_CACHE", str(tmp_path))
    save_season("NFL:2025", inject_chiefs_preset([], "NFL:2025"))
    store = GameStore()
    store.save_summary(_summary_fixture("901", ("1", "HOM", 27), ("2", "AWY", 10)))

    def fetch(url: str) -> dict:
        if "week=1" in url:
            return {"events": [{"id": "902", "competitions": [{"status": {"type": {"state": "post"}}}]}]}
        if "week=" in url:
            return {"events": []}
        if "event=902" in url:
            return _summary_fixture("902", ("3", "THR", 20), ("2", "AWY", 24))
        if url.endswith("/teams"):
            return {"sports": []}
        raise RuntimeError(url)

    state = DashboardState("NFL:2025", live=LiveFeed(_fake_live_fetch([])), store=store, ingestor=Ingestor(store, fetch))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        teams = json.loads(urllib.request.urlopen(base + "/api/teams", timeout=5).read())
        assert teams["season"] == 2026 and teams["completed"] == 1 and teams["players"] == 4
        assert {t["abbr"] for t in teams["teams"]} == {"HOM", "AWY"}
        assert teams["metrics"][0]["key"] == "points_pg"

        detail = json.loads(urllib.request.urlopen(base + "/api/teams/hom", timeout=5).read())
        assert detail["abbr"] == "HOM" and detail["game_log"][0]["opponent"] == "AWY"
        assert detail["offense_mix"]["deep"] == 1

        game = json.loads(urllib.request.urlopen(base + "/api/games/901", timeout=5).read())
        assert len(game["drives"]) == 2 and game["drives"][0]["plays"][0]["depth"] == "deep"

        req = urllib.request.Request(base + "/api/ingest", method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 202
        state.ingestor.wait(10)
        status = json.loads(urllib.request.urlopen(base + "/api/ingest", timeout=5).read())
        assert status["running"] is False and status["fetched"] == 1

        teams2 = json.loads(urllib.request.urlopen(base + "/api/teams", timeout=5).read())
        assert teams2["completed"] == 2
        awy = next(t for t in teams2["teams"] if t["abbr"] == "AWY")
        assert awy["record"] == "1-1" and awy["games"] == 2

        html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert 'data-tab="teams"' in html and 'id="teamDetail"' in html
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_live_feed_persists_summaries_to_store(tmp_path, monkeypatch):
    from eeefut.dashboard import DashboardState
    from eeefut.live import LiveFeed
    from eeefut.store import GameStore

    monkeypatch.setenv("EEEFUT_CACHE", str(tmp_path))
    save_season("NFL:2025", inject_chiefs_preset([], "NFL:2025"))
    store = GameStore()
    state = DashboardState("NFL:2025", live=LiveFeed(lambda url: {}), store=store)

    live_game = {"id": "401", "state": "in"}
    state._persist_live_summary(live_game, _summary_fixture("401", ("1", "HOM", 7), ("2", "AWY", 0), state="in"))  # noqa: SLF001
    assert store.state_of(2026, "401") == "in"
    state._persist_live_summary({"id": "401", "state": "post"}, _summary_fixture("401", ("1", "HOM", 27), ("2", "AWY", 10)))  # noqa: SLF001
    assert store.state_of(2026, "401") == "post"
    assert store.find("401")["home"]["score"] == 27
    state._persist_live_summary({"id": "402", "state": "pre"}, {})  # noqa: SLF001
    assert store.count(2026) == 1


def test_cli_host_flag_defaults():
    from eeefut.cli import build_parser

    ns = build_parser().parse_args(["--dashboard", "--host", "0.0.0.0", "--port", "8082"])
    assert ns.host == "0.0.0.0"
    assert ns.port == 8082


def test_nginx_routes_port_80_to_dashboard_ports():
    from pathlib import Path

    conf = Path(__file__).resolve().parents[1] / "scripts" / "nginx-eeefut-dashboard.conf"
    text = conf.read_text()
    assert "listen 80 default_server" in text
    assert "server 127.0.0.1:8082" in text
    assert "server_name eeefut.com www.eeefut.com _;" in text
    assert "acme-challenge" in text
    assert "proxy_pass http://eeefut_dashboard" in text
    assert "8081" not in text
    assert "/eeesoc/" not in text
    assert "/julia/" not in text


def test_nginx_https_server_name_and_443():
    from pathlib import Path

    conf = Path(__file__).resolve().parents[1] / "scripts" / "nginx-eeefut-https.conf"
    text = conf.read_text()
    assert "listen 443 ssl" in text
    assert "server_name eeefut.com www.eeefut.com;" in text
    assert "ssl_certificate" in text
    assert "return 301 https://eeefut.com" in text


def test_install_nginx_script_starts_inactive_unit():
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "install-nginx-80.sh"
    text = script.read_text()
    assert "systemctl start nginx" in text
    assert "disable_nginx_default_80.py" in text


def test_disable_amazon_linux_padded_listen_80(tmp_path):
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    from disable_nginx_default_80 import disable_default_80

    # Stock Amazon Linux / RHEL nginx.conf uses many spaces before the port.
    al_conf = """
http {
    include /etc/nginx/conf.d/*.conf;

    server {
        listen       80;
        listen       [::]:80;
        server_name  _;
        root         /usr/share/nginx/html;
    }
}
"""
    patched, n = disable_default_80(al_conf)
    assert n == 1
    assert "eeefut: default :80 server disabled" in patched
    assert "#         listen       80;" in patched
    live = "\n".join(line for line in patched.splitlines() if not line.lstrip().startswith("#"))
    assert "listen" not in live

    again, n2 = disable_default_80(patched)
    assert n2 == 0
