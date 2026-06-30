"""Command-line interface: synthesize a factory from a config file.

Usage:
    python -m quality_loop.cli <config.yaml|.json>
    quality-loop <config.yaml|.json>

The config selects the recipe, machine, output mode and belt cap. See
examples/factory.full.yaml for a fully documented config.

The engine itself (efficiency tables, single loops) stays available as an
importable library -- e.g. quality_loop.efficiency(...) and
quality_loop.loop_result(...); the CLI is dedicated to factory synthesis.
"""
from __future__ import annotations

import sys

from .config import load_factory_config
from .engine import MACHINES
from .factory import plan_factory

TIER_NAMES = ["normal", "uncommon", "rare", "epic", "legendary"]

_USAGE = "usage: quality-loop <config.yaml|.json>  (see examples/factory.full.yaml)"


def _print_plan(cfg, plan) -> None:
    name = MACHINES[cfg.machine.machine_key].name
    ing = ", ".join(f"{i.count:g}x{i.name}" for i in cfg.recipe.ingredients)
    print(f"{name}: legendary {cfg.output_mode.value} loop, efficiency {plan.efficiency_pct:.4f}%")
    print(f"Recipe {cfg.recipe.name}: {ing} -> {cfg.recipe.output_yield:g}x{cfg.recipe.output_item}")
    print(f"Extract: {plan.target_name} at {plan.target_output_rate:.3f}/s")
    print(f"Input: {plan.input_rate:.3f} sets/s")
    for iname, rate in plan.raw_input_rates:
        print(f"    {iname:24} {rate:8.3f}/s")
    for iname, rate in plan.fluid_input_rates:
        print(f"    {iname:24} {rate:8.3f}/s  (fluid, piped, not recycled)")
    print(
        f"Binding belt: {plan.binding_belt} at {plan.binding_belt_flow:.1f}/s "
        f"(cap {cfg.belt_cap:g}); recycler-output={plan.phi_belt:.1f}/s, "
        f"tier0-input={plan.tier0_belt:.1f}/s"
    )
    print("Module placement (quality, productivity) per input tier:")
    for tier_name, mc in zip(TIER_NAMES, plan.module_configs):
        print(f"  {tier_name:10} -> Q{mc.n_quality} P{mc.n_productivity}")
    print(f"{'tier':10} {'bank':>5} {'count':>6} {'util':>7}  role")
    for row, tier_name in zip(plan.tier_rows, TIER_NAMES):
        bank = "yes" if row.has_assembler_bank else "no"
        print(
            f"  {tier_name:8} {bank:>5} {row.assembler_count:>6} "
            f"{row.utilization * 100:>6.1f}%  {row.role}"
        )
    print(f"Recyclers: {plan.recycler_count} (fractional {plan.recycler_fractional:.2f})")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        raise SystemExit(_USAGE)
    try:
        cfg = load_factory_config(argv[0])
        plan = plan_factory(
            cfg.recipe, cfg.machine, cfg.output_mode,
            target_ingredient=cfg.target_ingredient, belt_cap=cfg.belt_cap,
        )
    except (KeyError, ValueError, FileNotFoundError) as e:
        raise SystemExit(f"error: {e}")
    _print_plan(cfg, plan)


if __name__ == "__main__":
    main()
