# quality-loop

**Plan optimal Factorio quality factories — and the steady-state math behind them.**

`quality-loop` answers two questions about a recycler–assembler quality loop:

1. *How efficient is it?* — what fraction of normal input becomes legendary output, for a
   given machine, module layout, and recipe (the **engine**).
2. *How do I build it?* — the optimal per-tier module layout **and the exact machine counts**
   for a fixed factory topology, sized so a single shared belt is the bottleneck (the
   **factory synthesizer**).

It ships a database of real Space Age recipes, so you describe a build in a short config file
and get a concrete plan: how many assemblers per quality tier, how many recyclers, the belt
that limits you, and the input/output rates.

---

## Table of contents

- [Who this is for](#who-this-is-for)
- [Install](#install)
- [For players: synthesize a factory](#for-players-synthesize-a-factory)
  - [Quick start](#quick-start)
  - [Reading the output](#reading-the-output)
  - [Writing a config](#writing-a-config)
  - [Finding your machine speeds](#finding-your-machine-speeds)
  - [Extracting an ingredient instead of an item](#extracting-an-ingredient-instead-of-an-item)
- [Config reference](#config-reference)
- [The factory model](#the-factory-model)
- [For developers: use it as a library](#for-developers-use-it-as-a-library)
  - [Engine API](#engine-api)
  - [Factory synthesis API](#factory-synthesis-api)
  - [Recipe database API](#recipe-database-api)
- [The engine math](#the-engine-math)
- [Machines](#machines)
- [Recipe database](#recipe-database)
- [Validation](#validation)
- [Project layout](#project-layout)
- [Requirements](#requirements)

---

## Who this is for

- **Players** who want a build planned for them: point the tool at a recipe and a machine, get
  back module loadouts, machine counts, and throughput. You only write a YAML file and run one
  command.
- **Developers** who want the underlying model: a small, dependency-light, fully-typed library
  with a validated steady-state solver and a pure factory-synthesis layer on top.

---

## Install

Requires **Python 3.10+**.

```bash
# with pip (editable install from a clone)
git clone https://github.com/Benj181/quality-loop.git
cd quality-loop
pip install -e .

# or with Poetry
poetry install
```

This installs the `quality-loop` command and the importable `quality_loop` package.
Runtime dependencies are just `numpy` and `pyyaml`.

---

## For players: synthesize a factory

### Quick start

Create a file `my-build.yaml`:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Benj181/quality-loop/main/examples/factory.schema.json
recipe: electronic-circuit      # any recipe in the database
output_mode: items              # extract the legendary item
machine:
  machine_key: em_plant         # Electromagnetic plant
  assembler_speed: 5.0          # your machine's in-game crafting speed
  recycler_speed: 0.5           # your recycler's crafting speed
  module_tier: legendary        # quality of the modules you'll use
  productivity_research: 100    # +% from productivity research
```

Run it:

```bash
quality-loop my-build.yaml
```

You'll get something like:

```
Electromagnetic plant: legendary items loop, efficiency 157.6414%
Recipe electronic-circuit: 1xiron-plate, 3xcopper-cable -> 1xelectronic-circuit
Extract: electronic-circuit at 10.879/s
Input: 6.901 sets/s
    iron-plate                  6.901/s
    copper-cable               20.703/s
Binding belt: recycler-output at 240.0/s (cap 240); recycler-output=240.0/s, tier0-input=93.0/s
Module placement (quality, productivity) per input tier:
  normal     -> Q0 P5
  uncommon   -> Q0 P5
  rare       -> Q0 P5
  epic       -> Q0 P5
  legendary  -> Q0 P5
tier        bank  count    util  role
  normal     yes      3   77.5%  assemble + recycle
  uncommon   yes      2   82.6%  assemble + recycle
  rare       yes      2   66.9%  assemble + recycle
  epic       yes      2   54.3%  assemble + recycle
  legendary   yes      1   29.0%  assemble + extract item
Recyclers: 16 (fractional 15.00)
```

### Reading the output

- **Efficiency** — legendary output per unit of normal input. With high productivity it can
  exceed 100% (productivity creates extra material).
- **Extract / Input** — your sustained output rate and the raw input you must feed in. Input is
  shown in *sets/s* (one set = one craft's worth of ingredients) and broken down per ingredient.
- **Binding belt** — the shared belt that limits the build, held at the cap (240/s by default,
  a stacked turbo belt). The tool reports both candidate belts: the mixed recycler-output belt
  and the merged tier-0 input belt, and picks whichever is fuller.
- **Module placement** — the optimal `(quality, productivity)` module split for each tier's
  assembler bank. The legendary bank is always full productivity (nothing higher to promote to).
- **Per-tier table** — how many assemblers each quality tier needs, their utilization (slack from
  rounding up), and each tier's role. `util` below 100% is headroom that keeps the belt — not the
  machines — as the bottleneck.
- **Recyclers** — how many recyclers the shared block needs.

### Writing a config

A config needs a **recipe**, a **machine**, and what to **extract**. Everything else has sensible
defaults. The recipe can be looked up by name from the bundled database, or written inline:

```yaml
recipe:
  ingredients:            # ingredient -> count per craft
    iron-plate: 1
    copper-cable: 3
  craft_time: 0.5         # base recipe time in seconds
  output_yield: 1.0
machine:
  machine_key: foundry
  assembler_speed: 4.0
  recycler_speed: 0.5
```

See [`examples/factory.full.yaml`](examples/factory.full.yaml) for a fully documented config.
If your editor has the **YAML Language Server** (e.g. the Red Hat YAML extension for VS Code),
the `# yaml-language-server: $schema=...` line at the top of that file gives you autocomplete and
validation for `machine_key`, `module_tier`, and `output_mode`.

### Finding your machine speeds

`assembler_speed` and `recycler_speed` are the machines' **effective crafting speed** — already
including machine quality, speed modules, and beacons. The model takes these as given; it does
not reconstruct them from module math. Read the crafting speed off the machine in-game (it's the
"Crafting speed" line), or compute it from the base speed × your modules/beacons. The recycler's
base crafting speed is `0.5`.

Speed only affects **machine counts** (how fast each machine crafts). It never affects efficiency
or yields — that's productivity's job — and the two are kept strictly separate.

### Extracting an ingredient instead of an item

A quality loop upcycles ingredients too. To extract a legendary *ingredient* rather than the
finished item, set `output_mode: ingredients` and name the ingredient:

```yaml
recipe: electronic-circuit
output_mode: ingredients
target_ingredient: copper-cable
machine:
  machine_key: foundry
  assembler_speed: 4.0
  recycler_speed: 0.5
  productivity_research: 250
```

In this mode the legendary tier has **no assembler bank** — the legendary ingredient is filtered
off to storage instead of being crafted further. The tool labels that row `extract ingredient
(no bank)` so a missing bank doesn't look like a bug.

---

## Config reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `recipe` | string \| mapping | — (required) | A recipe **name** from the database, or an inline recipe (`ingredients` map, `craft_time`, optional `output_yield`, `output_item`, `name`). |
| `recipe_db` | string | bundled `data/recipes.json` | Path to a recipe database JSON. |
| `output_mode` | `items` \| `ingredients` | `items` | Extract the legendary product item, or one legendary ingredient. |
| `target_ingredient` | string | — | **Required** for `ingredients` mode: which ingredient to upcycle and extract. Must be one of the recipe's ingredients. |
| `belt_cap` | number | `240` | Binding-belt cap, items/sec (240 = stacked turbo belt). |
| `machine.machine_key` | string | — (required) | One of the [machines](#machines). |
| `machine.assembler_speed` | number | — (required) | Measured assembler crafting speed. |
| `machine.recycler_speed` | number | — (required) | Measured recycler crafting speed. |
| `machine.module_tier` | name \| 0–4 | `legendary` | Quality tier of the modules. |
| `machine.productivity_research` | number | `0` | Productivity research %, added to total machine productivity (the engine clamps the total at +300%). |
| `machine.recycling_factor` | number | `0.0625` | Recycle time as a fraction of craft time (Factorio default 1/16). |

---

## The factory model

The synthesizer assumes one fixed, hardcoded topology:

- **Separate assembler banks per quality tier** — normal, uncommon, rare, epic (and legendary in
  item mode) are physically distinct machine groups.
- **One shared recycler block** recycles the crafted item of all tiers together.
- **One mixed shared output belt** — all recyclers dump returned ingredients onto a single belt;
  quality tiers are filtered off in sequence. The target tier is extracted; the normal-tier
  remainder is priority-merged with raw input and returned to the tier-0 banks.

Key modeling choices (and why they're correct):

- **Legendary target.** The optimizer is built around the top tier; the legendary bank carries
  only productivity modules.
- **Derived 25% recycling.** Recycling returns 25% of a recipe's ingredients in recipe proportion
  at 1/16 the craft time — exactly what Factorio's auto-generated recycling recipes do. This keeps
  the loop a single balanced commodity, which is what makes the steady-state solver exact.
- **Set units.** Because recycling returns ingredients in recipe proportion, the loop is measured
  in *crafts/sets*; physical per-ingredient rates are recovered by scaling by each ingredient's
  per-craft count.
- **Mixed belt → scales with total ingredients.** The shared belt carries the sum over all
  ingredient types, so a recipe with more ingredients per craft hits the belt cap sooner.
- **The binding belt is computed, not assumed.** Depending on productivity, either the recycler
  belt or the merged tier-0 belt can be the fuller one; the tool caps whichever binds and reports
  it. Machine counts always round **up**, so the belt — not a starved machine — stays the limit.

---

## For developers: use it as a library

`quality_loop` is split into three pure layers with a one-way dependency
(`config → factory → engine`, plus a standalone `recipes` data layer). Everything is frozen
dataclasses and pure functions; only `config`/`recipes` do I/O.

```python
import quality_loop as ql
```

### Engine API

The engine is the validated core: matrix construction, the loop solver, and the optimizer. It has
no I/O and no notion of belts or machine counts.

```python
from quality_loop import MACHINES, SystemOutput, ModuleStrategy, efficiency, loop_result

# Optimal module layout + efficiency for a machine/output/strategy:
eff_pct, configs = efficiency(
    MACHINES["em_plant"],
    SystemOutput.ITEMS,
    ModuleStrategy.OPTIMIZE,
    # extra_productivity=100.0,   # optional: productivity research %
)
# eff_pct -> 7.6852, configs -> list[ModuleConfig], one per quality tier

# Raw steady-state flow vector for the same config:
flows = loop_result(
    MACHINES["em_plant"], configs,
    input_vector=1.0, keep_items_from=4, keep_ingredients_from=None,
)
# flows is a 10-vector [ingredients(5), items(5)]; flows[9] == eff_pct/100
# (legendary items per unit of normal input). To model productivity research,
# pass extra_productivity to efficiency() AND raise the machine's base
# productivity by the same amount before loop_result (see factory.plan_factory).
```

Key types: `Machine`, `ModuleConfig`, `Tier`, `SystemOutput` (`ITEMS`/`INGREDIENTS`/`BOTH`),
`ModuleStrategy` (`OPTIMIZE`/`FULL_QUALITY`/`FULL_PRODUCTIVITY`). Matrix builders
`production_matrix`, `assembler_matrix`, `recycler_matrix`, `transition_matrix`, and the solver
`solve_loop` are also exported.

### Factory synthesis API

```python
from quality_loop import RecipeDB, RecipeSpec, MachineSpec, plan_factory, SystemOutput

db = RecipeDB.load()                                  # bundled recipe database
recipe = RecipeSpec.from_recipe(db.get("electronic-circuit"))

machine = MachineSpec(
    machine_key="em_plant",
    assembler_speed=5.0,
    recycler_speed=0.5,
    productivity_research=100.0,   # affects yields only
)

plan = plan_factory(recipe, machine, SystemOutput.ITEMS)

plan.efficiency_pct        # 157.64...
plan.input_rate            # input sets/sec (lambda)
plan.raw_input_rates       # [("iron-plate", 6.90), ("copper-cable", 20.70)]
plan.target_output_rate    # legendary items/sec
plan.binding_belt          # "recycler-output" or "tier0-input"
plan.recycler_count        # int
for row in plan.tier_rows:                 # one TierRow per quality tier
    row.tier, row.assembler_count, row.utilization, row.has_assembler_bank, row.role
```

`plan_factory(recipe, machine, output_mode, *, target_ingredient=None, belt_cap=240.0)` returns a
frozen `FactoryPlan`. For `SystemOutput.INGREDIENTS` you must pass `target_ingredient`. The pure
helper `craft_rate(speed, craft_time)` exposes the per-machine craft rate (deliberately takes no
productivity argument — speed and productivity never multiply).

You can build a `RecipeSpec` inline too, without the database:

```python
from quality_loop import Ingredient
recipe = RecipeSpec(
    ingredients=(Ingredient("iron-plate", 1.0), Ingredient("copper-cable", 3.0)),
    craft_time=0.5, output_yield=1.0, name="electronic-circuit",
    output_item="electronic-circuit",
)
```

### Recipe database API

```python
from quality_loop import RecipeDB

db = RecipeDB.load()                  # or RecipeDB.load("path/to/recipes.json")
r = db.get("electronic-circuit")
r.ingredients          # (Ingredient("iron-plate", 1.0), Ingredient("copper-cable", 3.0))
r.craft_time           # 0.5
r.total_ingredients    # 4.0
r.recycle_yields()     # derived 25% returns: iron-plate 0.25, copper-cable 0.75
```

`get()` raises a helpful `KeyError` with close-match suggestions for typos, and explains *why* a
recipe is unavailable if it was deliberately skipped (e.g. a fluid ingredient that recycling can't
recover). `db.skipped` holds those reasons.

To load config files programmatically, `quality_loop.config.load_factory_config(path)` returns a
`FactoryConfig` (recipe, machine, output_mode, belt_cap, target_ingredient).

---

## The engine math

The loop alternates two steps: an assembler crafts ingredients into items; a recycler returns 25%
of items as ingredients. Both can carry quality modules, so material climbs the five quality tiers
(normal → legendary) over many passes.

The infinite loop is unrolled into a linear chain and solved with a block transition matrix over a
10-dimensional state `[ingredients(5), items(5)]`:

$$ T = \begin{bmatrix} 0 & A \\ R & 0 \end{bmatrix}, \qquad
   \vec{t} = \sum_{x=0}^{\infty} \vec{t_0}\, T^{x} $$

where `A` (5×5) is the assembler production matrix and `R` (5×5) the recycler's. Each row `i` of a
production matrix distributes one unit of tier-`i` input over output tiers, accounting for the
quality chance `q`, the multi-tier promotion distribution (90% +1 tier, 9% +2, 0.9% +3, 0.09% +4,
renormalized), and the output multiplier `(1 + productivity)` (assembler) or `0.25` (recycler).
The top tier takes no quality penalty since there is nothing higher to promote to.

The geometric series is summed iteratively until the residual flow falls below tolerance. Loops
above the +300% productivity cap are net-positive and will not converge for item output (the
solver raises with an explanatory message); query ingredient output instead, which stays lossless.

### Module constants (Factorio 2.0, verified against the wiki)

Tier multiplier `×{1.0, 1.3, 1.6, 1.9, 2.5}` applied to the base bonus:

- Quality module 3: base 2.5% → legendary 6.25% per module.
- Productivity module 3: base 10% → legendary 25% per module.

The in-game UI truncates 6.25% to 6.2% (and 3.25% → 3.2%, 4.75% → 4.7%); the solver uses the true
value by default. This is a display-only quirk: a Factorio dev-forum post confirms the shown
numbers are "rounded down (technically incorrectly)" while the game rolls on the precise internal
value ([forums.factorio.com/viewtopic.php?t=121747](https://forums.factorio.com/viewtopic.php?t=121747);
multipliers per [wiki.factorio.com/Quality_module](https://wiki.factorio.com/Quality_module)).
Pass a lower `module_tier` for non-legendary modules.

---

## Machines

| `machine_key` | Machine | Module slots | Base productivity |
|---------------|---------|:------------:|:-----------------:|
| `electric_furnace` | Electric furnace / Centrifuge | 2 | 0% |
| `chemical_plant` | Chemical plant | 3 | 0% |
| `assembling_machine` | Assembling machine 3 | 4 | 0% |
| `foundry` | Foundry / Biochamber | 4 | 50% |
| `em_plant` | Electromagnetic plant | 5 | 50% |
| `cryogenic_plant` | Cryogenic plant | 8 | 0% |

`machine_key` selects module slots and base productivity. Measured crafting speeds are supplied
separately in the config (they encode quality, speed modules, and beacons).

---

## Recipe database

The bundled [`data/recipes.json`](data/recipes.json) is generated from a Factorio `data.raw` dump.
Recipes with fluid ingredients (not recoverable by recycling) and multi-output recipes without a
clear primary product are skipped, with the reason recorded under `_skipped`.

To regenerate it from a new dump (e.g. for a different game version or mod set), use the offline
converter — it is a standalone script the runtime never imports:

```bash
python scripts/convert_dump.py path/to/data-raw-dump.txt data/recipes.json
```

---

## Validation

`tests/test_engine.py` reproduces a reference blog's worked example to `1e-4` and Konage's
efficiency table (assembling machine, foundry, EM plant, cryo plant) to `1e-5` using the in-game
truncated constants. The factory layer is pinned to the engine by a tie-back test: synthesized
`output_rate / input_rate` equals the engine's efficiency to `1e-9`, which fails loudly if
productivity is ever double-counted. Run the whole suite with:

```bash
pytest -q
```

---

## Project layout

| File | Role |
|------|------|
| `src/quality_loop/engine.py` | Pure matrix construction, loop solver, optimizer. **Sealed/validated — no I/O.** |
| `src/quality_loop/factory.py` | Factory synthesis: optimal modules + discrete machine counts. Pure. |
| `src/quality_loop/recipes.py` | Runtime recipe database (reads the normalized JSON). |
| `src/quality_loop/config.py` | YAML/JSON config loading and validation. |
| `src/quality_loop/cli.py` | Config-driven command-line entry point. |
| `scripts/convert_dump.py` | Offline `data.raw` → `recipes.json` converter (not imported at runtime). |
| `data/recipes.json` | Bundled recipe database. |
| `examples/factory.full.yaml` | Fully documented example config. |
| `examples/factory.schema.json` | JSON Schema for editor autocomplete/validation. |

The engine is the source of truth; the factory layer depends on it and never the reverse.

---

## Requirements

- Python 3.10+
- `numpy`, `pyyaml` (runtime); `pytest` (dev)
