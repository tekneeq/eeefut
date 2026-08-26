"""Core game / snapshot models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

GAME_LENGTH = 60  # four 15-minute quarters


def clock_label(minute: int) -> str:
    """Elapsed game minute 1–60 → Qn remaining clock."""
    m = max(0, min(GAME_LENGTH, int(minute)))
    if m <= 0:
        return "Q1 15:00"
    if m >= GAME_LENGTH:
        return "Final"
    quarter = 1 + (m - 1) // 15
    into_q = (m - 1) % 15 + 1
    remain = 15 - into_q
    return f"Q{quarter} {remain}:00"


@dataclass(frozen=True)
class ScoreEvent:
    minute: int
    team: str  # "home" | "away"
    points: int = 7


@dataclass(frozen=True)
class GameSnapshot:
    """Frozen in-play state used for Similar lookalikes."""

    minute: int
    home_score: int
    away_score: int
    home_yards: int
    away_yards: int
    home_fd: int
    away_fd: int
    score_minutes: tuple[int, ...] = ()

    def clock(self) -> str:
        return clock_label(self.minute)

    def label(self) -> str:
        scores = "/".join(f"{m}'" for m in self.score_minutes) or "—"
        return (
            f"{scores} · {self.home_yards}/{self.home_fd} vs "
            f"{self.away_yards}/{self.away_fd}"
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["clock"] = self.clock()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameSnapshot:
        return cls(
            minute=int(data["minute"]),
            home_score=int(data["home_score"]),
            away_score=int(data["away_score"]),
            home_yards=int(data["home_yards"]),
            away_yards=int(data["away_yards"]),
            home_fd=int(data["home_fd"]),
            away_fd=int(data["away_fd"]),
            score_minutes=tuple(int(m) for m in data.get("score_minutes", ())),
        )


@dataclass
class Game:
    match_id: str
    season: str
    date: str
    week: int
    game_type: str
    home: str
    away: str
    home_score_ft: int
    away_score_ft: int
    home_yards_ft: int
    away_yards_ft: int
    home_fd_ft: int
    away_fd_ft: int
    scores: list[ScoreEvent] = field(default_factory=list)
    # Cumulative yards / first downs at each minute 1..60 (index 0 unused)
    home_yards_by_min: list[int] = field(default_factory=list)
    away_yards_by_min: list[int] = field(default_factory=list)
    home_fd_by_min: list[int] = field(default_factory=list)
    away_fd_by_min: list[int] = field(default_factory=list)

    def snapshot_at(self, minute: int) -> GameSnapshot:
        minute = max(1, min(GAME_LENGTH, int(minute)))
        hy = _at(self.home_yards_by_min, minute)
        ay = _at(self.away_yards_by_min, minute)
        hfd = _at(self.home_fd_by_min, minute)
        afd = _at(self.away_fd_by_min, minute)
        scored = [g for g in self.scores if g.minute <= minute]
        return GameSnapshot(
            minute=minute,
            home_score=sum(g.points for g in scored if g.team == "home"),
            away_score=sum(g.points for g in scored if g.team == "away"),
            home_yards=hy,
            away_yards=ay,
            home_fd=hfd,
            away_fd=afd,
            score_minutes=tuple(g.minute for g in scored),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "season": self.season,
            "date": self.date,
            "week": self.week,
            "game_type": self.game_type,
            "home": self.home,
            "away": self.away,
            "home_score_ft": self.home_score_ft,
            "away_score_ft": self.away_score_ft,
            "home_yards_ft": self.home_yards_ft,
            "away_yards_ft": self.away_yards_ft,
            "home_fd_ft": self.home_fd_ft,
            "away_fd_ft": self.away_fd_ft,
            "scores": [
                {"minute": g.minute, "team": g.team, "points": g.points} for g in self.scores
            ],
            "home_yards_by_min": self.home_yards_by_min,
            "away_yards_by_min": self.away_yards_by_min,
            "home_fd_by_min": self.home_fd_by_min,
            "away_fd_by_min": self.away_fd_by_min,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Game:
        scores = [
            ScoreEvent(
                minute=int(g["minute"]),
                team=str(g["team"]),
                points=int(g.get("points", 7)),
            )
            for g in data.get("scores", [])
        ]
        return cls(
            match_id=str(data["match_id"]),
            season=str(data["season"]),
            date=str(data["date"]),
            week=int(data.get("week", 0)),
            game_type=str(data.get("game_type", "REG")),
            home=str(data["home"]),
            away=str(data["away"]),
            home_score_ft=int(data["home_score_ft"]),
            away_score_ft=int(data["away_score_ft"]),
            home_yards_ft=int(data["home_yards_ft"]),
            away_yards_ft=int(data["away_yards_ft"]),
            home_fd_ft=int(data["home_fd_ft"]),
            away_fd_ft=int(data["away_fd_ft"]),
            scores=scores,
            home_yards_by_min=list(data.get("home_yards_by_min") or []),
            away_yards_by_min=list(data.get("away_yards_by_min") or []),
            home_fd_by_min=list(data.get("home_fd_by_min") or []),
            away_fd_by_min=list(data.get("away_fd_by_min") or []),
        )


def _at(series: list[int], minute: int) -> int:
    if not series:
        return 0
    if minute >= len(series):
        return series[-1]
    return series[minute]
