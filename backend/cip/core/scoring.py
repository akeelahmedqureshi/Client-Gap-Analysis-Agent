"""Transparent, configurable opportunity scoring.

The LLM is never asked "which feature matters most". Instead every opportunity
carries factor scores (0-5) and the priority is a weighted sum, scaled by how
well the opportunity is evidenced (BRS 15: impact × evidence confidence):

    raw   = Σ w_i · benefit_i  −  w_c · complexity  −  w_r · risk
    score = raw × (1 − c_w + c_w · evidence_confidence)

Benefits include the BRS 7.16 dimensions: competitive importance, customer value,
revenue potential, cost-saving potential, productivity impact, time to value and
strategic importance. Weights are configurable per run so an account team can tune
them, and the full breakdown is stored with every recommendation.

Priority (BRS 7.14) is High / Medium / Low from the normalized score; a finding
with confidence below ``high_min_confidence`` is never High, so weak evidence
cannot drive the top recommendations (BRS 30).
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
    "cost_saving",
    "productivity",
    "time_to_value",
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
            "cost_saving": 1.0,
            "productivity": 0.8,
            "time_to_value": 0.5,
            "complexity": 1.0,
            "risk": 0.8,
        }
    )
    # Phase thresholds on complexity (0-5) and normalized score (0-1).
    quick_win_max_complexity: float = 2.0
    growth_max_complexity: float = 3.0
    major_max_complexity: float = 4.0
    strategic_min_ai_opportunity: float = 4.0
    # Evidence confidence scales the score: 0 = ignore confidence, 1 = score × confidence.
    confidence_weight: float = 0.4
    # Priority bands on the normalized score (0-1); High also needs this much evidence confidence.
    # Calibrated so "mostly 4/5 with good evidence" is High and "mostly 3/5" is Medium.
    high_min_normalized: float = 0.58
    medium_min_normalized: float = 0.46
    high_min_confidence: float = 0.5

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


def score_opportunity(factors: dict[str, float], config: ScoringConfig,
                      confidence: float | None = None) -> ScoreBreakdown:
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
    raw = total
    factor = None
    if confidence is not None:
        c = max(0.0, min(1.0, float(confidence)))
        factor = round(1 - config.confidence_weight + config.confidence_weight * c, 4)
        # Scale the benefit part only: a weak finding is less attractive, never "less complex".
        benefits = sum(v for k, v in contributions.items() if k in BENEFIT_FACTORS)
        total = benefits * factor + (raw - benefits)
    return ScoreBreakdown(
        total=round(total, 3),
        contributions=contributions,
        weights={f: config.weight(f) for f in ALL_FACTORS},
        raw_total=round(raw, 3) if factor is not None else None,
        confidence=confidence, confidence_factor=factor,
    )


def normalized(score: ScoreBreakdown, config: ScoringConfig) -> float:
    lo, hi = config.min_score(), config.max_score()
    if hi == lo:
        return 0.0
    return round((score.total - lo) / (hi - lo), 4)


def priority_label(score: ScoreBreakdown, config: ScoringConfig, confidence: float | None = None) -> str:
    """High / Medium / Low (BRS 7.14). Low-confidence findings are capped at Medium (BRS 30)."""
    n = normalized(score, config)
    if n >= config.high_min_normalized and (confidence is None or confidence >= config.high_min_confidence):
        return "high"
    if n >= config.medium_min_normalized:
        return "medium"
    return "low"


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
