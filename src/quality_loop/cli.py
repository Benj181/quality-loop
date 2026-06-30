"""Command-line interface for the Factorio quality loop solver.

Examples
--------
    # Optimal module placement, EM plant, keep legendary items:
    python -m quality_loop.cli efficiency --machine em_plant --output items --strategy optimize

    # Full table across all machines:
    python -m quality_loop.cli table

    # Single loop with explicit per-tier module config:
    python -m quality_loop.cli loop --machine assembling_machine \\
        --assembler-modules 1,3 1,3 1,3 2,2 0,4

    # Blue circuits with 10 levels of productivity research:
    python -m quality_loop.cli efficiency --machine em_plant --output items \\
        --strategy optimize --extra-prod 100
"""
from __future__ import annotations

import argparse

import numpy as np

from .engine import (
    MACHINES,
    ModuleConfig,
    ModuleStrategy,
    SystemOutput,
    Tier,
    efficiency,
    loop_result,
)

TIER_NAMES = ["normal", "uncommon", "rare", "epic", "legendary"]


def _parse_module(s: str) -> tuple[int, int]:
    q, p = s.split(",")
    return int(q), int(p)


def _tier_arg(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return Tier[s.upper()].value


def cmd_efficiency(args: argparse.Namespace) -> None:
    machine = MACHINES[args.machine]
    e, cfg = efficiency(
        machine,
        SystemOutput(args.output),
        ModuleStrategy(args.strategy),
        quality_module_tier=_tier_arg(args.module_tier),
        prod_module_tier=_tier_arg(args.module_tier),
        recipe_ratio=args.recipe_ratio,
        extra_productivity=args.extra_prod,
    )
    print(f"{machine.name}: {e:.4f}% legendary {args.output} per normal input")
    if cfg is not None:
        print("Module placement (quality, productivity) per input tier:")
        for name, c in zip(TIER_NAMES, cfg):
            print(f"  {name:10} -> Q{c.n_quality} P{c.n_productivity}")


def cmd_loop(args: argparse.Namespace) -> None:
    machine = MACHINES[args.machine]
    mods = args.assembler_modules
    if len(mods) == 1:
        mods = mods * 5
    if len(mods) != 5:
        raise SystemExit("Provide either 1 or 5 --assembler-modules entries.")
    configs = [
        ModuleConfig(q, p, _tier_arg(args.module_tier), _tier_arg(args.module_tier))
        for (q, p) in mods
    ]
    keep_items = None if args.keep_items is None else _tier_arg(args.keep_items)
    keep_ing = None if args.keep_ingredients is None else _tier_arg(args.keep_ingredients)
    out = loop_result(
        machine, configs,
        input_vector=args.input,
        recipe_ratio=args.recipe_ratio,
        keep_items_from=keep_items,
        keep_ingredients_from=keep_ing,
    )
    np.set_printoptions(suppress=True, precision=5)
    print("Ingredients (normal..legendary):", out[:5])
    print("Items       (normal..legendary):", out[5:])


def cmd_table(args: argparse.Namespace) -> None:
    cols = [
        (SystemOutput.ITEMS, ModuleStrategy.FULL_QUALITY, "Q-only items"),
        (SystemOutput.ITEMS, ModuleStrategy.FULL_PRODUCTIVITY, "P-only items"),
        (SystemOutput.ITEMS, ModuleStrategy.OPTIMIZE, "opt items"),
        (SystemOutput.INGREDIENTS, ModuleStrategy.FULL_QUALITY, "Q-only ingr"),
        (SystemOutput.INGREDIENTS, ModuleStrategy.FULL_PRODUCTIVITY, "P-only ingr"),
        (SystemOutput.INGREDIENTS, ModuleStrategy.OPTIMIZE, "opt ingr"),
    ]
    header = f"{'machine':28}" + "".join(f"{c[2]:>13}" for c in cols)
    print(header)
    print("-" * len(header))
    for key, machine in MACHINES.items():
        row = f"{machine.name:28}"
        for so, st, _ in cols:
            e, _ = efficiency(
                machine, so, st,
                quality_module_tier=_tier_arg(args.module_tier),
                prod_module_tier=_tier_arg(args.module_tier),
            )
            row += f"{e:13.4f}"
        print(row)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quality_loop", description=__doc__)
    p.add_argument(
        "--module-tier", default="legendary",
        help="Module quality tier (normal..legendary or 0..4). Default legendary.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    pe = sub.add_parser("efficiency", help="Efficiency of one machine/output/strategy.")
    pe.add_argument("--machine", required=True, choices=list(MACHINES))
    pe.add_argument("--output", default="items", choices=[o.value for o in SystemOutput])
    pe.add_argument("--strategy", default="optimize", choices=[s.value for s in ModuleStrategy])
    pe.add_argument("--recipe-ratio", type=float, default=1.0)
    pe.add_argument("--extra-prod", type=float, default=0.0, help="Extra productivity %% (research).")
    pe.set_defaults(func=cmd_efficiency)

    pl = sub.add_parser("loop", help="Run one loop with explicit module config.")
    pl.add_argument("--machine", required=True, choices=list(MACHINES))
    pl.add_argument(
        "--assembler-modules", nargs="+", type=_parse_module, required=True,
        metavar="Q,P", help="Per-tier 'quality,productivity' (1 or 5 entries).",
    )
    pl.add_argument("--input", type=float, default=1.0)
    pl.add_argument("--recipe-ratio", type=float, default=1.0)
    pl.add_argument("--keep-items", default="legendary")
    pl.add_argument("--keep-ingredients", default="legendary")
    pl.set_defaults(func=cmd_loop)

    pt = sub.add_parser("table", help="Full efficiency table for all machines.")
    pt.set_defaults(func=cmd_table)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
