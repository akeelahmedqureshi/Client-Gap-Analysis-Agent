"""Transparent, configurable opportunity scoring.

The LLM is never asked "which feature matters most". Instead every opportunity
carries factor scores (0-5) and the priority is a weighted sum:

    score = Σ w_i · benefit_i  −  w_c · complexity  −  w_r · risk

Weights are configurable per run so an account team can tune them, and the
full breakdown is stored with every recommendation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from cip.core.schemas import Phase, ScoreBreakdown

BENEFIT_FACTORS = (
    "business_value",
    "user_impact",
    "market_demand",
    "competitive_gap",
    "revenue_potential",
    "strategic_alignment",
    "ai_opportunity",
    "technical_feasibility",
)
PENALTY_FACTORS = ("complexity", "risk")
ALL_FACTORS = BENEFIT_FACTORS + PENALTY_FACTORS


class ScoringConfig(BaseModel):
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "business_value": 1.5,
            "user_impact": 1.2,
            "market_demand": 1.0,
            "competitive_gap": 1.0,
            "revenue_potential": 1.2,
            "strategic_alignment": 1.0,
            "ai_opportunity": 0.8,
            "technical_feasibility": 0.6,
            "complexity": 1.0,
            "risk": 0.8,
        }
    )
    # Phase thresholds on complexity (0-5) and normalized score (0-1).
    quick_win_max_complexity: float = 2.0
    growth_max_complexity: float = 3.0
    major_max_complexity: float = 4.0
    strategic_min_ai_opportunity: float = 4.0

    def weight(self, factor: str) -> float:
        return float(self.weights.get(factor, 0.0))

    def max_score(self) -> float:
        return sum(5 * self.weight(f) for f in BENEFIT_FACTORS)

    def min_score(self) -> float:
        return -sum(5 * self.weight(f) for f in PENALTY_FACTORS)


def clamp_factor(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(5.0, float(value)))


def score_opportunity(factors: dict[str, float], config: ScoringConfig) -> ScoreBreakdown:
    contributions: dict[str, float] = {}
    total = 0.0
    for f in BENEFIT_FACTORS:
        c = config.weight(f) * clamp_factor(factors.get(f))
        contributions[f] = round(c, 3)
        total += c
    for f in PENALTY_FACTORS:
        c = -config.weight(f) * clamp_factor(factors.get(f))
        contributions[f] = round(c, 3)
        total += c
    return ScoreBreakdown(
        total=round(total, 3),
        contributions=contributions,
        weights={f: config.weight(f) for f in ALL_FACTORS},
    )


def normalized(score: ScoreBreakdown, config: ScoringConfig) -> float:
    lo, hi = config.min_score(), config.max_score()
    if hi == lo:
        return 0.0
    return round((score.total - lo) / (hi - lo), 4)


def complexity_label(complexity: float) -> str:
    if complexity <= 2.0:
        return "low"
    if complexity <= 3.0:
        return "medium"
    if complexity <= 4.0:
        return "high"
    return "very_high"


def assign_phase(factors: dict[str, float], config: ScoringConfig) -> Phase:
    """Map an opportunity to a roadmap phase from its complexity and AI weight.

    Phase 1: quick wins (0-4 weeks)          complexity <= quick_win_max
    Phase 2: growth features (1-3 months)    complexity <= growth_max
    Phase 3: major improvements (3-6 months) complexity <= major_max
    Phase 4: strategic / AI (6-12 months)    anything larger, or high-complexity AI bets
    """
    cx = clamp_factor(factors.get("complexity"))
    ai = clamp_factor(factors.get("ai_opportunity"))
    if cx <= config.quick_win_max_complexity:
        return "phase_1_quick_wins"
    if cx <= config.growth_max_complexity:
        return "phase_2_growth"
    if cx <= config.major_max_complexity and ai < config.strategic_min_ai_opportunity:
        return "phase_3_major"
    return "phase_4_strategic"


PHASE_LABELS: dict[str, str] = {
    "phase_1_quick_wins": "Phase 1 — Quick wins (0-4 weeks)",
    "phase_2_growth": "Phase 2 — Growth features (1-3 months)",
    "phase_3_major": "Phase 3 — Major product improvements (3-6 months)",
    "phase_4_strategic": "Phase 4 — Strategic / AI capabilities (6-12 months)",
}
