"""Tests for digital_twin.simulator."""

from __future__ import annotations

import pytest

from digital_twin.simulator import simulate
from src.models.growth_model import GrowthModel


def test_mismatched_profile_lengths_raise() -> None:
    with pytest.raises(ValueError):
        simulate([37.0, 37.0], [80.0])


def test_output_length_matches_input_profile() -> None:
    steps = simulate([37.0] * 5, [80.0] * 5, time_step_hours=1.0)
    assert len(steps) == 5


def test_biomass_stays_within_carrying_capacity() -> None:
    steps = simulate([37.0] * 24, [80.0] * 24, time_step_hours=1.0, carrying_capacity_g_l=5.0)
    assert all(s.biomass_g_l <= 5.0 + 1e-6 for s in steps)


def test_biomass_grows_under_favorable_conditions() -> None:
    steps = simulate([37.0] * 5, [80.0] * 5, initial_biomass_g_l=0.05)
    biomass_values = [s.biomass_g_l for s in steps]
    assert biomass_values == sorted(biomass_values), "biomass should be non-decreasing while growing"


def test_biomass_declines_under_lethal_temperature() -> None:
    steps = simulate([60.0] * 5, [80.0] * 5, initial_biomass_g_l=1.0)
    assert steps[-1].biomass_g_l < steps[0].biomass_g_l


def test_custom_model_instance_is_respected() -> None:
    custom = GrowthModel(opt_temp=30.0)
    steps = simulate([30.0] * 3, [80.0] * 3, model=custom)
    assert steps[0].growth_rate == pytest.approx(custom.max_growth_rate, rel=1e-6)
