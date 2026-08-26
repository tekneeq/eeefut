"""Similar-game lookalike scoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from eeefut.models import Game, GameSnapshot


@dataclass(frozen=True)
class SimilarHit:
    match: Game
    snapshot: GameSnapshot
    distance: float
    score: float

    def to_dict(self) -> dict:
        return {
            "match_id": self.match.match_id,
            "season": self.match.season,
            "date": self.match.date,
            "week": self.match.week,
            "game_type": self.match.game_type,
            "home": self.match.home,
            "away": self.match.away,
            "distance": round(self.distance, 4),
            "score": round(self.score, 4),
            "snapshot": self.snapshot.to_dict(),
            "label": self.snapshot.label(),
            "clock": self.snapshot.clock(),
            "ft": f"{self.match.home_score_ft}-{self.match.away_score_ft}",
        }


def snapshot_distance(a: GameSnapshot, b: GameSnapshot) -> float:
    """Weighted L1 distance across scoreline, yards/FD, and scoring times."""
    score_term = abs(a.home_score - b.home_score) * 0.45 + abs(a.away_score - b.away_score) * 0.45
    yard_term = (
        abs(a.home_yards - b.home_yards) * 0.015
        + abs(a.away_yards - b.away_yards) * 0.015
        + abs(a.home_fd - b.home_fd) * 0.55
        + abs(a.away_fd - b.away_fd) * 0.55
    )
    minute_term = abs(a.minute - b.minute) * 0.05

    ga = list(a.score_minutes)
    gb = list(b.score_minutes)
    goal_term = abs(len(ga) - len(gb)) * 1.5
    for i in range(min(len(ga), len(gb))):
        goal_term += abs(ga[i] - gb[i]) * 0.08

    return score_term + yard_term + minute_term + goal_term


def find_similar(
    query: GameSnapshot,
    corpus: Iterable[Game],
    *,
    limit: int = 12,
    exclude_ids: set[str] | None = None,
) -> list[SimilarHit]:
    exclude_ids = exclude_ids or set()
    hits: list[SimilarHit] = []
    for match in corpus:
        if match.match_id in exclude_ids:
            continue
        snap = match.snapshot_at(query.minute)
        dist = snapshot_distance(query, snap)
        score = 1.0 / (1.0 + dist)
        hits.append(SimilarHit(match=match, snapshot=snap, distance=dist, score=score))
    hits.sort(key=lambda h: (h.distance, h.match.date))
    return hits[:limit]
