"""
Scoring engine for S.H.I.O.K. Index.
Implements pure functions for subscores and composite score calculation.
"""

from typing import Dict, Any, Optional, Union

NO_TRANSIT_IN_RANGE = "NO_TRANSIT_IN_RANGE"
NOT_YET_SCORED = "NOT_YET_SCORED"


def score_transit_access(
    dist_mrt_m: float, params: Dict[str, Any], is_bus_interchange: bool = False
) -> Union[float, str]:
    """
    Score transit access based on distance to nearest MRT/LRT or Bus Interchange.
    Returns NO_TRANSIT_IN_RANGE if distance exceeds zero_credit_m.
    """
    if is_bus_interchange:
        full_credit = float(params.get("bus_interchange_full_credit_m", 200.0))
    else:
        full_credit = float(params.get("full_credit_m", 400.0))

    linear_floor = float(params.get("linear_floor_m", 800.0))
    zero_credit = float(params.get("zero_credit_m", 1200.0))
    score_at_800 = float(params.get("score_at_800m", 40.0))

    if dist_mrt_m > zero_credit:
        return NO_TRANSIT_IN_RANGE

    if dist_mrt_m <= full_credit:
        return 100.0

    if dist_mrt_m <= linear_floor:
        # Interpolate between 100 and score_at_800
        ratio = (dist_mrt_m - full_credit) / (linear_floor - full_credit)
        return max(0.0, min(100.0, 100.0 - ratio * (100.0 - score_at_800)))

    # Interpolate between score_at_800 and 0
    ratio = (dist_mrt_m - linear_floor) / (zero_credit - linear_floor)
    return max(0.0, min(100.0, score_at_800 - ratio * score_at_800))


def score_bus_connectivity(
    expected_wait_min: Optional[float], params: Dict[str, Any]
) -> Union[float, str]:
    """
    Score bus connectivity based on expected wait time.
    If 0 buses (expected_wait_min is None), returns NO_TRANSIT_IN_RANGE.
    """
    if expected_wait_min is None:
        return NO_TRANSIT_IN_RANGE

    full_credit = float(params.get("full_credit_wait_min", 2.0))
    zero_credit = float(params.get("zero_credit_wait_min", 15.0))

    if expected_wait_min <= full_credit:
        return 100.0
    if expected_wait_min >= zero_credit:
        return 0.0

    ratio = (expected_wait_min - full_credit) / (zero_credit - full_credit)
    return max(0.0, min(100.0, 100.0 - ratio * 100.0))


def score_rain_shelter(sheltered_m: float, total_m: float) -> float:
    """
    Score rain shelter as percentage of route covered.
    Fully covered path returns 100.
    """
    if total_m <= 0:
        return 100.0
    return max(0.0, min(100.0, (sheltered_m / total_m) * 100.0))


def score_heat_comfort(sheltered_m: float, total_m: float) -> float:
    """
    Score heat comfort. Provisional: identical to rain shelter (covered-only).
    """
    if total_m <= 0:
        return 100.0
    return max(0.0, min(100.0, (sheltered_m / total_m) * 100.0))


def score_crossing_friction(num_at_grade_crossings: int, params: Dict[str, Any]) -> float:
    """
    Score crossing friction. Subtracts penalty per crossing.
    Floors at 0 (e.g. 6+ crossings floor if penalty is 20).
    """
    penalty = float(params.get("penalty_per_crossing", 20.0))
    score = 100.0 - (num_at_grade_crossings * penalty)
    return max(0.0, min(100.0, float(score)))


def calculate_composite_score(
    subscores: Dict[str, Union[float, str]], weights: Dict[str, float]
) -> Union[float, str]:
    """
    Calculate exact weighted sum of subscores.
    If transit_access is NO_TRANSIT_IN_RANGE, composite is NO_TRANSIT_IN_RANGE.
    """
    if subscores.get("transit_access") == NO_TRANSIT_IN_RANGE:
        return NO_TRANSIT_IN_RANGE

    total_score = 0.0
    for key, weight in weights.items():
        val = subscores.get(key, 0.0)
        if val == NO_TRANSIT_IN_RANGE or val == NOT_YET_SCORED:
            val = 0.0
        total_score += float(val) * weight

    return total_score
