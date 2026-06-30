"""Config-file loading for factory synthesis.

Parses a YAML or JSON config into the inputs of plan_factory. This keeps the
factory layer pure (no I/O): config.py reads files, factory.py only computes.

A recipe is given either by name (looked up in the recipe database) or inline.
See examples/factory.full.yaml for a fully documented config.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .engine import MACHINES, SystemOutput, Tier
from .factory import BELT_CAP, RECYCLING_FACTOR, MachineSpec, RecipeSpec
from .recipes import Ingredient, RecipeDB

_TOP_KEYS = {"output_mode", "belt_cap", "recipe", "recipe_db", "target_ingredient", "machine"}
_RECIPE_KEYS = {"ingredients", "craft_time", "output_yield", "output_item", "name"}
_MACHINE_KEYS = {
    "machine_key", "assembler_speed", "recycler_speed",
    "module_tier", "productivity_research", "recycling_factor",
}


@dataclass(frozen=True)
class FactoryConfig:
    """Everything plan_factory needs, parsed from a config file."""
    recipe: RecipeSpec
    machine: MachineSpec
    output_mode: SystemOutput
    belt_cap: float
    target_ingredient: str | None


def _reject_unknown(data: dict, allowed: set[str], where: str) -> None:
    extra = set(data) - allowed
    if extra:
        raise ValueError(
            f"unknown {where} option(s): {sorted(extra)}; allowed: {sorted(allowed)}"
        )


def _require(data: dict, key: str, where: str):
    if key not in data:
        raise ValueError(f"missing required {where} option: {key!r}")
    return data[key]


def _parse_tier(value) -> int:
    """Accept an int 0..4 or a tier name (normal..legendary)."""
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly
        raise ValueError(f"invalid module_tier: {value!r}")
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return Tier[str(value).upper()].value
    except KeyError:
        raise ValueError(f"invalid module_tier: {value!r}") from None


def _parse_recipe(data: dict, db: RecipeDB | None) -> RecipeSpec:
    recipe_field = _require(data, "recipe", "top-level")
    if isinstance(recipe_field, str):
        if db is None:
            db = RecipeDB.load(data.get("recipe_db"))
        return RecipeSpec.from_recipe(db.get(recipe_field))
    if isinstance(recipe_field, dict):
        _reject_unknown(recipe_field, _RECIPE_KEYS, "recipe")
        ing_map = _require(recipe_field, "ingredients", "recipe")
        if not isinstance(ing_map, dict) or not ing_map:
            raise ValueError("inline recipe 'ingredients' must be a non-empty mapping name->count")
        ingredients = tuple(Ingredient(str(k), float(v)) for k, v in ing_map.items())
        return RecipeSpec(
            ingredients=ingredients,
            craft_time=float(_require(recipe_field, "craft_time", "recipe")),
            output_yield=float(recipe_field.get("output_yield", 1.0)),
            name=str(recipe_field.get("name", "recipe")),
            output_item=str(recipe_field.get("output_item", "product")),
        )
    raise ValueError("'recipe' must be a recipe name (string) or an inline mapping")


def parse_factory_config(data, db: RecipeDB | None = None) -> FactoryConfig:
    """Validate a parsed mapping and build a FactoryConfig.

    db lets callers (tests) inject a recipe database; if a recipe name is used
    and db is None, the bundled DB (or recipe_db path) is loaded.
    """
    if not isinstance(data, dict):
        raise ValueError("config root must be a mapping")
    _reject_unknown(data, _TOP_KEYS, "top-level")

    recipe = _parse_recipe(data, db)

    machine_d = _require(data, "machine", "top-level")
    if not isinstance(machine_d, dict):
        raise ValueError("'machine' must be a mapping")
    _reject_unknown(machine_d, _MACHINE_KEYS, "machine")
    machine_key = _require(machine_d, "machine_key", "machine")
    if machine_key not in MACHINES:
        raise ValueError(f"unknown machine_key {machine_key!r}; choices: {sorted(MACHINES)}")
    machine = MachineSpec(
        machine_key=machine_key,
        assembler_speed=float(_require(machine_d, "assembler_speed", "machine")),
        recycler_speed=float(_require(machine_d, "recycler_speed", "machine")),
        module_tier=_parse_tier(machine_d.get("module_tier", Tier.LEGENDARY.value)),
        productivity_research=float(machine_d.get("productivity_research", 0.0)),
        recycling_factor=float(machine_d.get("recycling_factor", RECYCLING_FACTOR)),
    )

    output_mode = SystemOutput(data.get("output_mode", "items"))
    if output_mode is SystemOutput.BOTH:
        raise ValueError("output_mode must be 'items' or 'ingredients', not 'both'")
    belt_cap = float(data.get("belt_cap", BELT_CAP))

    target_ingredient = data.get("target_ingredient")
    if output_mode is SystemOutput.INGREDIENTS:
        if target_ingredient is None:
            raise ValueError(
                "ingredient extraction requires 'target_ingredient' (one of "
                f"{[i.name for i in recipe.solid_ingredients]})"
            )
        if recipe.ingredient(target_ingredient).is_fluid:  # also validates membership
            raise ValueError(f"cannot extract fluid {target_ingredient!r}: fluids are not recycled")
    elif target_ingredient is not None:
        raise ValueError("'target_ingredient' is only valid when output_mode is 'ingredients'")

    return FactoryConfig(
        recipe=recipe, machine=machine, output_mode=output_mode,
        belt_cap=belt_cap, target_ingredient=target_ingredient,
    )


def load_config_text(text: str, fmt: str, db: RecipeDB | None = None) -> FactoryConfig:
    """Parse config from raw text. fmt is 'yaml' or 'json'."""
    if fmt == "json":
        data = json.loads(text)
    elif fmt == "yaml":
        import yaml  # imported lazily so json-only use needs no PyYAML
        data = yaml.safe_load(text)
    else:
        raise ValueError(f"unsupported config format: {fmt!r}")
    return parse_factory_config(data, db)


def load_factory_config(path: str, db: RecipeDB | None = None) -> FactoryConfig:
    """Load and parse a factory config from a .yaml/.yml or .json file."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".yaml", ".yml"):
        fmt = "yaml"
    elif ext == ".json":
        fmt = "json"
    else:
        raise ValueError(f"unsupported config extension {ext!r}; use .yaml, .yml or .json")
    with open(path, encoding="utf-8") as f:
        return load_config_text(f.read(), fmt, db)
