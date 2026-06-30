"""Runtime recipe database for factory synthesis.

Reads the normalized recipe JSON at data/recipes.json (committed; generated
offline from a Factorio data.raw dump). Recycling is derived, not stored:
recycling a crafted item returns each ingredient at 25% of its per-craft count,
taking 1/16 of the craft time. This matches the game's auto-generated recycling
recipes and keeps the loop a single balanced commodity (so the sealed engine
stays exact).

This module is pure data: it does not import the engine.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from difflib import get_close_matches

RECYCLE_RETURN = 0.25       # recycling returns 25% of ingredients
RECYCLE_TIME_FACTOR = 1.0 / 16.0  # recycling takes 1/16 of the craft time

# Default bundled DB, shipped alongside the package at <repo>/data/recipes.json.
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "recipes.json"
)


@dataclass(frozen=True)
class Ingredient:
    name: str
    count: float
    type: str = "item"  # "item" (recyclable/looped) or "fluid" (external, piped)

    @property
    def is_fluid(self) -> bool:
        return self.type == "fluid"


@dataclass(frozen=True)
class Recipe:
    """A crafting recipe. craft_time is energy_required (seconds at speed 1)."""
    name: str
    ingredients: tuple[Ingredient, ...]
    craft_time: float
    output_yield: float
    output_item: str
    category: str = "crafting"

    @property
    def recipe_ratio(self) -> float:
        """Items produced per input-set (engine's recipe_ratio in set units)."""
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
        Fluids are excluded: recyclers do not return them, so they never ride the
        shared belt."""
        return sum(i.count for i in self.solid_ingredients)

    def recycle_yields(self) -> tuple[Ingredient, ...]:
        """Derived recycler output per recycled item (25% of solid ingredients)."""
        return tuple(Ingredient(i.name, i.count * RECYCLE_RETURN) for i in self.solid_ingredients)

    def recycle_time(self, factor: float = RECYCLE_TIME_FACTOR) -> float:
        return self.craft_time * factor


@dataclass(frozen=True)
class RecipeDB:
    recipes: dict[str, Recipe]
    skipped: dict[str, str]

    @classmethod
    def from_dict(cls, data: dict) -> "RecipeDB":
        skipped = dict(data.get("_skipped", {}))
        recipes: dict[str, Recipe] = {}
        for name, r in data.items():
            if name == "_skipped":
                continue
            recipes[name] = Recipe(
                name=r["name"],
                ingredients=tuple(
                    Ingredient(i["name"], float(i["amount"]), i.get("type", "item"))
                    for i in r["ingredients"]
                ),
                craft_time=float(r["energy_required"]),
                output_yield=float(r["output_yield"]),
                output_item=r["output_item"],
                category=r.get("category", "crafting"),
            )
        return cls(recipes=recipes, skipped=skipped)

    @classmethod
    def load(cls, path: str | None = None) -> "RecipeDB":
        with open(path or _DEFAULT_DB_PATH, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def get(self, name: str) -> Recipe:
        if name in self.recipes:
            return self.recipes[name]
        if name in self.skipped:
            raise KeyError(
                f"recipe {name!r} is in the dump but unsupported for the loop model: "
                f"{self.skipped[name]}"
            )
        near = get_close_matches(name, self.recipes, n=5)
        hint = f"; did you mean: {near}" if near else ""
        raise KeyError(f"unknown recipe {name!r}{hint}")
