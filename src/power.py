"""
Cycling power estimation from segment parameters.

Based on Martin et al. (1998), "Validation of a Mathematical Model for Road
Cycling Power." The model sums four components:

  P_total = P_rolling + P_aero + P_gravity + P_accel

  P_rolling = Crr * m * g * v * cos(theta)
  P_aero    = 0.5 * rho * CdA * (v + v_wind)^2 * v
  P_gravity = m * g * v * sin(theta)
  P_accel   = m * v * dv/dt  (negligible for steady-state segments)

For segment-scale estimation we assume steady-state over the segment length,
so dv/dt = 0 and we use average velocity = distance / time.

Default constants aimed at a reasonably fit rider on a road bike, wearing
normal clothing, no wind. Adjust RIDER_MASS and BIKE_MASS in the scorer for
your own body weight / setup.
"""

import math

# physical constants
G = 9.80665            # m/s^2, gravity
RHO = 1.225            # kg/m^3, air density at sea level 15C
CRR = 0.005            # rolling resistance coefficient, tarmac
CDA = 0.32             # m^2, frontal area * drag coefficient, hoods position
DRIVETRAIN_EFF = 0.977 # 2.3 percent drivetrain loss

# defaults, overrideable per call
DEFAULT_RIDER_MASS = 75.0  # kg
DEFAULT_BIKE_MASS = 9.0    # kg


def required_watts(
    distance_m: float,
    elapsed_s: float,
    elevation_gain_m: float,
    rider_mass: float = DEFAULT_RIDER_MASS,
    bike_mass: float = DEFAULT_BIKE_MASS,
    crr: float = CRR,
    cda: float = CDA,
    rho: float = RHO,
) -> float:
    """
    Average power at the wheel required to cover distance_m in elapsed_s with
    elevation_gain_m of climbing, plus drivetrain losses to give crank power.

    Treats the grade as the average grade over the segment, which under-reports
    power for segments with uneven grades (climbing hard then coasting uses more
    power than steady effort), but is a reasonable first approximation.
    """
    if elapsed_s <= 0 or distance_m <= 0:
        return 0.0

    v = distance_m / elapsed_s  # m/s
    total_mass = rider_mass + bike_mass

    # average grade as rise over run; for small grades sin(theta) ~ tan(theta)
    # but we use the exact form since Brooklyn still has some non-trivial
    # pitches in Prospect Park and around the bridges.
    if distance_m > abs(elevation_gain_m):
        theta = math.asin(elevation_gain_m / distance_m)
    else:
        # degenerate segment data, treat as flat
        theta = 0.0

    p_roll = crr * total_mass * G * v * math.cos(theta)
    p_aero = 0.5 * rho * cda * v * v * v
    p_grav = total_mass * G * v * math.sin(theta)

    p_wheel = p_roll + p_aero + p_grav
    if p_wheel < 0:
        # net downhill where gravity alone exceeds drag+rolling; rider
        # would be coasting, crank power effectively zero
        return 0.0
    return p_wheel / DRIVETRAIN_EFF


def estimate_rider_cp(activities: list[dict]) -> dict[str, float]:
    """
    Estimate your sustainable power at various durations from recent rides.

    We do not have power streams here (that would require the streams endpoint,
    one call per activity), so we use activity-level average_watts where
    available, weighted by moving_time. This gives a rough critical-power curve
    approximated by two anchors: short (under 10 minutes) and long (over 30
    minutes). Segments between those get linearly interpolated.

    Returns a dict {'short_watts': x, 'long_watts': y} that score.py uses.
    """
    short_samples = []  # (watts, seconds) for efforts under 10 minutes
    long_samples = []   # same for efforts over 30 minutes

    for a in activities:
        if a.get("type") not in ("Ride", "VirtualRide"):
            continue
        w = a.get("average_watts") or a.get("weighted_average_watts")
        t = a.get("moving_time", 0)
        if not w or not t:
            continue
        # activity-level averages are most meaningful for fairly hard rides
        # so we skip low-intensity activities by a rough heuristic
        if w < 100:
            continue
        if t < 600:
            short_samples.append((w, t))
        elif t > 1800:
            long_samples.append((w, t))

    def _weighted_top(samples, top_frac=0.3):
        """Take the top 30 percent of samples by watts, time-weighted mean."""
        if not samples:
            return None
        samples = sorted(samples, key=lambda x: -x[0])
        top = samples[: max(1, int(len(samples) * top_frac))]
        total_t = sum(t for _, t in top)
        return sum(w * t for w, t in top) / total_t if total_t else None

    short = _weighted_top(short_samples) or 250.0  # fallback
    long = _weighted_top(long_samples) or 200.0    # fallback
    return {"short_watts": short, "long_watts": long}


def your_sustainable_watts(duration_s: float, cp: dict[str, float]) -> float:
    """
    Given a duration and your CP profile, estimate watts you could hold.
    Short anchor is 10 minutes, long anchor is 30 minutes, linear in log time.
    """
    if duration_s <= 600:
        return cp["short_watts"]
    if duration_s >= 1800:
        return cp["long_watts"]
    # linear interpolation in log seconds between 600 and 1800
    frac = (math.log(duration_s) - math.log(600)) / (math.log(1800) - math.log(600))
    return cp["short_watts"] + frac * (cp["long_watts"] - cp["short_watts"])
