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
