"""Tests for the factory-synthesis layer (multi-ingredient, set-unit model).

The tie-back test is the centerpiece: it fails loudly if productivity is
double-counted or the wrong flow entry is read.
"""
import pytest

from quality_loop.engine import SystemOutput
from quality_loop.factory import (
    BELT_CAP,
    Ingredient,
    MachineSpec,
    RecipeSpec,
    craft_rate,
    plan_factory,
)

# electronic-circuit: 1 iron-plate + 3 copper-cable -> 1 item, 0.5s.
RECIPE = RecipeSpec(
    ingredients=(Ingredient("iron-plate", 1.0), Ingredient("copper-cable", 3.0)),
    craft_time=0.5, output_yield=1.0, name="electronic-circuit",
    output_item="electronic-circuit",
)
SINGLE = RecipeSpec(
    ingredients=(Ingredient("widget", 1.0),),
    craft_time=0.5, output_yield=1.0, name="widget", output_item="widget",
)


def _machine(**kw):
    base = dict(machine_key="foundry", assembler_speed=5.0, recycler_speed=0.5)
    base.update(kw)
    return MachineSpec(**base)


@pytest.mark.parametrize("extra", [0.0, 100.0])
def test_tieback_items(extra):
    plan = plan_factory(RECIPE, _machine(productivity_research=extra), SystemOutput.ITEMS)
    assert plan.target_output_rate / plan.input_rate == pytest.approx(plan.efficiency_pct / 100.0, abs=1e-9)


@pytest.mark.parametrize("extra", [0.0, 100.0])
def test_tieback_ingredients(extra):
    # Extract copper-cable (count 3): efficiency is per-set, so divide by n_target.
    plan = plan_factory(
        RECIPE, _machine(productivity_research=extra), SystemOutput.INGREDIENTS,
        target_ingredient="copper-cable",
    )
    n_target = 3.0
    ratio = plan.target_output_rate / (plan.input_rate * n_target)
    assert ratio == pytest.approx(plan.efficiency_pct / 100.0, abs=1e-9)


@pytest.mark.parametrize("mode,kw", [
    (SystemOutput.ITEMS, {}),
    (SystemOutput.INGREDIENTS, {"target_ingredient": "iron-plate"}),
])
def test_belt_cap_honored(mode, kw):
    plan = plan_factory(RECIPE, _machine(productivity_research=100.0), mode, **kw)
    assert plan.binding_belt_flow == pytest.approx(BELT_CAP)
    assert plan.phi_belt <= BELT_CAP + 1e-6
    assert plan.tier0_belt <= BELT_CAP + 1e-6


def test_binding_belt_regression():
    """High prod -> recycler belt binds; plain -> tier-0 input belt binds."""
    high = plan_factory(RECIPE, _machine(productivity_research=100.0), SystemOutput.ITEMS)
    assert high.binding_belt == "recycler-output"
    plain = plan_factory(RECIPE, _machine(productivity_research=0.0), SystemOutput.ITEMS)
    assert plain.binding_belt == "tier0-input"


@pytest.mark.parametrize("mode,kw", [
    (SystemOutput.ITEMS, {}),
    (SystemOutput.INGREDIENTS, {"target_ingredient": "copper-cable"}),
])
def test_discrete_no_starvation(mode, kw):
    machine = _machine(productivity_research=50.0)
    plan = plan_factory(RECIPE, machine, mode, **kw)
    cr = craft_rate(machine.assembler_speed, RECIPE.craft_time)
    for row in plan.tier_rows:
        assert isinstance(row.assembler_count, int)
        assert row.assembler_count >= row.fractional_assemblers
        assert row.assembler_count * cr + 1e-9 >= row.fractional_assemblers * cr
        assert 0.0 <= row.utilization <= 1.0 + 1e-9
    assert isinstance(plan.recycler_count, int)
    assert plan.recycler_count >= plan.recycler_fractional


def test_modes_and_missing_bank():
    items = plan_factory(RECIPE, _machine(), SystemOutput.ITEMS)
    ing = plan_factory(RECIPE, _machine(), SystemOutput.INGREDIENTS, target_ingredient="iron-plate")

    assert items.tier_rows[4].has_assembler_bank is True
    assert "item" in items.tier_rows[4].role
    assert items.target_name == "electronic-circuit"

    ing_t4 = ing.tier_rows[4]
    assert ing_t4.has_assembler_bank is False
    assert ing_t4.assembler_count == 0
    assert "no bank" in ing_t4.role
    assert ing.target_name == "iron-plate"


def test_ingredient_mode_requires_valid_target():
    with pytest.raises(ValueError, match="requires target_ingredient"):
        plan_factory(RECIPE, _machine(), SystemOutput.INGREDIENTS)
    with pytest.raises(KeyError):
        plan_factory(RECIPE, _machine(), SystemOutput.INGREDIENTS, target_ingredient="nope")
    with pytest.raises(ValueError, match="only valid for ingredient"):
        plan_factory(RECIPE, _machine(), SystemOutput.ITEMS, target_ingredient="iron-plate")


def test_productivity_does_not_leak_into_craft_rate():
    """craft_rate depends only on speed/craft_time, never productivity."""
    cr = craft_rate(5.0, 0.5)
    assert cr == pytest.approx(5.0 / 0.5)
    low = plan_factory(RECIPE, _machine(productivity_research=0.0), SystemOutput.ITEMS)
    high = plan_factory(RECIPE, _machine(productivity_research=200.0), SystemOutput.ITEMS)
    assert high.efficiency_pct > low.efficiency_pct
    assert craft_rate(5.0, 0.5) == cr  # unchanged regardless of prod


def test_mixed_belt_scales_with_total_ingredients():
    """Doubling every ingredient count doubles N, so lambda (and the per-tier and
    recycler fractional demands) halve, while efficiency is unchanged."""
    doubled = RecipeSpec(
        ingredients=tuple(Ingredient(i.name, i.count * 2) for i in RECIPE.ingredients),
        craft_time=RECIPE.craft_time, output_yield=RECIPE.output_yield,
    )
    machine = _machine(productivity_research=100.0)
    base = plan_factory(RECIPE, machine, SystemOutput.ITEMS)
    dbl = plan_factory(doubled, machine, SystemOutput.ITEMS)

    assert dbl.total_ingredients == pytest.approx(2 * base.total_ingredients)
    assert dbl.input_rate == pytest.approx(base.input_rate / 2)
    assert dbl.recycler_fractional == pytest.approx(base.recycler_fractional / 2)
    for rb, rd in zip(base.tier_rows, dbl.tier_rows):
        assert rd.fractional_assemblers == pytest.approx(rb.fractional_assemblers / 2)
    assert dbl.efficiency_pct == pytest.approx(base.efficiency_pct)


def test_efficiency_invariant_to_ingredient_multiplicity():
    """Efficiency depends only on output_yield, not how many ingredient types."""
    machine = _machine(productivity_research=100.0)
    multi = plan_factory(RECIPE, machine, SystemOutput.ITEMS)
    single = plan_factory(SINGLE, machine, SystemOutput.ITEMS)
    assert multi.efficiency_pct == pytest.approx(single.efficiency_pct)


def test_raw_input_rates_per_ingredient():
    plan = plan_factory(RECIPE, _machine(productivity_research=100.0), SystemOutput.ITEMS)
    rates = dict(plan.raw_input_rates)
    assert rates["copper-cable"] == pytest.approx(3.0 * plan.input_rate)
    assert rates["iron-plate"] == pytest.approx(1.0 * plan.input_rate)
