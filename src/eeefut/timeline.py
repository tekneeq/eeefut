"""Build minute-level yard / score timelines from full-game box scores."""

from __future__ import annotations

import hashlib
from typing import Iterable

from eeefut.models import GAME_LENGTH, ScoreEvent

SERIES_LEN = GAME_LENGTH + 1  # index 0 unused


def _seed(*parts: object) -> int:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return int(h[:8], 16)


def _rng(seed: int) -> Iterable[float]:
    """Tiny deterministic LCG yielding floats in [0, 1)."""
    x = seed % (2**31 - 1) or 1
    while True:
        x = (1103515245 * x + 12345) % (2**31)
        yield x / (2**31)


def decompose_score(points: int) -> list[int]:
    """Split a final score into typical NFL scoring chunks (TD / FG / safety)."""
    points = max(0, int(points))
    if points <= 0:
        return []
    n7, rem = divmod(points, 7)
    plays = [7] * n7
    if rem == 0:
        return plays
    if rem == 1:
        if plays:
            plays[-1] = 8  # 2-pt instead of PAT
        else:
            plays = [3, 3, 3, 2]  # unreachable for real NFL 1-point scores
        return plays
    if rem == 2:
        plays.append(2)
        return plays
    if rem == 3:
        plays.append(3)
        return plays
    if rem == 4:
        plays.extend([2, 2])
        return plays
    if rem == 5:
        plays.extend([3, 2])
        return plays
    if rem == 6:
        plays.append(6)
        return plays
    return plays


def place_scores(home_ft: int, away_ft: int, seed: int) -> list[ScoreEvent]:
    """Place scoring plays across 1–60 matching full-time totals."""
    rng = _rng(seed)
    events: list[ScoreEvent] = []
    used: set[int] = set()

    def slot(plays: list[int], team: str) -> None:
        for pts in plays:
            placed = False
            for _attempt in range(50):
                u = next(rng) ** 0.72
                minute = 1 + int(u * (GAME_LENGTH - 1))
                minute = max(1, min(GAME_LENGTH, minute))
                if minute not in used:
                    used.add(minute)
                    events.append(ScoreEvent(minute=minute, team=team, points=pts))
                    placed = True
                    break
            if not placed:
                for m in range(1, GAME_LENGTH + 1):
                    if m not in used:
                        used.add(m)
                        events.append(ScoreEvent(minute=m, team=team, points=pts))
                        break

    slot(decompose_score(home_ft), "home")
    slot(decompose_score(away_ft), "away")
    events.sort(key=lambda g: (g.minute, g.team))
    return events


def cumulative_yards(
    total_yards: int,
    first_downs: int,
    seed: int,
    length: int = SERIES_LEN,
) -> tuple[list[int], list[int]]:
    """
    Spread yards across minutes; first downs land with a subset of gain plays.
    Returns (yards_by_min, fd_by_min) length `length` (index 0 unused / zero).
    """
    total_yards = max(0, int(total_yards))
    first_downs = max(0, int(first_downs))
    yards = [0] * length
    fds = [0] * length
    if total_yards == 0 and first_downs == 0:
        return yards, fds

    n_plays = max(first_downs, min(90, max(8, total_yards // 6 or 1)))
    n_plays = max(n_plays, 1)
    rng = _rng(seed)
    raw = [next(rng) ** 0.65 for _ in range(n_plays)]
    s = sum(raw) or 1.0
    weights = [r / s for r in raw]

    chunks: list[int] = []
    allocated = 0
    for i, w in enumerate(weights):
        if i == n_plays - 1:
            chunks.append(max(0, total_yards - allocated))
        else:
            c = int(round(total_yards * w))
            chunks.append(max(0, c))
            allocated += chunks[-1]
    drift = total_yards - sum(chunks)
    chunks[-1] = max(0, chunks[-1] + drift)

    play_minutes: list[int] = []
    acc = 0.0
    for w in weights:
        acc += w
        minute = 1 + int(acc * (GAME_LENGTH - 1))
        minute = max(1, min(GAME_LENGTH, minute))
        play_minutes.append(minute)

    ordered = sorted(enumerate(play_minutes), key=lambda t: (t[1], t[0]))
    fd_idx = {i for i, _ in ordered[: min(first_downs, n_plays)]}

    by_min_yards = {m: 0 for m in range(1, GAME_LENGTH + 1)}
    by_min_fd = {m: 0 for m in range(1, GAME_LENGTH + 1)}
    for i, m in enumerate(play_minutes):
        by_min_yards[m] += chunks[i]
        if i in fd_idx:
            by_min_fd[m] += 1

    running_yards = 0
    running_fd = 0
    for m in range(1, GAME_LENGTH + 1):
        running_yards += by_min_yards[m]
        running_fd += by_min_fd[m]
        if m < length:
            yards[m] = running_yards
            fds[m] = running_fd
    yards[0] = 0
    fds[0] = 0
    return yards, fds


def build_timelines(
    match_id: str,
    home_ft: int,
    away_ft: int,
    home_yards: int,
    away_yards: int,
    home_fd: int,
    away_fd: int,
) -> dict:
    seed = _seed(match_id, home_ft, away_ft, home_yards, away_yards)
    scores = place_scores(home_ft, away_ft, seed)
    hy, hfd = cumulative_yards(home_yards, home_fd, seed ^ 0xA5)
    ay, afd = cumulative_yards(away_yards, away_fd, seed ^ 0x5A)
    return {
        "scores": scores,
        "home_yards_by_min": hy,
        "away_yards_by_min": ay,
        "home_fd_by_min": hfd,
        "away_fd_by_min": afd,
    }
