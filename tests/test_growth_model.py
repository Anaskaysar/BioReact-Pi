"""Tests for src.models.growth_model.GrowthModel."""

from __future__ import annotations

import math

import pytest

from src.models.growth_model import GrowthModel


@pytest.fixture
def model() -> GrowthModel:
    return GrowthModel()


def test_temperature_effect_continuous_across_former_boundaries(model: GrowthModel) -> None:
    for boundary in (2, 8, 22.5, 37, 45, 50):
        before = model.temperature_effect(boundary - 0.001)
        at = model.temperature_effect(boundary)
        after = model.temperature_effect(boundary + 0.001)
        assert abs(before - at) < 0.01, f"jump before {boundary}: {before} -> {at}"
        assert abs(at - after) < 0.01, f"jump after {boundary}: {at} -> {after}"


def test_humidity_effect_continuous_across_former_boundaries(model: GrowthModel) -> None:
    for boundary in (20, 40, 60, 80):
        before = model.humidity_effect(boundary - 0.001)
        after = model.humidity_effect(boundary + 0.001)
        assert abs(before - after) < 0.01, f"jump at {boundary}: {before} -> {after}"


def test_temperature_effect_peaks_at_opt_temp(model: GrowthModel) -> None:
    peak = model.temperature_effect(model.opt_temp)
    for t in (20, 25, 30, 40, 45):
        assert model.temperature_effect(t) <= peak


def test_temperature_effect_negative_outside_survivable_range(model: GrowthModel) -> None:
    assert model.temperature_effect(model.min_temp - 1) < 0
    assert model.temperature_effect(model.max_temp + 1) < 0


def test_humidity_effect_never_negative_and_monotonic(model: GrowthModel) -> None:
    prev = -math.inf
    h = 0.0
    while h <= 100:
        effect = model.humidity_effect(h)
        assert effect >= 0, f"humidity effect negative at {h}%"
        assert effect >= prev - 1e-9, f"humidity effect decreased at {h}%"
        prev = effect
        h += 5


def test_growth_rate_positive_at_optimal_conditions(model: GrowthModel) -> None:
    rate = model.growth_rate(model.opt_temp, model.opt_humidity)
    assert rate > 0
    assert rate <= model.max_growth_rate + 1e-9


def test_growth_rate_negative_at_lethal_temp_regardless_of_humidity(model: GrowthModel) -> None:
    assert model.growth_rate(60, 100) < 0
    assert model.growth_rate(60, 0) < 0


def test_growth_positive_strictly_between_8_and_50_peaking_at_37(model: GrowthModel) -> None:
    """E. coli reference range: grows between ~8C and ~50C, optimal at 37C."""
    assert model.growth_rate(7.9) <= 0
    assert model.growth_rate(50.1) <= 0
    for t in (8.5, 15, 25, 30, 37, 42, 48, 49.5):
        assert model.growth_rate(t) > 0, f"expected growth at {t}C"
    assert model.growth_rate(37) == pytest.approx(model.max_growth_rate, rel=1e-6)


def test_growth_rate_without_humidity_reading_is_neutral(model: GrowthModel) -> None:
    """No humidity sensor (humidity_pct=None) must not silently assume a
    specific reading — it should behave exactly like optimal humidity
    (no penalty), not like a fixed guess such as 80%."""
    assert model.growth_rate(model.opt_temp) == pytest.approx(
        model.growth_rate(model.opt_temp, model.opt_humidity), rel=1e-9
    )


def test_dry_conditions_slow_but_do_not_reverse_growth(model: GrowthModel) -> None:
    wet = model.growth_rate(model.opt_temp, 90)
    dry = model.growth_rate(model.opt_temp, 10)
    assert dry > 0, "dry growth rate should still be positive, not lethal"
    assert dry < wet, "dry conditions should slow growth relative to wet"


def test_logistic_growth_approaches_but_never_exceeds_carrying_capacity(
    model: GrowthModel,
) -> None:
    pop = 100.0
    max_pop = 5000.0
    for _ in range(50):
        pop = model.update_population(pop, 1.2, 1.0, max_pop)
        assert pop <= max_pop + 1e-6
    assert pop > max_pop * 0.9


def test_exponential_death_never_drops_below_survivor_floor(model: GrowthModel) -> None:
    pop = 1000.0
    for _ in range(50):
        pop = model.update_population(pop, -1.5, 1.0)
        assert pop >= model.min_survivors - 1e-6


def test_zero_growth_rate_leaves_population_unchanged(model: GrowthModel) -> None:
    assert model.update_population(500, 0, 3) == 500


def test_phase_labels_at_representative_rates(model: GrowthModel) -> None:
    assert model.phase(2.0) == "exponential"
    assert model.phase(0.5) == "growth"
    assert model.phase(0) == "stationary"
    assert model.phase(-0.3) == "declining"
    assert model.phase(-1.0) == "death"


def test_doubling_time_infinite_for_non_positive_rates(model: GrowthModel) -> None:
    assert model.doubling_time_minutes(0) == math.inf
    assert model.doubling_time_minutes(-1) == math.inf
    dt = model.doubling_time_minutes(1)
    assert 0 < dt < math.inf
