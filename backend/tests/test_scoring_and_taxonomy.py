from cip.core.scoring import ScoringConfig, assign_phase, normalized, score_opportunity
from cip.core.taxonomy import load_taxonomy


def test_score_is_transparent_weighted_sum():
    cfg = ScoringConfig()
    factors = {"business_value": 4, "user_impact": 3, "complexity": 2, "risk": 1}
    s = score_opportunity(factors, cfg)
    assert s.contributions["business_value"] == 4 * cfg.weights["business_value"]
    assert s.contributions["complexity"] == -2 * cfg.weights["complexity"]
    assert s.total == round(sum(s.contributions.values()), 3)
    assert 0 <= normalized(s, cfg) <= 1


def test_weights_are_configurable():
    base = score_opportunity({"ai_opportunity": 5}, ScoringConfig())
    cfg = ScoringConfig()
    cfg.weights["ai_opportunity"] = 3.0
    assert score_opportunity({"ai_opportunity": 5}, cfg).total > base.total


def test_factor_values_are_clamped():
    s = score_opportunity({"business_value": 50, "risk": -3}, ScoringConfig())
    assert s.contributions["business_value"] == 5 * 1.5
    assert s.contributions["risk"] == 0


def test_phase_assignment():
    cfg = ScoringConfig()
    assert assign_phase({"complexity": 1}, cfg) == "phase_1_quick_wins"
    assert assign_phase({"complexity": 3}, cfg) == "phase_2_growth"
    assert assign_phase({"complexity": 4, "ai_opportunity": 1}, cfg) == "phase_3_major"
    assert assign_phase({"complexity": 4, "ai_opportunity": 5}, cfg) == "phase_4_strategic"
    assert assign_phase({"complexity": 5}, cfg) == "phase_4_strategic"


def test_taxonomy_matching():
    tx = load_taxonomy()
    hits = tx.match_text("Book an appointment online. Our AI assistant helps. Two-factor authentication.")
    assert {"workflow.scheduling", "ai.assistant", "auth.mfa"} <= set(hits)
    assert "comm.sms" not in tx.match_text("We use smsc protocol")  # word boundaries
    assert "billing.payments" in tx.match_code_signal("stripe")
    assert "ai.assistant" in tx.match_code_signal("@anthropic-ai/sdk")
    assert tx.match_code_signal("stripes-ui") == []


def test_all_ai_category_features_are_flagged_ai():
    tx = load_taxonomy()
    ai = {f.id for f in tx.features if f.ai}
    assert {"ai.assistant", "ai.rag", "ai.search", "ai.predictive"} <= ai
    assert all(f.category_id == "ai" for f in tx.features if f.ai)
