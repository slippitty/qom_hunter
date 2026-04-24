"""
Grade-adjusted pace (GAP) for running.

Minetti et al. (2002) measured the metabolic cost of running on gradients and
produced a polynomial in grade (as decimal, so 0.05 = 5 percent up):

  C(i) = 155.4*i^5 - 30.4*i^4 - 43.3*i^3 + 46.3*i^2 + 19.5*i + 3.6  (J/kg/m)

We use that to convert actual pace at grade i to an equivalent flat pace:

  gap_pace = pace_actual * C(0) / C(i)

Then "beating a QOM" on a graded segment reduces to comparing your flat-
equivalent pace to hers.
"""


def minetti_cost(grade: float) -> float:
    """Metabolic cost of running per unit distance, J/kg/m, given grade as decimal."""
    i = grade
    return (
        155.4 * i ** 5
        - 30.4 * i ** 4
        - 43.3 * i ** 3
        + 46.3 * i ** 2
        + 19.5 * i
        + 3.6
    )


FLAT_COST = minetti_cost(0.0)


def gap_seconds_per_km(seconds_per_km: float, grade_decimal: float) -> float:
    """Convert actual pace to grade-adjusted pace (flat-equivalent)."""
    cost = minetti_cost(grade_decimal)
    if cost <= 0:
        return seconds_per_km
    return seconds_per_km * (FLAT_COST / cost)


def estimate_runner_gap(activities: list[dict]) -> dict[str, float]:
    """
    Estimate your sustainable flat pace at two anchor durations from recent runs.
    We treat each run's average pace as roughly flat-equivalent, since Brooklyn
    running activities are mostly near sea level with limited grade variance.
    Returns {'short_s_per_km': x, 'long_s_per_km': y}.
    """
    short_samples = []  # efforts under 15 minutes, likely hard
    long_samples = []   # efforts over 40 minutes

    for a in activities:
        if a.get("type") not in ("Run", "TrailRun"):
            continue
        dist = a.get("distance", 0)
        t = a.get("moving_time", 0)
        if dist < 1000 or t < 300:
            continue
        pace = t / (dist / 1000.0)  # seconds per km
        if t < 900:
            short_samples.append((pace, t))
        elif t > 2400:
            long_samples.append((pace, t))

    def _weighted_fastest(samples, top_frac=0.3):
        if not samples:
            return None
        samples = sorted(samples, key=lambda x: x[0])  # fastest first
        top = samples[: max(1, int(len(samples) * top_frac))]
        total_t = sum(t for _, t in top)
        return sum(p * t for p, t in top) / total_t if total_t else None

    short = _weighted_fastest(short_samples) or 300.0  # 5:00/km fallback
    long = _weighted_fastest(long_samples) or 330.0    # 5:30/km fallback
    return {"short_s_per_km": short, "long_s_per_km": long}


def your_sustainable_pace(duration_s: float, gap: dict[str, float]) -> float:
    """Return estimated s/km you could hold for the given duration, flat-equivalent."""
    if duration_s <= 600:
        return gap["short_s_per_km"]
    if duration_s >= 2400:
        return gap["long_s_per_km"]
    # linear in log time, same shape as the ride model
    import math

    frac = (math.log(duration_s) - math.log(600)) / (math.log(2400) - math.log(600))
    return gap["short_s_per_km"] + frac * (gap["long_s_per_km"] - gap["short_s_per_km"])
