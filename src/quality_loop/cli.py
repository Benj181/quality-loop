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
from .factory import plan_factory, sweep_beacons

TIER_NAMES = ["normal", "uncommon", "rare", "epic", "legendary"]

_USAGE = (
    "usage: quality-loop <config.yaml|.json> [--sweep-beacons]"
    "  (see examples/factory.full.yaml)"
)


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
    if plan.recycler_speed_bonus or plan.assembler_speed_bonus:
        rec_q = max(0.0, 25.0 - plan.recycler_quality_penalty)  # 25% = 4 legendary Q3 modules
        print(
            f"Beacons: recycler speed +{plan.recycler_speed_bonus * 100:.0f}% "
            f"(quality {rec_q:.1f}%, -{plan.recycler_quality_penalty:.1f}pp), "
            f"assembler speed +{plan.assembler_speed_bonus * 100:.0f}% "
            f"(-{plan.assembler_quality_penalty:.1f}pp quality)"
        )


_Q_NAMES = {0: "normal", 4: "legendary"}


def _print_sweep(cfg) -> None:
    rows = sweep_beacons(
        cfg.recipe, cfg.machine, cfg.output_mode,
        target_ingredient=cfg.target_ingredient, belt_cap=cfg.belt_cap,
    )
    print("Speed-beacon options (1 beacon of legendary Spd-3 modules):")
    print(f"  {'placement':>9} {'bcn.q':>9} {'mods':>4} {'rec.spd+':>8} {'asm.spd+':>8} "
          f"{'rec.-q':>6} {'asm.-q':>6} {'eff%':>8} {'recyc':>5} {'asm':>4} "
          f"{'out/s':>8} {'mach/out':>9}  note")
    for r in rows:
        notes = []
        if r.is_optimum:
            notes.append("*OPTIMUM (min mach/out)")
        if r.is_fewest_recyclers:
            notes.append("fewest recyclers")
        qname = _Q_NAMES.get(r.beacon_quality_tier, str(r.beacon_quality_tier)) if r.placement != "none" else "-"
        print(
            f"  {r.placement:>9} {qname:>9} {r.modules:>4} "
            f"{r.recycler_speed_bonus * 100:>7.0f}% {r.assembler_speed_bonus * 100:>7.0f}% "
            f"{r.recycler_quality_penalty:>6.1f} {r.assembler_quality_penalty:>6.1f} "
            f"{r.efficiency_pct:>8.3f} {r.recycler_count:>5} {r.total_assemblers:>4} "
            f"{r.target_output_rate:>8.3f} {r.machines_per_output:>9.4f}  {', '.join(notes)}"
        )


def _parse_args(argv: list[str]) -> tuple[str, bool]:
    """Return (config_path, do_sweep). --sweep-beacons enables the beacon comparison."""
    path = None
    do_sweep = False
    for a in argv:
        if a in ("-h", "--help"):
            raise SystemExit(_USAGE)
        if a == "--sweep-beacons":
            do_sweep = True
        elif a.startswith("-"):
            raise SystemExit(f"error: unknown option {a!r}\n{_USAGE}")
        elif path is None:
            path = a
        else:
            raise SystemExit(_USAGE)
    if path is None:
        raise SystemExit(_USAGE)
    return path, do_sweep


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    path, do_sweep = _parse_args(argv)
    try:
        cfg = load_factory_config(path)
        plan = plan_factory(
            cfg.recipe, cfg.machine, cfg.output_mode,
            target_ingredient=cfg.target_ingredient, belt_cap=cfg.belt_cap,
        )
    except (KeyError, ValueError, FileNotFoundError) as e:
        raise SystemExit(f"error: {e}")
    _print_plan(cfg, plan)
    if do_sweep:
        print()
        _print_sweep(cfg)


if __name__ == "__main__":
    main()
