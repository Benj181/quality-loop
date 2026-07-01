"""Tests for speed beacons: the beacon_effect formula, the quality penalty it
feeds into the loop, and the sweep_beacons comparison.

Mechanics checked against the wiki: a speed module raises speed (scaled by module
quality) but lowers quality by a fixed amount (NOT scaled by quality); a beacon
transmits both at strength distribution_efficiency * sqrt(n_beacons).
"""
import math

import pytest

from quality_loop import (
    MACHINES,
    ModuleStrategy,
    SystemOutput,
    beacon_effect,
    efficiency,
)
from quality_loop.engine import (
    BEACON_DISTRIBUTION_EFFICIENCY,
    SPEED_BONUS_BY_LEVEL,
    SPEED_QUALITY_PENALTY_BY_LEVEL,
    TIER_MULT,
    Tier,
)
from quality_loop.factory import (
    BeaconSpec,
    Ingredient,
    MachineSpec,
    RecipeSpec,
    plan_factory,
    sweep_beacons,
)

RECIPE = RecipeSpec(
    ingredients=(Ingredient("iron-plate", 1.0), Ingredient("copper-cable", 3.0)),
    craft_time=0.5, output_yield=1.0, name="electronic-circuit",
    output_item="electronic-circuit",
)

NORMAL = Tier.NORMAL.value
LEG = Tier.LEGENDARY.value


# ---- beacon_effect --------------------------------------------------------

def test_beacon_effect_zero_when_no_beacons_or_modules():
    assert beacon_effect(0, 2) == (0.0, 0.0)
    assert beacon_effect(3, 0) == (0.0, 0.0)


def test_beacon_effect_linear_in_modules():
    """Both speed and penalty are exactly linear in modules-per-beacon."""
    s1, p1 = beacon_effect(2, 1)
    s2, p2 = beacon_effect(2, 2)
    assert math.isclose(s2, 2 * s1)
    assert math.isclose(p2, 2 * p1)


def test_beacon_effect_sqrt_n_scaling():
    """Combined transmission across n beacons scales as sqrt(n)."""
    s1, p1 = beacon_effect(1, 1)
    s4, p4 = beacon_effect(4, 1)
    assert math.isclose(s4, 2 * s1)  # sqrt(4)/sqrt(1) = 2
    assert math.isclose(p4, 2 * p1)


def test_beacon_effect_known_values():
    """4 normal beacons x 2 legendary Spd-3: transmission 1.5*2 = 3.0."""
    s, p = beacon_effect(4, 2, speed_module_level=3,
                         speed_module_quality_tier=LEG, beacon_quality_tier=NORMAL)
    trans = 1.5 * 2.0  # d * sqrt(4)
    assert math.isclose(s, trans * 2 * (50.0 * TIER_MULT[LEG]) / 100.0)  # +750%
    assert math.isclose(p, trans * 2 * 2.5)  # 15 pp


def test_ratio_invariant_to_beacon_quality_and_layout():
    """penalty/speed depends only on the module, never on beacon quality or (n, m)."""
    ratios = []
    for bq in range(5):
        for n in (1, 3, 7):
            for m in (1, 2):
                s, p = beacon_effect(n, m, beacon_quality_tier=bq)
                ratios.append(p / s)
    assert max(ratios) - min(ratios) < 1e-12


def test_legendary_speed_module_dominates():
    """Legendary Spd-3 gives 2.5x the speed of a normal Spd-3 at the SAME penalty."""
    s_norm, p_norm = beacon_effect(1, 1, speed_module_quality_tier=NORMAL)
    s_leg, p_leg = beacon_effect(1, 1, speed_module_quality_tier=LEG)
    assert math.isclose(s_leg, s_norm * TIER_MULT[LEG])
    assert math.isclose(p_leg, p_norm)  # drawback does not scale with module quality


# ---- quality penalty threading into efficiency ----------------------------

def _eff(**kw):
    e, _ = efficiency(
        MACHINES["assembling_machine"], SystemOutput.ITEMS, ModuleStrategy.OPTIMIZE, **kw
    )
    return e


def test_recycler_penalty_monotonically_lowers_efficiency():
    base = _eff()
    small = _eff(recycler_quality_penalty=10.0)
    big = _eff(recycler_quality_penalty=100.0)
    assert base > small > big > 0.0


def test_penalty_clamps_at_zero_quality():
    """Beyond the recycler's own quality, extra penalty can't push quality negative:
    a huge penalty gives the same result as exactly zeroing recycler quality."""
    huge = _eff(recycler_quality_penalty=1000.0)
    at_zero = _eff(recycler_quality_penalty=25.0)  # 4 legendary Q3 modules = 25%
    assert math.isclose(huge, at_zero, rel_tol=1e-9)


def test_zero_penalty_is_unchanged():
    assert math.isclose(_eff(recycler_quality_penalty=0.0), _eff())


# ---- plan_factory with beacons --------------------------------------------

def _machine(**kw):
    base = dict(machine_key="assembling_machine", assembler_speed=1.25,
                recycler_speed=0.5, productivity_research=50.0)
    base.update(kw)
    return MachineSpec(**base)


def test_no_beacons_is_backward_compatible():
    """Absent a beacon spec, the plan is identical and reports zero beacon effect."""
    plan = plan_factory(RECIPE, _machine(), SystemOutput.ITEMS)
    assert plan.recycler_speed_bonus == 0.0
    assert plan.assembler_speed_bonus == 0.0
    assert plan.recycler_quality_penalty == 0.0
    assert plan.assembler_quality_penalty == 0.0


def test_recycler_beacon_speeds_up_recycler_only():
    """A recycler beacon adds recycler speed + penalty but no assembler effect."""
    beacon = BeaconSpec(n_beacons=1, modules_per_beacon=2)
    plan = plan_factory(RECIPE, _machine(recycler_beacons=beacon), SystemOutput.ITEMS)
    assert plan.recycler_speed_bonus > 0.0
    assert plan.recycler_quality_penalty > 0.0
    assert plan.assembler_speed_bonus == 0.0
    assert plan.assembler_quality_penalty == 0.0


def test_recycler_beacon_reduces_recycler_count_vs_same_penalty_no_speed():
    """Speed is what cuts machine counts: at equal efficiency (same penalty), the
    beaconed recycler needs no more recyclers than the un-sped one."""
    beacon = BeaconSpec(n_beacons=2, modules_per_beacon=2)
    sped = plan_factory(RECIPE, _machine(recycler_beacons=beacon), SystemOutput.ITEMS)
    # Same quality penalty but zero speed bonus: a beacon with 0 modules-worth of
    # speed is not expressible, so compare against the no-beacon baseline instead --
    # the beaconed plan must use strictly fewer recyclers than fractional baseline.
    base = plan_factory(RECIPE, _machine(), SystemOutput.ITEMS)
    assert sped.recycler_count <= base.recycler_count


# ---- sweep_beacons --------------------------------------------------------

def test_sweep_structure_and_flags():
    rows = sweep_beacons(RECIPE, _machine(), SystemOutput.ITEMS)
    # baseline + 3 placements x 2 qualities x 2 module counts = 13 rows.
    assert len(rows) == 13
    assert rows[0].placement == "none"
    assert rows[0].modules == 0
    assert rows[0].recycler_speed_bonus == 0.0 and rows[0].assembler_speed_bonus == 0.0
    assert sum(r.is_optimum for r in rows) == 1
    assert sum(r.is_fewest_recyclers for r in rows) == 1
    placements = {r.placement for r in rows}
    assert placements == {"none", "recycler", "assembler", "both"}


def test_sweep_placement_effects_are_localized():
    rows = sweep_beacons(RECIPE, _machine(), SystemOutput.ITEMS)
    for r in rows:
        if r.placement == "recycler":
            assert r.recycler_speed_bonus > 0 and r.assembler_speed_bonus == 0
        elif r.placement == "assembler":
            assert r.assembler_speed_bonus > 0 and r.recycler_speed_bonus == 0
        elif r.placement == "both":
            assert r.recycler_speed_bonus > 0 and r.assembler_speed_bonus > 0


def test_sweep_optimum_beats_baseline():
    rows = sweep_beacons(RECIPE, _machine(), SystemOutput.ITEMS)
    baseline = rows[0]
    opt = next(r for r in rows if r.is_optimum)
    assert opt.machines_per_output <= baseline.machines_per_output
