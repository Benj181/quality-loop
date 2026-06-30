"""Tests for the runtime recipe database (the normalized JSON DB)."""
import pytest

from quality_loop.recipes import RECYCLE_TIME_FACTOR, RecipeDB

# The normalized JSON shape committed at data/recipes.json (one entry + _skipped).
DATA = {
    "electronic-circuit": {
        "name": "electronic-circuit",
        "category": "electronics",
        "energy_required": 0.5,
        "ingredients": [
            {"name": "iron-plate", "amount": 1, "type": "item"},
            {"name": "copper-cable", "amount": 3, "type": "item"},
        ],
        "results": [{"name": "electronic-circuit", "amount": 1, "type": "item"}],
        "output_item": "electronic-circuit",
        "output_yield": 1.0,
    },
    "_skipped": {
        "sulfur": "fluid ingredient(s) not recoverable by recycling: ['water']",
    },
}


@pytest.fixture
def db():
    return RecipeDB.from_dict(DATA)


def test_multi_ingredient_fields(db):
    ec = db.get("electronic-circuit")
    assert ec.craft_time == 0.5
    assert {(i.name, i.count) for i in ec.ingredients} == {("iron-plate", 1.0), ("copper-cable", 3.0)}
    assert ec.output_yield == 1.0
    assert ec.recipe_ratio == 1.0
    assert ec.total_ingredients == 4.0


def test_derived_recycling(db):
    ec = db.get("electronic-circuit")
    yields = {y.name: y.count for y in ec.recycle_yields()}
    assert yields == {"iron-plate": 0.25, "copper-cable": 0.75}  # 25% of counts
    assert ec.recycle_time() == pytest.approx(0.5 * RECYCLE_TIME_FACTOR)


def test_lookup_errors(db):
    with pytest.raises(KeyError, match="did you mean"):
        db.get("electronic-circ")  # typo -> suggestions
    with pytest.raises(KeyError, match="unsupported for the loop model"):
        db.get("sulfur")  # skipped -> explains why


def test_bundled_db_loads():
    db = RecipeDB.load()
    ec = db.get("electronic-circuit")
    assert {(i.name, i.count) for i in ec.ingredients} == {("iron-plate", 1.0), ("copper-cable", 3.0)}
