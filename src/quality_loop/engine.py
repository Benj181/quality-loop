"""Factorio quality recycler-assembler loop solver.

Steady-state production rates for a recycler-assembler quality upcycle loop,
via the transition-matrix / loop-unrolling method:

    T = [[0, A],
         [R, 0]]            (block 10x10)

    t_total = sum_x  t_0 @ T^x    (geometric series, summed iteratively)

The 5 quality tiers are indexed 0..4 (normal, uncommon, rare, epic, legendary).
A state vector has 10 entries: [ingredients(5), items(5)].

References for the math: dfamonteiro.com recycler-assembler-loop post and the
Factorio wiki Quality page. Numeric module constants verified against the wiki
(quality module 3 = 2.5% base, productivity module 3 = 10% base; tier
multipliers x1.0/1.3/1.6/1.9/2.5).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from itertools import product
from typing import Sequence

import numpy as np

N_TIERS = 5  # normal, uncommon, rare, epic, legendary

# Quality tier multiplier applied to a module's base bonus.
# index 0 = normal module ... index 4 = legendary module.
# These multipliers are the base Quality module's per-tier bonuses
# (1.0/1.3/1.6/1.9/2.5%), taken verbatim from the wiki module table:
#   https://wiki.factorio.com/Quality_module
# We use the *exact* values, not the truncated numbers shown in-game. A
# legendary Quality module 3 is 2.5% x 2.5 = 6.25%, which the game's UI (and
# the wiki's derived tables) truncate to "6.2%" -- likewise 3.25% -> "3.2%"
# and 4.75% -> "4.7%". Confirmed by a Factorio dev-forum post: the displayed
# values are "rounded down (technically incorrectly)" while the game uses the
# precise internal value for the actual roll:
#   https://forums.factorio.com/viewtopic.php?t=121747
TIER_MULT = (1.0, 1.3, 1.6, 1.9, 2.5)

# Base per-module bonuses (highest module level: Q3 / Prod3).
QUAL3_BASE = 2.5   # % quality chance per quality-module-3
PROD3_BASE = 10.0  # % productivity per productivity-module-3
# Lower module levels, if ever needed: Q1=1.0 Q2=2.0 ; Prod1=4 Prod2=6.
QUAL_BASE_BY_LEVEL = {1: 1.0, 2: 2.0, 3: 2.5}
PROD_BASE_BY_LEVEL = {1: 4.0, 2: 6.0, 3: 10.0}

RECYCLER_PRODUCTIVITY = -75.0  # recycler returns 25% -> effective -75% prod
PROD_CAP = 300.0  # game caps total productivity bonus at +300% (i.e. 400% total)


class Tier(int, Enum):
    NORMAL = 0
    UNCOMMON = 1
    RARE = 2
    EPIC = 3
    LEGENDARY = 4


class SystemOutput(Enum):
    ITEMS = "items"
    INGREDIENTS = "ingredients"
    BOTH = "both"


class ModuleStrategy(Enum):
    FULL_QUALITY = "quality"
    FULL_PRODUCTIVITY = "productivity"
    OPTIMIZE = "optimize"


@dataclass(frozen=True)
class ModuleConfig:
    """Module loadout for a single machine slot-group.

    n_quality + n_productivity must not exceed the machine's slot count;
    the caller is responsible for that. Module *quality tier* is shared
    across the quality modules and (separately) the productivity modules.
    """
    n_quality: int = 0
    n_productivity: int = 0
    quality_module_tier: int = Tier.LEGENDARY.value
    prod_module_tier: int = Tier.LEGENDARY.value
    quality_module_level: int = 3  # Q1/Q2/Q3
    prod_module_level: int = 3

    def quality_chance(self) -> float:
        base = QUAL_BASE_BY_LEVEL[self.quality_module_level]
        return self.n_quality * base * TIER_MULT[self.quality_module_tier]

    def productivity_bonus(self) -> float:
        base = PROD_BASE_BY_LEVEL[self.prod_module_level]
        return self.n_productivity * base * TIER_MULT[self.prod_module_tier]


@dataclass(frozen=True)
class Machine:
    """An assembling-type machine (the crafting side of the loop)."""
    name: str
    module_slots: int
    base_productivity: float = 0.0  # % intrinsic (e.g. EM plant 50, foundry 50)


# Common machines: (slots, base productivity %).
MACHINES = {
    "electric_furnace": Machine("Electric furnace/Centrifuge", 2, 0.0),
    "chemical_plant": Machine("Chemical plant", 3, 0.0),
    "assembling_machine": Machine("Assembling machine 3", 4, 0.0),
    "foundry": Machine("Foundry/Biochamber", 4, 50.0),
    "em_plant": Machine("Electromagnetic plant", 5, 50.0),
    "cryogenic_plant": Machine("Cryogenic plant", 8, 0.0),
}


def _quality_roll_distribution(n_jumps_max: int) -> np.ndarray:
    """Probability of jumping k tiers GIVEN a quality roll succeeded.

    Factorio: 90% one tier, 9% two, 0.9% three, 0.09% four, ... (x0.1 each),
    renormalized over the achievable jumps from the current tier.
    Returns array of length n_jumps_max (index 0 -> +1 tier).
    """
    if n_jumps_max <= 0:
        return np.array([])
    weights = np.array([0.9 * (0.1 ** k) for k in range(n_jumps_max)])
    weights[-1] = 1.0 - weights[:-1].sum()  # remaining mass lumps into top jump
    return weights


def production_matrix(
    per_tier: Sequence[tuple[float, float]],
) -> np.ndarray:
    """Build a 5x5 production matrix from per-tier (quality%, productivity_mult).

    per_tier[i] = (quality_chance_percent, output_multiplier) for input tier i,
    where output_multiplier = 1 + productivity_bonus (for an assembler) or the
    recycler return fraction (0.25) scaled by its productivity.

    Row i, col j = expected units of tier-j output per unit of tier-i input.
    Quality promotions cascade upward; the multiplier applies to all output.
    """
    M = np.zeros((N_TIERS, N_TIERS))
    for i, (q_pct, out_mult) in enumerate(per_tier):
        q = max(0.0, q_pct) / 100.0
        # At the top tier there is no higher quality to promote to, so the
        # quality roll is wasted and all output stays at this tier.
        if i == N_TIERS - 1:
            q = 0.0
        # Fraction staying at tier i:
        M[i, i] = (1.0 - q) * out_mult
        if q > 0 and i < N_TIERS - 1:
            dist = _quality_roll_distribution(N_TIERS - 1 - i)
            for k, w in enumerate(dist):
                M[i, i + 1 + k] = q * w * out_mult
    return M


def assembler_matrix(
    configs: Sequence[ModuleConfig],
    base_productivity: float,
    recipe_ratio: float,
    keep_from_tier: int | None,
) -> np.ndarray:
    """5x5 assembler production matrix.

    configs: one ModuleConfig per input tier (length 5).
    base_productivity: machine intrinsic + research productivity (%).
    recipe_ratio: items produced per ingredient (recipe output/input ratio).
    keep_from_tier: tiers >= this are removed (row zeroed). None => keep nothing.
    """
    per_tier = []
    for i, cfg in enumerate(configs):
        prod = base_productivity + cfg.productivity_bonus()
        prod = min(prod, PROD_CAP)
        out_mult = (1.0 + prod / 100.0) * recipe_ratio
        per_tier.append((cfg.quality_chance(), out_mult))
    M = production_matrix(per_tier)
    if keep_from_tier is not None:
        for t in range(keep_from_tier, N_TIERS):
            M[t, :] = 0.0
    return M


def recycler_matrix(
    configs: Sequence[ModuleConfig],
    recipe_ratio: float,
    keep_from_tier: int | None,
) -> np.ndarray:
    """5x5 recycler production matrix. Recycler has -75% productivity (25% return)
    and converts items back to ingredients, dividing out the recipe ratio."""
    per_tier = []
    for cfg in configs:
        # recycler: 25% return, modified by its (negative) productivity floor.
        out_mult = (1.0 + RECYCLER_PRODUCTIVITY / 100.0) / recipe_ratio
        per_tier.append((cfg.quality_chance(), out_mult))
    M = production_matrix(per_tier)
    if keep_from_tier is not None:
        for t in range(keep_from_tier, N_TIERS):
            M[t, :] = 0.0
    return M


def transition_matrix(R: np.ndarray, A: np.ndarray) -> np.ndarray:
    """Block matrix T = [[0, A],[R, 0]] (10x10).
    State layout: [ingredients(0..4), items(5..9)]."""
    T = np.zeros((10, 10))
    T[0:5, 5:10] = A
    T[5:10, 0:5] = R
    return T


def solve_loop(
    T: np.ndarray,
    input_vector: np.ndarray,
    tol: float = 1e-12,
    max_iter: int = 100_000,
) -> np.ndarray:
    """Sum the unrolled loop: t_total = sum_x input @ T^x.

    Returns the 10-vector of accumulated flows. For kept tiers the value is the
    production rate; for reprocessed tiers it is the internal flow rate.
    Raises if it fails to converge (e.g. net-positive loop above prod cap).
    """
    flows = input_vector.astype(float).copy()
    current = input_vector.astype(float).copy()
    for _ in range(max_iter):
        current = current @ T
        flows += current
        if np.abs(current).sum() < tol:
            return flows
    raise RuntimeError(
        "Loop did not converge: likely a net-positive (efficiency-capped) "
        "configuration. Switch system output to ingredients, or reduce productivity."
    )


# ---- High-level driver ---------------------------------------------------

def _configs_from_tuple(
    n_quality: int,
    n_productivity: int,
    quality_tier: int,
    prod_tier: int,
) -> list[ModuleConfig]:
    return [
        ModuleConfig(n_quality, n_productivity, quality_tier, prod_tier)
        for _ in range(N_TIERS)
    ]


def loop_result(
    machine: Machine,
    assembler_configs: Sequence[ModuleConfig],
    *,
    recycler_quality_tier: int = Tier.LEGENDARY.value,
    recycler_quality_modules: int | None = None,
    input_vector: np.ndarray | float = 1.0,
    recipe_ratio: float = 1.0,
    keep_items_from: int | None = Tier.LEGENDARY.value,
    keep_ingredients_from: int | None = Tier.LEGENDARY.value,
) -> np.ndarray:
    """Run a full loop and return the 10-vector of flows.

    recycler_quality_modules: how many quality modules the recycler runs
        (defaults to 4, the standard "always quality in recycler" choice).
    """
    rq = 4 if recycler_quality_modules is None else recycler_quality_modules
    recycler_configs = [
        ModuleConfig(rq, 0, recycler_quality_tier, recycler_quality_tier)
        for _ in range(N_TIERS)
    ]
    # Removing legendary ITEMS means they never enter the recycler -> zero the
    # recycler's corresponding rows. Removing legendary INGREDIENTS means they
    # never enter the assembler -> zero the assembler's corresponding rows.
    A = assembler_matrix(
        assembler_configs, machine.base_productivity, recipe_ratio, keep_ingredients_from
    )
    R = recycler_matrix(recycler_configs, recipe_ratio, keep_items_from)
    T = transition_matrix(R, A)
    if isinstance(input_vector, np.ndarray):
        iv = np.asarray(input_vector, dtype=float)
    else:
        iv = np.zeros(10)
        iv[0] = float(input_vector)
    return solve_loop(T, iv)


def _all_module_splits(slots: int) -> list[tuple[int, int]]:
    """All (n_quality, n_productivity) with sum <= slots."""
    return [(q, slots - q) for q in range(slots + 1)]


def efficiency(
    machine: Machine,
    system_output: SystemOutput,
    strategy: ModuleStrategy,
    *,
    quality_module_tier: int = Tier.LEGENDARY.value,
    prod_module_tier: int = Tier.LEGENDARY.value,
    recipe_ratio: float = 1.0,
    extra_productivity: float = 0.0,
) -> tuple[float, list[ModuleConfig] | None]:
    """Return (efficiency_percent, best_assembler_configs).

    Efficiency = legendary output rate per unit of normal input, as a %.
    """
    if system_output == SystemOutput.ITEMS:
        keep_items, keep_ing = Tier.LEGENDARY.value, None
        idx = 9
    elif system_output == SystemOutput.INGREDIENTS:
        keep_items, keep_ing = None, Tier.LEGENDARY.value
        idx = 4
    else:
        keep_items = keep_ing = Tier.LEGENDARY.value
        idx = None  # caller sums 4 and 9

    base_prod = machine.base_productivity + extra_productivity
    machine_eff = replace(machine, base_productivity=base_prod)

    def run(configs):
        out = loop_result(
            machine_eff, configs,
            recycler_quality_tier=quality_module_tier,
            input_vector=100.0, recipe_ratio=recipe_ratio,
            keep_items_from=keep_items, keep_ingredients_from=keep_ing,
        )
        return out[idx] if idx is not None else out[4] + out[9]

    if strategy != ModuleStrategy.OPTIMIZE:
        if strategy == ModuleStrategy.FULL_PRODUCTIVITY:
            nq, npr = 0, machine.module_slots
        else:
            nq, npr = machine.module_slots, 0
        configs = _configs_from_tuple(nq, npr, quality_module_tier, prod_module_tier)
        return run(configs), configs

    # OPTIMIZE: search per-tier module splits independently. The legendary-tier
    # crafter never carries quality modules (nothing higher to upgrade to), so
    # it is fixed to full productivity. The remaining 4 tiers each pick a split.
    splits = _all_module_splits(machine.module_slots)
    legendary_cfg = ModuleConfig(
        0, machine.module_slots, quality_module_tier, prod_module_tier
    )
    best_eff, best_cfg = 0.0, None
    for combo in product(splits, repeat=N_TIERS - 1):
        configs = [
            ModuleConfig(nq, npr, quality_module_tier, prod_module_tier)
            for (nq, npr) in combo
        ]
        configs.append(legendary_cfg)
        try:
            e = run(configs)
        except RuntimeError:
            e = float("inf")  # capped / net-positive
        if e > best_eff:
            best_eff, best_cfg = e, configs
    return best_eff, best_cfg
