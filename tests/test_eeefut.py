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
    assert "proxy_pass http://eeefut_dashboard" in text
    assert "8081" not in text
    assert "8501" not in text
    assert "/eeesoc/" not in text
    assert "/julia/" not in text


def test_install_nginx_script_starts_inactive_unit():
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "install-nginx-80.sh"
    text = script.read_text()
    assert "systemctl start nginx" in text
    assert "nginx.conf" in text
    assert "eeefut: default :80 server disabled" in text
