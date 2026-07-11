"""Logistic bacterial growth model driven by chamber temperature and humidity.

Converts raw DHT22 (or mock) sensor readings into a growth/death rate, then
integrates that rate over a time step using the logistic equation so biomass
eases into the chamber's carrying capacity instead of hitting a hard ceiling.

Used by the edge controller (``src/main.py`` + ``src/control``) to decide
heater/fan output, and by ``digital_twin/simulator.py`` for offline what-if
runs — both should import :class:`GrowthModel` rather than reimplementing
this math, so there is exactly one source of truth for the growth curve.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def _interpolate(x: float, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation over sorted (x, y) points.

    Extrapolates using the slope of the nearest segment when ``x`` falls
    outside the given range, so the curve has no discontinuities and no
    arbitrary flat clamping at the edges (e.g. extreme heat keeps getting
    deadlier rather than plateauing at the last defined point).
    """
    if x <= points[0][0]:
        (x0, y0), (x1, y1) = points[0], points[1]
        slope = (y1 - y0) / (x1 - x0)
        return y0 + slope * (x - x0)

    if x >= points[-1][0]:
        (x0, y0), (x1, y1) = points[-2], points[-1]
        slope = (y1 - y0) / (x1 - x0)
        return y1 + slope * (x - x1)

    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)

    return points[-1][1]  # unreachable given the bounds checks above


@dataclass
class GrowthModel:
    """Temperature/humidity-driven logistic growth model for a bacterial culture.

    All thresholds are constructor fields (not magic numbers buried in
    branches) so they can be tuned per-culture without touching the curve
    logic itself.
    """

    # Temperature thresholds (°C) — E. coli reference values: growth is
    # positive strictly between min_growth and max_temp, zero at those two
    # boundaries, peaks at opt_temp, and goes negative (death) outside them.
    min_temp: float = 2.0       # deep-cold anchor, well below min_growth: dying
    min_growth: float = 8.0     # growth range floor — zero growth right at this point
    opt_temp: float = 37.0      # peak growth
    max_growth: float = 45.0    # past this: still positive but declining fast
    max_temp: float = 50.0      # growth range ceiling — zero growth right at this point

    # Humidity thresholds (%) — only meaningful when you actually have a
    # humidity reading; see growth_rate()'s humidity_pct=None behavior for
    # what happens when you don't (e.g. no DHT22 wired up).
    min_humidity: float = 40.0  # below this: stunted growth
    opt_humidity: float = 80.0  # optimal humidity

    max_growth_rate: float = 2.4  # divisions/hour at optimal conditions

    # Spore floor — population never fully dies out. Must be set in the same
    # units you pass to update_population()/max_pop (e.g. ~3 raw cells if you
    # track counts, but ~0.001 g/L if you track biomass concentration like
    # the digital twin does — a bare "3.0" would be a huge floor in g/L terms).
    min_survivors: float = 3.0

    _temp_points: list[tuple[float, float]] = field(init=False, repr=False)
    _humidity_points: list[tuple[float, float]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._temp_points = [
            (self.min_temp - 6, -0.5),
            (self.min_temp, -0.3),
            (self.min_growth, 0.0),
            ((self.min_growth + self.opt_temp) / 2, 0.55),
            (self.opt_temp, 1.0),
            (self.max_growth, 0.35),
            (self.max_temp, 0.0),
            (self.max_temp + 5, -1.5),
        ]
        self._humidity_points = [
            (0.0, 0.02),
            (20.0, 0.1),
            (self.min_humidity, 0.4),
            (60.0, 0.7),
            (self.opt_humidity, 1.0),
            (100.0, 1.0),
        ]

    def temperature_effect(self, temp_c: float) -> float:
        """Temperature effect on growth (~-1.5 to 1). Continuous across the full range."""
        return _interpolate(temp_c, self._temp_points)

    def humidity_effect(self, humidity_pct: float) -> float:
        """Humidity effect on growth (~0.02 to 1). Continuous; never lethal on its own."""
        clamped = max(0.0, min(100.0, humidity_pct))
        return _interpolate(clamped, self._humidity_points)

    def growth_rate(self, temp_c: float, humidity_pct: float | None = None) -> float:
        """Growth rate (divisions/hour). Positive = growth, negative = death.

        Death is driven by temperature only — dry conditions stunt growth,
        they don't kill outright. ``humidity_pct=None`` (the default) means
        "no humidity sensor" — the humidity term is left neutral (1.0, no
        penalty) rather than assuming a specific reading we don't actually
        have.
        """
        temp_eff = self.temperature_effect(temp_c)

        if temp_eff < 0:
            return temp_eff

        humidity_eff = 1.0 if humidity_pct is None else self.humidity_effect(humidity_pct)
        return self.max_growth_rate * temp_eff * humidity_eff

    def update_population(
        self,
        current_pop: float,
        growth_rate: float,
        time_hours: float,
        max_pop: float = 5000.0,
    ) -> float:
        """Integrate population over a time step.

        Growth uses the closed-form logistic solution (S-curve easing into
        ``max_pop``); death is exponential decay toward ``min_survivors``.
        """
        if growth_rate > 0:
            if current_pop <= 0:
                return 0.0
            ratio = (max_pop - current_pop) / current_pop
            new_pop = max_pop / (1 + ratio * math.exp(-growth_rate * time_hours))
            return min(new_pop, max_pop)

        if growth_rate < 0:
            new_pop = current_pop * math.exp(growth_rate * time_hours)
            return max(new_pop, self.min_survivors)

        return current_pop

    def phase(self, growth_rate: float) -> str:
        """Growth phase label from the instantaneous rate."""
        if growth_rate > 1.5:
            return "exponential"
        if growth_rate > 0.1:
            return "growth"
        if growth_rate > -0.1:
            return "stationary"
        if growth_rate > -0.5:
            return "declining"
        return "death"

    def doubling_time_minutes(self, growth_rate: float) -> float:
        """Doubling time in minutes; ``inf`` for non-positive rates."""
        if growth_rate <= 0:
            return math.inf
        return (math.log(2) / growth_rate) * 60
