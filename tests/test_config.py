"""Tests for config-file loading (YAML/JSON) of factory specs."""
import json

import pytest

from quality_loop.config import (
    load_config_text,
    load_factory_config,
    parse_factory_config,
)
from quality_loop.engine import SystemOutput, Tier
from quality_loop.recipes import Ingredient, Recipe, RecipeDB

# A tiny injectable DB so config tests don't depend on the bundled file.
_DB = RecipeDB(
    recipes={
        "electronic-circuit": Recipe(
            name="electronic-circuit",
            ingredients=(Ingredient("iron-plate", 1.0), Ingredient("copper-cable", 3.0)),
            craft_time=0.5, output_yield=1.0, output_item="electronic-circuit",
            category="electronics",
        )
    },
    skipped={"sulfur": "fluid ingredient(s) not recoverable by recycling: ['water']"},
)

MINIMAL = {
    "recipe": "electronic-circuit",
    "machine": {"machine_key": "em_plant", "assembler_speed": 5.0, "recycler_speed": 0.5},
}


def parse(data):
    return parse_factory_config(data, db=_DB)


def test_defaults_and_name_lookup():
    cfg = parse(MINIMAL)
    assert cfg.output_mode is SystemOutput.ITEMS
    assert cfg.belt_cap == 240.0
    assert cfg.target_ingredient is None
    assert cfg.recipe.name == "electronic-circuit"
    assert cfg.recipe.total_ingredients == 4.0
    assert cfg.machine.module_tier == Tier.LEGENDARY.value
    assert cfg.machine.recycling_factor == pytest.approx(1 / 16)


def test_inline_recipe():
    cfg = parse({
        "recipe": {"ingredients": {"iron-plate": 2, "copper-cable": 3}, "craft_time": 0.5},
        "machine": MINIMAL["machine"],
    })
    assert cfg.recipe.total_ingredients == 5.0
    assert {(i.name, i.count) for i in cfg.recipe.ingredients} == {("iron-plate", 2.0), ("copper-cable", 3.0)}


def test_module_tier_name_and_int():
    data = {**MINIMAL, "machine": {**MINIMAL["machine"], "module_tier": "rare"}}
    assert parse(data).machine.module_tier == Tier.RARE.value
    data = {**MINIMAL, "machine": {**MINIMAL["machine"], "module_tier": 2}}
    assert parse(data).machine.module_tier == 2


def test_target_ingredient_rules():
    # required for ingredient mode
    with pytest.raises(ValueError, match="requires 'target_ingredient'"):
        parse({**MINIMAL, "output_mode": "ingredients"})
    # must be an actual ingredient
    with pytest.raises(KeyError):
        parse({**MINIMAL, "output_mode": "ingredients", "target_ingredient": "steel"})
    # valid
    cfg = parse({**MINIMAL, "output_mode": "ingredients", "target_ingredient": "copper-cable"})
    assert cfg.target_ingredient == "copper-cable"
    # not allowed in item mode
    with pytest.raises(ValueError, match="only valid when output_mode"):
        parse({**MINIMAL, "target_ingredient": "copper-cable"})


def test_unknown_recipe_and_keys():
    with pytest.raises(KeyError, match="did you mean|unknown recipe"):
        parse({**MINIMAL, "recipe": "electronic-circ"})
    with pytest.raises(ValueError, match="unknown top-level"):
        parse({**MINIMAL, "belt_capp": 240})
    with pytest.raises(ValueError, match="unknown recipe"):
        parse({"recipe": {"ingredients": {"a": 1}, "craft_time": 1, "foo": 2}, "machine": MINIMAL["machine"]})


def test_bad_machine_key_and_mode():
    with pytest.raises(ValueError, match="unknown machine_key"):
        parse({**MINIMAL, "machine": {**MINIMAL["machine"], "machine_key": "nope"}})
    with pytest.raises(ValueError, match="items.*ingredients"):
        parse({**MINIMAL, "output_mode": "both"})


def test_yaml_and_json_text_agree():
    payload = {
        "recipe": "electronic-circuit", "belt_cap": 120,
        "machine": {"machine_key": "em_plant", "assembler_speed": 4, "recycler_speed": 0.5},
    }
    yaml_text = (
        "recipe: electronic-circuit\nbelt_cap: 120\n"
        "machine:\n  machine_key: em_plant\n  assembler_speed: 4\n  recycler_speed: 0.5\n"
    )
    from_yaml = load_config_text(yaml_text, "yaml", db=_DB)
    from_json = load_config_text(json.dumps(payload), "json", db=_DB)
    assert from_yaml == from_json
    assert from_yaml.belt_cap == 120.0


def test_example_file_loads():
    cfg = load_factory_config("examples/factory.full.yaml")  # uses bundled DB
    assert cfg.recipe.name == "electronic-circuit"
    assert cfg.output_mode is SystemOutput.ITEMS


def test_schema_in_sync_with_loader_and_engine():
    """The JSON schema's keys/enums must match what the loader and engine accept,
    so editor autocomplete never drifts from the real options."""
    from quality_loop import config as cfgmod
    from quality_loop.engine import MACHINES

    with open("examples/factory.schema.json", encoding="utf-8") as f:
        schema = json.load(f)
    props = schema["properties"]

    assert set(props) == cfgmod._TOP_KEYS
    recipe_obj = next(s for s in props["recipe"]["oneOf"] if s["type"] == "object")
    assert set(recipe_obj["properties"]) == cfgmod._RECIPE_KEYS
    assert set(props["machine"]["properties"]) == cfgmod._MACHINE_KEYS
    assert set(props["machine"]["properties"]["machine_key"]["enum"]) == set(MACHINES)
    assert set(props["output_mode"]["enum"]) == {"items", "ingredients"}
    tier_names = props["machine"]["properties"]["module_tier"]["anyOf"][0]["enum"]
    assert [t.upper() for t in tier_names] == [t.name for t in Tier]
