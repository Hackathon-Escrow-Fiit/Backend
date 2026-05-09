"""
DecentraWork Elo Rating System

Formula (applied at client/DAO decision time):

  Accepted: delta = +round(BASE × ai × E × nur × rating_factor)
  Rejected: delta = -round(BASE × (1 - ai) × E / nur)

  ai            = confidence_score / 100          (AI quality, 0-1)
  E             = 1 / (1 + 10^((complexity - elo) / 400))  (expected score, 0-1)
  nur           = new_user_rating (2.5 first task → 1.0 at 30 tasks)
  rating_factor = (MAX_ELO - elo) / (MAX_ELO - STARTING_ELO)  (1.0 at start → 0.1 at max)
  BASE          = 50  (max Elo per task: ai=100%, E=1.0, nur=1.0, rating_factor=1.0)

Direction is always set by the human decision (approved/rejected).
Magnitude is set by AI quality: high score + approved = big gain,
good score wrongly rejected = small penalty.
Higher Elo → smaller gains (rating_factor shrinks toward top).
"""

STARTING_ELO = 300
MAX_ELO      = 1000
BASE_GAIN    = 50   # max Elo points per task (ai=100%, E=1.0 easy task, nur=1.0)

TIERS = [
    (800, "Elite"),
    (600, "Expert"),
    (400, "Skilled"),
    (200, "Rising"),
    (0,   "Entry"),
]


def get_tier(elo: int) -> str:
    for threshold, name in TIERS:
        if elo >= threshold:
            return name
    return "Entry"


def new_user_rating_multiplier(tasks_completed: int) -> float:
    """2.5 on first task, linearly decreasing to 1.0 at 30 tasks."""
    return max(1.0, 2.5 - tasks_completed * (1.5 / 30.0))


def calculate_elo_change(
    freelancer_elo: int,
    task_complexity: int,
    confidence_score: float,   # 0-100
    approved: bool,
    tasks_completed: int,
) -> tuple[int, dict]:
    """
    Returns (delta, formula_breakdown) so callers can log/display every input value.
    """
    e   = 1.0 / (1.0 + 10.0 ** ((task_complexity - freelancer_elo) / 400.0))
    ai  = confidence_score / 100.0
    nur = new_user_rating_multiplier(tasks_completed)

    # rating_factor: 1.0 at STARTING_ELO, shrinks linearly to 0.1 at MAX_ELO.
    # Higher-rated freelancers gain less per task — harder to climb the top.
    rating_range  = MAX_ELO - STARTING_ELO          # 700
    rating_factor = max(0.1, (MAX_ELO - freelancer_elo) / rating_range)

    if approved:
        formula_str = "BASE × ai × E × nur × rating_factor"
        raw   = BASE_GAIN * ai * e * nur * rating_factor
        delta = max(1, round(raw))
    else:
        formula_str = "-BASE × (1 - ai) × E / nur"
        raw   = BASE_GAIN * (1.0 - ai) * e / nur
        delta = -max(1, round(raw))

    breakdown = {
        "formula":          formula_str,
        "BASE":             BASE_GAIN,
        "confidence_score": confidence_score,
        "ai":               round(ai, 4),
        "task_complexity":  task_complexity,
        "freelancer_elo":   freelancer_elo,
        "E_expected":       round(e, 4),
        "nur_multiplier":   round(nur, 4),
        "rating_factor":    round(rating_factor, 4),
        "tasks_completed":  tasks_completed,
        "raw_delta":        round(raw, 3),
        "final_delta":      delta,
        "approved":         approved,
    }
    return delta, breakdown


def apply_elo(
    current_elo: int,
    tasks_completed: int,
    active_modifiers: list[dict],
    task_complexity: int,
    confidence_score: float,
    approved: bool,
) -> dict:
    """
    Full Elo update at decision time. Returns a dict with old/new Elo,
    delta, tier change info, and the full formula breakdown for display.
    """
    old_tier = get_tier(current_elo)

    delta, breakdown = calculate_elo_change(
        current_elo, task_complexity, confidence_score, approved, tasks_completed
    )

    new_elo   = min(MAX_ELO, max(0, current_elo + delta))
    new_tasks = tasks_completed + 1
    new_tier  = get_tier(new_elo)

    updated_mods = [
        {**m, "remaining": m["remaining"] - 1}
        for m in active_modifiers
        if m.get("remaining", 0) > 1
    ]

    if new_tier != old_tier and new_elo > current_elo:
        updated_mods.append({"type": "tier_promotion", "remaining": 5})

    return {
        "old_elo":            current_elo,
        "new_elo":            new_elo,
        "elo_delta":          delta,
        "old_tier":           old_tier,
        "new_tier":           new_tier,
        "tier_changed":       new_tier != old_tier,
        "confidence_score":   confidence_score,
        "task_complexity":    task_complexity,
        "new_tasks_completed": new_tasks,
        "updated_modifiers":  updated_mods,
        "formula_breakdown":  breakdown,
    }
