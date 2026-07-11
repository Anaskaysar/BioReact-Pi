"""Offline growth simulation — the digital twin's core loop.

Steps :class:`~src.models.growth_model.GrowthModel` forward over a chamber
temperature/humidity profile without any hardware attached. Useful for
calibrating thresholds, testing PID setpoints, or generating demo data
before a Pi is wired up.

Output rows use the same field names as the ``growth`` block in
``ui/data/demo_telemetry.json`` (``biomass_*_g_l``, ``phase``) so simulated
runs can be dropped straight into the dashboard's expected payload shape.

Run directly for a quick demo:

    python -m digital_twin.simulator
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# Makes `python digital_twin/simulator.py` (run directly, e.g. an editor's
# "Run" button) work the same as `python -m digital_twin.simulator` — without
# this, the `src` import below fails with "ModuleNotFoundError: No module
# named 'src'" because the script's own directory (not the project root)
# ends up on sys.path. Harmless when already run as a module or under
# pytest; it just adds the root a second time in that case.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.models.growth_model import GrowthModel


@dataclass
class SimulationStep:
    t_hours: float
    temp_c: float
    humidity_pct: float
    growth_rate: float
    biomass_g_l: float
    phase: str


def simulate(
    temp_profile: list[float],
    humidity_profile: list[float],
    time_step_hours: float = 1.0,
    initial_biomass_g_l: float = 0.05,
    carrying_capacity_g_l: float = 5.0,
    model: GrowthModel | None = None,
) -> list[SimulationStep]:
    """Step the growth model across paired temperature/humidity readings.

    ``temp_profile`` and ``humidity_profile`` must be the same length — one
    entry per time step. Returns one :class:`SimulationStep` per input entry.
    """
    if len(temp_profile) != len(humidity_profile):
        raise ValueError("temp_profile and humidity_profile must be the same length")

    # GrowthModel's default min_survivors (3.0) is tuned for raw cell counts.
    # This simulator works in g/L biomass concentration, so the default here
    # uses a floor sized for that unit instead — otherwise die-off would snap
    # up to an unphysical fraction of carrying capacity.
    model = model or GrowthModel(min_survivors=0.001)
    biomass = initial_biomass_g_l
    steps: list[SimulationStep] = []

    for i, (temp, humidity) in enumerate(zip(temp_profile, humidity_profile)):
        rate = model.growth_rate(temp, humidity)
        biomass = model.update_population(
            biomass, rate, time_step_hours, max_pop=carrying_capacity_g_l
        )
        steps.append(
            SimulationStep(
                t_hours=round((i + 1) * time_step_hours, 3),
                temp_c=temp,
                humidity_pct=humidity,
                growth_rate=round(rate, 4),
                biomass_g_l=round(biomass, 4),
                phase=model.phase(rate),
            )
        )

    return steps


def _demo() -> None:
    """Simulate 24 hours held near the model's optimum, print a summary."""
    hours = 24
    temp_profile = [37.0] * hours
    humidity_profile = [80.0] * hours

    steps = simulate(temp_profile, humidity_profile, time_step_hours=1.0)

    print(f"{'hour':>4}  {'temp_c':>7}  {'humidity':>8}  {'rate':>7}  {'biomass_g_l':>11}  phase")
    for s in steps:
        print(
            f"{s.t_hours:>4.0f}  {s.temp_c:>7.1f}  {s.humidity_pct:>8.1f}  "
            f"{s.growth_rate:>7.3f}  {s.biomass_g_l:>11.4f}  {s.phase}"
        )


if __name__ == "__main__":
    _demo()
