"""Convert a Factorio data.raw Lua/Serpent dump into a normalized recipe JSON DB.

The dump is the full `data.raw` serialized as a Lua table (the line starts with
`Script @__DataRawSerpent__/...: ` followed by one big `{ ... }`). We only need
the top-level `recipe` table, so we brace-match just that block and parse it with
a minimal recursive Lua-table reader. Recycling recipes are ignored: recycling is
derived (25% of ingredients, time = craft_energy/16), which matches the dump.

This is a one-off, offline tool: the runtime reads the committed data/recipes.json
and never imports this module. Re-run it only to regenerate the DB from a new dump.

Usage:
    python scripts/convert_dump.py <dump.txt> [data/recipes.json]

Output JSON:
    {
      "<recipe-name>": {
        "name", "category", "energy_required",
        "ingredients": [{"name", "amount", "type"}, ...],
        "results":     [{"name", "amount", "type"}, ...],
        "output_item", "output_yield"
      },
      ...,
      "_skipped": {"<recipe-name>": "<reason>", ...}
    }
"""
from __future__ import annotations

import json
import sys

DEFAULT_ENERGY = 0.5  # Factorio default when energy_required is absent


# ---- minimal Lua-table parser (only the recipe block) --------------------

class _Lua:
    """Recursive-descent reader for the Serpent subset used by recipe data:
    tables {...} (array and key=value, mixed), "strings", numbers, booleans,
    nil, bareword keys and ["quoted"] keys, trailing commas."""

    def __init__(self, s: str):
        self.s = s
        self.i = 0
        self.n = len(s)

    def _ws(self) -> None:
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c in " \t\r\n,":
                self.i += 1
            elif c == "-" and self.i + 1 < n and s[self.i + 1] == "-":
                # line comment (not expected in serpent output, but be safe)
                while self.i < n and s[self.i] != "\n":
                    self.i += 1
            else:
                break

    def parse(self):
        self._ws()
        return self._value()

    def _value(self):
        c = self.s[self.i]
        if c == "{":
            return self._table()
        if c in "\"'":
            return self._string()
        return self._scalar()

    def _string(self):
        quote = self.s[self.i]
        self.i += 1
        out = []
        s = self.s
        while self.i < self.n:
            c = s[self.i]
            if c == "\\":
                nxt = s[self.i + 1]
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                self.i += 2
                continue
            if c == quote:
                self.i += 1
                break
            out.append(c)
            self.i += 1
        return "".join(out)

    def _scalar(self):
        s = self.s
        start = self.i
        while self.i < self.n and s[self.i] not in ",}{=":
            self.i += 1
        tok = s[start:self.i].strip()
        if tok == "true":
            return True
        if tok == "false":
            return False
        if tok == "nil":
            return None
        try:
            f = float(tok)
            return int(f) if f.is_integer() and "." not in tok and "e" not in tok.lower() else f
        except ValueError:
            return tok  # bareword treated as string

    def _key(self):
        """Parse a table key at current position; return (key or None, is_keyed)."""
        self._ws()
        c = self.s[self.i]
        if c == "[":
            self.i += 1  # consume '['
            self._ws()
            key = self._string() if self.s[self.i] in "\"'" else self._scalar()
            self._ws()
            assert self.s[self.i] == "]", "expected ] in key"
            self.i += 1
            self._ws()
            assert self.s[self.i] == "=", "expected = after [key]"
            self.i += 1
            return key, True
        # bareword key = ...   (lookahead for '=' that is not '==')
        j = self.i
        while j < self.n and (self.s[j].isalnum() or self.s[j] in "_-."):
            j += 1
        k = j
        while k < self.n and self.s[k] in " \t\r\n":
            k += 1
        if k < self.n and self.s[k] == "=" and (k + 1 >= self.n or self.s[k + 1] != "="):
            key = self.s[self.i:j]
            self.i = k + 1
            return key, True
        return None, False

    def _table(self):
        self.i += 1  # consume '{'
        arr: list = []
        obj: dict = {}
        while True:
            self._ws()
            if self.s[self.i] == "}":
                self.i += 1
                break
            key, is_keyed = self._key()
            self._ws()
            val = self._value()
            if is_keyed:
                obj[key] = val
            else:
                arr.append(val)
        if obj and not arr:
            return obj
        if arr and not obj:
            return arr
        if not arr and not obj:
            return []
        obj["_array"] = arr  # mixed (unused by recipes)
        return obj


def _extract_recipe_block(text: str) -> str:
    body = text[text.index("{"):]
    key = "\n  recipe = {"
    idx = body.index(key)
    start = idx + len(key) - 1  # at the '{'
    depth = 0
    for j in range(start, len(body)):
        if body[j] == "{":
            depth += 1
        elif body[j] == "}":
            depth -= 1
            if depth == 0:
                return body[start:j + 1]
    raise ValueError("unterminated recipe block")


def _norm_items(items) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append({
            "name": it.get("name"),
            "amount": float(it.get("amount", 1)),
            "type": it.get("type", "item"),
        })
    return out


def _primary_result(results: list[dict], recipe_name: str):
    if not results:
        return None, "no results"
    items = [r for r in results if r.get("type") == "item"]
    if not items:
        return None, "no item-type result (fluid-only output)"
    if len(items) == 1:
        return items[0], None
    match = [r for r in items if r["name"] == recipe_name]
    if match:
        return match[0], None
    return None, f"multiple item results, no primary matching '{recipe_name}'"


def convert(text: str) -> dict:
    block = _extract_recipe_block(text)
    recipes = _Lua(block).parse()
    db: dict = {}
    skipped: dict = {}
    for name, r in recipes.items():
        if not isinstance(r, dict):
            continue
        if r.get("category") == "recycling":
            continue  # recycling is derived, not stored
        ingredients = _norm_items(r.get("ingredients"))
        if not ingredients:
            skipped[name] = "no ingredients (raw resource / mining)"
            continue
        # Fluid ingredients are kept but flagged: recycling never returns them,
        # so they are external (piped) inputs, not part of the recycle loop. A
        # recipe needs at least one item (solid) ingredient to form a loop at all.
        if not any(i["type"] == "item" for i in ingredients):
            skipped[name] = "no recyclable (item) ingredients to loop"
            continue
        results = _norm_items(r.get("results"))
        primary, reason = _primary_result(results, name)
        if primary is None:
            skipped[name] = reason
            continue
        db[name] = {
            "name": name,
            "category": r.get("category", "crafting"),
            "energy_required": float(r.get("energy_required", DEFAULT_ENERGY)),
            "ingredients": ingredients,
            "results": results,
            "output_item": primary["name"],
            "output_yield": primary["amount"],
        }
    db["_skipped"] = skipped
    return db


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("usage: convert_dump.py <dump.txt> [out.json]")
    src = argv[0]
    out = argv[1] if len(argv) > 1 else "data/recipes.json"
    with open(src, encoding="utf-8", errors="replace") as f:
        text = f.read()
    db = convert(text)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=1, sort_keys=True)
    n = len([k for k in db if k != "_skipped"])
    print(f"wrote {n} recipes to {out} ({len(db['_skipped'])} skipped)")


if __name__ == "__main__":
    main(sys.argv[1:])
