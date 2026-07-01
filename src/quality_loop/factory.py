"""Factory synthesis on top of the sealed quality-loop engine.

Where engine.py *analyses* a given loop, this layer *synthesizes* one: given a
recipe and measured machine speeds, it returns the optimal per-tier module config
(from the engine optimizer) plus discrete machine counts for a fixed topology,
sized so one shared belt is the bottleneck.

Fixed topology (hardcoded, not a parameter):
  - Separate assembler banks per quality tier (physically distinct groups).
  - One shared recycler block recycling the crafted item of all tiers together.
  - All recyclers dump returned ingredients onto one shared, mixed output belt;
    quality tiers are filtered off in sequence. The target tier is extracted to
    storage; the normal-tier remainder is priority-merged with raw input and
    returned to the tier-0 banks.
  - The binding constraint is a shared belt at its fullest point, capped at
    BELT_CAP items/sec. Because the belt is mixed, its flow is the SUM over all
    ingredient types, so both shared belts scale by the total per-craft ingredient
    count N. The recycler-output belt (phi_belt) and the merged tier-0 input belt
    (tier0_belt) can each be the largest, so we cap whichever binds.

Set units: recycling returns ingredients in recipe proportion (25%), so the loop
is a single balanced commodity measured in crafts/sets. The engine runs with
recipe_ratio = output_yield; its flow vector t is per input-set. Physical
per-ingredient quantities are recovered by scaling set flows by per-craft counts.

Target tier is legendary (tier 4). Item mode extracts the legendary product item
(the tier-4 assembler bank exists); ingredient mode upcycles and extracts one
named legendary ingredient (no tier-4 assembler bank).

Speed and productivity are kept strictly separate: measured crafting speed sets
the per-machine craft rate (machine counts) and never touches the engine;
productivity research lives entirely inside the engine flow magnitudes (yields).
They must never multiply.

One-way dependency: factory -> engine, never the reverse.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Sequence

from .engine import (
    BEACON_SLOTS,
    MACHINES,
    ModuleConfig,
    ModuleStrategy,
    SystemOutput,
    Tier,
    beacon_effect,
    efficiency,
    loop_result,
)
from .recipes import RECYCLE_TIME_FACTOR, Ingredient, Recipe

BELT_CAP = 240.0  # items/sec on the binding belt (stacked turbo belt)
RECYCLING_FACTOR = RECYCLE_TIME_FACTOR  # recycle time / craft time
TARGET_TIER = Tier.LEGENDARY.value  # the engine optimizer targets the top tier


@dataclass(frozen=True)
class RecipeSpec:
    """A recipe as the synthesis layer needs it. Build from a database Recipe via
    from_recipe(), or construct inline."""
    ingredients: tuple[Ingredient, ...]
    craft_time: float
    output_yield: float = 1.0
    name: str = "recipe"
    output_item: str = "product"

    @classmethod
    def from_recipe(cls, r: Recipe) -> "RecipeSpec":
        return cls(
            ingredients=r.ingredients,
            craft_time=r.craft_time,
            output_yield=r.output_yield,
            name=r.name,
            output_item=r.output_item,
        )

    @property
    def recipe_ratio(self) -> float:
        """Items per input-set (engine's recipe_ratio in set units)."""
        return self.output_yield

    @property
    def solid_ingredients(self) -> tuple[Ingredient, ...]:
        """Item (recyclable) ingredients -- the ones that ride the loop/belt."""
        return tuple(i for i in self.ingredients if not i.is_fluid)

    @property
    def fluid_ingredients(self) -> tuple[Ingredient, ...]:
        """Fluid ingredients -- external (piped) inputs, never recycled."""
        return tuple(i for i in self.ingredients if i.is_fluid)

    @property
    def total_ingredients(self) -> float:
        """Sum of per-craft SOLID ingredient counts (mixed-belt scaling factor N).
        Fluids are excluded: recyclers never return them, so they never ride the
        shared belt."""
        return sum(i.count for i in self.solid_ingredients)

    def ingredient(self, name: str) -> Ingredient:
        for i in self.ingredients:
            if i.name == name:
                return i
        raise KeyError(
            f"{name!r} is not an ingredient of {self.name!r}; "
            f"have {[i.name for i in self.ingredients]}"
        )


@dataclass(frozen=True)
class BeaconSpec:
    """A field of speed beacons affecting one machine group.

    n_beacons beacons, each holding modules_per_beacon speed modules (0..BEACON_SLOTS;
    a partial fill of 1 module is what lets a lone beacon be the optimum). The engine
    derives BOTH the speed multiplier and the negative quality effect from this spec:
    beacon quality only scales the two together (never their ratio), so it is a plain
    input, not something to optimize.
    """
    n_beacons: int = 0
    modules_per_beacon: int = BEACON_SLOTS
    speed_module_level: int = 3
    speed_module_quality_tier: int = Tier.LEGENDARY.value  # legendary modules dominate
    beacon_quality_tier: int = Tier.NORMAL.value

    def effect(self) -> tuple[float, float]:
        """(speed_bonus_frac, quality_penalty_pp) for one affected machine."""
        return beacon_effect(
            self.n_beacons, self.modules_per_beacon, self.speed_module_level,
            self.speed_module_quality_tier, self.beacon_quality_tier,
        )


@dataclass(frozen=True)
class MachineSpec:
    """Machine identity plus measured in-game speeds.

    machine_key indexes the engine's MACHINES registry (module slots + base
    productivity). assembler_speed / recycler_speed are measured crafting_speed
    values read from the game. With NO beacon spec they already bake in machine
    quality, speed modules and beacons, so they are taken as given. When an
    assembler_beacons / recycler_beacons spec IS given, the corresponding speed is
    treated as the beacon-free base and the beacon speed multiplier is applied on
    top, while the beacon's quality penalty is threaded into the loop -- the two
    faces of the same beacons stay consistent.
    productivity_research is a single scalar (%) added to total machine
    productivity inside the engine (engine clamps the total at +300%).
    """
    machine_key: str
    assembler_speed: float
    recycler_speed: float
    module_tier: int = Tier.LEGENDARY.value
    productivity_research: float = 0.0
    recycling_factor: float = RECYCLING_FACTOR
    assembler_beacons: BeaconSpec | None = None
    recycler_beacons: BeaconSpec | None = None


@dataclass(frozen=True)
class TierRow:
    """Per-tier assembler bank summary."""
    tier: int
    has_assembler_bank: bool
    role: str
    assembler_count: int
    fractional_assemblers: float
    utilization: float


@dataclass(frozen=True)
class FactoryPlan:
    """Synthesized factory: optimal modules + discrete machine counts + rates."""
    output_mode: SystemOutput
    target_tier: int
    efficiency_pct: float
    module_configs: Sequence[ModuleConfig]
    input_rate: float                    # raw input-sets/sec (lambda)
    raw_input_rates: Sequence[tuple[str, float]]    # solid ingredients, units/sec
    fluid_input_rates: Sequence[tuple[str, float]]  # fluid (piped) inputs, units/sec
    total_ingredients: float             # N (per-craft SOLID ingredient count sum)
    target_name: str                     # what is extracted
    target_ingredient: str | None        # ingredient mode only
    target_output_rate: float            # extracted target/sec
    recycler_count: int
    recycler_fractional: float
    phi_belt: float                      # recycler-output belt flow, items/sec
    tier0_belt: float                    # merged tier-0 input belt flow, items/sec
    binding_belt: str                    # "recycler-output" or "tier0-input"
    binding_belt_flow: float             # items/sec (== belt_cap)
    tier_rows: Sequence[TierRow]
    # Beacon effects actually applied (0.0 when no beacon spec is given).
    assembler_speed_bonus: float = 0.0   # fractional (0.5 == +50%)
    recycler_speed_bonus: float = 0.0
    assembler_quality_penalty: float = 0.0   # percentage points
    recycler_quality_penalty: float = 0.0


def craft_rate(speed: float, craft_time: float) -> float:
    """Crafts per second for one machine. Productivity NEVER enters this rate:
    prod adds free output, it does not change how fast crafts complete."""
    return speed / craft_time


def plan_factory(
    recipe: RecipeSpec,
    machine: MachineSpec,
    output_mode: SystemOutput,
    *,
    target_ingredient: str | None = None,
    belt_cap: float = BELT_CAP,
) -> FactoryPlan:
    """Synthesize a factory plan for a legendary-target loop."""
    if output_mode is SystemOutput.BOTH:
        raise ValueError("plan_factory targets a single extraction (items or ingredients).")

    m = MACHINES[machine.machine_key]
    if output_mode is SystemOutput.ITEMS:
        keep_items, keep_ing, idx = TARGET_TIER, None, 5 + TARGET_TIER
        if target_ingredient is not None:
            raise ValueError("target_ingredient is only valid for ingredient extraction.")
        target_name = recipe.output_item
        n_target = 1.0
    else:
        keep_items, keep_ing, idx = None, TARGET_TIER, TARGET_TIER
        if target_ingredient is None:
            raise ValueError(
                "ingredient extraction requires target_ingredient (one of "
                f"{[i.name for i in recipe.solid_ingredients]})."
            )
        target = recipe.ingredient(target_ingredient)
        if target.is_fluid:
            raise ValueError(
                f"cannot extract fluid {target_ingredient!r}: fluids have no quality "
                "and are not recycled."
            )
        n_target = target.count
        target_name = target_ingredient

    # 0. Beacon effects. Each beacon field yields a speed bonus (used only for
    #    machine counts) and a quality penalty (threaded into the loop). With no
    #    beacon spec both are 0 and everything below is unchanged.
    asm_speed_bonus, asm_qpen = (
        machine.assembler_beacons.effect() if machine.assembler_beacons else (0.0, 0.0)
    )
    rec_speed_bonus, rec_qpen = (
        machine.recycler_beacons.effect() if machine.recycler_beacons else (0.0, 0.0)
    )

    # 1. Optimal per-tier modules (intensive: independent of counts and belt).
    eff_pct, configs = efficiency(
        m, output_mode, ModuleStrategy.OPTIMIZE,
        quality_module_tier=machine.module_tier,
        prod_module_tier=machine.module_tier,
        recipe_ratio=recipe.recipe_ratio,
        extra_productivity=machine.productivity_research,
        assembler_quality_penalty=asm_qpen,
        recycler_quality_penalty=rec_qpen,
    )

    # 2. Per-input-set flow for that exact config (replicates efficiency()'s
    #    internal run with unit input). Productivity lives only here.
    meff = replace(m, base_productivity=m.base_productivity + machine.productivity_research)
    t = loop_result(
        meff, configs,
        recycler_quality_tier=machine.module_tier,
        input_vector=1.0,
        recipe_ratio=recipe.recipe_ratio,
        keep_items_from=keep_items,
        keep_ingredients_from=keep_ing,
        assembler_quality_penalty=asm_qpen,
        recycler_quality_penalty=rec_qpen,
    )
    # Tie-back invariant: extracted set-output per input set == engine efficiency.
    assert math.isclose(t[idx] * 100.0, eff_pct, rel_tol=0.0, abs_tol=1e-9), (
        f"flow/efficiency mismatch: {t[idx] * 100.0} vs {eff_pct}"
    )

    # 3. Belts. Set-unit flows: phi (recycler ingredient-set output over all tiers,
    #    raw input excluded -- it merges downstream) and tier0 (raw + normal return).
    #    The shared belts are mixed, so physical flow = set-flow * N.
    N = recipe.total_ingredients
    phi_set = float(t[0:5].sum() - 1.0) * N   # recycler-output belt, items per set
    tier0_set = float(t[0]) * N               # tier-0 input belt, items per set
    if phi_set >= tier0_set:
        binding_belt, binding_set = "recycler-output", phi_set
    else:
        binding_belt, binding_set = "tier0-input", tier0_set
    lam = belt_cap / binding_set  # input-sets/sec
    # Store actual belt rates (items/sec) like every other rate in the plan.
    phi_belt = phi_set * lam
    tier0_belt = tier0_set * lam
    binding_flow = binding_set * lam  # == belt_cap

    # 4. Assembler banks (discrete), sized by crafts/sec -- ingredient-independent.
    #    Beacon speed multiplies the base speed for counts only (never yields).
    cr = craft_rate(machine.assembler_speed * (1.0 + asm_speed_bonus), recipe.craft_time)
    tier_rows = []
    for i in range(5):
        bank = keep_ing is None or i < keep_ing
        if i == TARGET_TIER:
            role = "assemble + extract item" if output_mode is SystemOutput.ITEMS \
                else "extract ingredient (no bank)"
        else:
            role = "assemble + recycle"
        if bank:
            frac = lam * float(t[i]) / cr
            count = math.ceil(frac)
            util = frac / count if count else 0.0
        else:
            frac, count, util = 0.0, 0, 0.0
        tier_rows.append(TierRow(i, bank, role, count, frac, util))

    # 5. Recycler block (discrete). Recycled item tiers are those whose recycler
    #    row is not zeroed by keep_items. The recycler processes the single product
    #    item, so sizing uses item flow.
    recycle_time = recipe.craft_time * machine.recycling_factor
    # Beacon speed multiplies the base recycler speed for counts only.
    per_machine = machine.recycler_speed * (1.0 + rec_speed_bonus) / recycle_time
    recycler_frac = 0.0
    for i in range(5):
        if keep_items is None or i < keep_items:
            recycler_frac += lam * float(t[5 + i]) / per_machine
    recycler_count = math.ceil(recycler_frac)

    # 6. Rates. Solids are looped inputs (on the belt); fluids are external/piped.
    raw_input_rates = tuple((ing.name, lam * ing.count) for ing in recipe.solid_ingredients)
    fluid_input_rates = tuple((ing.name, lam * ing.count) for ing in recipe.fluid_ingredients)
    target_output_rate = lam * float(t[idx]) * n_target

    return FactoryPlan(
        output_mode=output_mode,
        target_tier=TARGET_TIER,
        efficiency_pct=eff_pct,
        module_configs=configs,
        input_rate=lam,
        raw_input_rates=raw_input_rates,
        fluid_input_rates=fluid_input_rates,
        total_ingredients=N,
        target_name=target_name,
        target_ingredient=target_ingredient,
        target_output_rate=target_output_rate,
        recycler_count=recycler_count,
        recycler_fractional=recycler_frac,
        phi_belt=phi_belt,
        tier0_belt=tier0_belt,
        binding_belt=binding_belt,
        binding_belt_flow=binding_flow,
        tier_rows=tier_rows,
        assembler_speed_bonus=asm_speed_bonus,
        recycler_speed_bonus=rec_speed_bonus,
        assembler_quality_penalty=asm_qpen,
        recycler_quality_penalty=rec_qpen,
    )


@dataclass(frozen=True)
class SweepRow:
    """One enumerated beacon option in a beacon comparison sweep.

    Each option is a single beacon (n_beacons = 1) of the given quality holding
    `modules` legendary Speed-3 modules, placed on `placement` machines.
    placement is one of "none" (baseline), "recycler", "assembler", "both".
    """
    placement: str
    beacon_quality_tier: int             # 0 (normal) or 4 (legendary)
    modules: int                         # 1 or 2 (legendary Spd-3 modules); 0 for baseline
    recycler_speed_bonus: float          # fractional
    assembler_speed_bonus: float
    recycler_quality_penalty: float      # percentage points
    assembler_quality_penalty: float
    efficiency_pct: float
    recycler_count: int
    total_assemblers: int
    target_output_rate: float
    machines_per_output: float           # (recyclers + assemblers) / output -- the objective
    is_optimum: bool = False             # min machines_per_output over the sweep
    is_fewest_recyclers: bool = False    # min recycler_count over the sweep


def sweep_beacons(
    recipe: RecipeSpec,
    machine: MachineSpec,
    output_mode: SystemOutput,
    *,
    speed_module_level: int = 3,
    target_ingredient: str | None = None,
    belt_cap: float = BELT_CAP,
) -> list[SweepRow]:
    """Compare a small, fixed set of speed-beacon options against the no-beacon case.

    Each option is ONE beacon holding 1 or 2 legendary Speed-3 modules, of normal or
    legendary beacon quality, placed on the recycler, the assembler banks, or both --
    12 options plus the no-beacon baseline. (A single beacon keeps the comparison
    concrete; beacon quality is included even though it is efficiency-neutral, so its
    footprint/granularity effect is visible.)

    Objective: minimize machines_per_output = (recyclers + assemblers) / output.
    Beacons are not charged in the numerator (cheap/shareable). Returns all rows with
    the objective-optimal and fewest-recycler rows flagged, baseline first.
    """
    def _make_row(placement, q_tier, mods):
        beacon = (
            None if placement == "none" else
            BeaconSpec(
                n_beacons=1, modules_per_beacon=mods,
                speed_module_level=speed_module_level,
                speed_module_quality_tier=Tier.LEGENDARY.value,  # legendary modules dominate
                beacon_quality_tier=q_tier,
            )
        )
        spec = replace(
            machine,
            assembler_beacons=beacon if placement in ("assembler", "both") else None,
            recycler_beacons=beacon if placement in ("recycler", "both") else None,
        )
        plan = plan_factory(
            recipe, spec, output_mode,
            target_ingredient=target_ingredient, belt_cap=belt_cap,
        )
        total_assemblers = sum(r.assembler_count for r in plan.tier_rows)
        total = plan.recycler_count + total_assemblers
        mpo = total / plan.target_output_rate if plan.target_output_rate > 0 else float("inf")
        return SweepRow(
            placement=placement,
            beacon_quality_tier=q_tier,
            modules=0 if placement == "none" else mods,
            recycler_speed_bonus=plan.recycler_speed_bonus,
            assembler_speed_bonus=plan.assembler_speed_bonus,
            recycler_quality_penalty=plan.recycler_quality_penalty,
            assembler_quality_penalty=plan.assembler_quality_penalty,
            efficiency_pct=plan.efficiency_pct,
            recycler_count=plan.recycler_count,
            total_assemblers=total_assemblers,
            target_output_rate=plan.target_output_rate,
            machines_per_output=mpo,
        )

    rows: list[SweepRow] = [_make_row("none", Tier.NORMAL.value, 0)]
    for placement in ("recycler", "assembler", "both"):
        for q_tier in (Tier.NORMAL.value, Tier.LEGENDARY.value):
            for mods in (1, 2):
                rows.append(_make_row(placement, q_tier, mods))

    opt = min(rows, key=lambda r: r.machines_per_output)
    fewest = min(rows, key=lambda r: r.recycler_count)
    return [
        replace(r, is_optimum=(r is opt), is_fewest_recyclers=(r is fewest))
        for r in rows
    ]
