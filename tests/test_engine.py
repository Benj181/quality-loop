"""Validation tests: reproduce the blog's worked example and Konage's table."""
import numpy as np
import pytest

from quality_loop.engine import (
    MACHINES, ModuleStrategy, SystemOutput,
    production_matrix, transition_matrix, solve_loop, efficiency,
)
import quality_loop.engine as engine


def test_blog_worked_example():
    """P=50%, Q=25%, keep legendary items -> 2.497% efficiency."""
    A = production_matrix([(25, 1.5)] * 5)
    R = production_matrix([(25, 0.25)] * 4 + [(0, 0)])
    iv = np.zeros(10); iv[0] = 1
    t = solve_loop(transition_matrix(R, A), iv)
    expected = [1.26733, 0.20327, 0.08342, 0.02966, 0.00632,
                1.42574, 0.65641, 0.20523, 0.07266, 0.02497]
    assert np.allclose(t, expected, atol=1e-4)


@pytest.fixture(autouse=True)
def _blog_constants(monkeypatch):
    # Konage/blog used truncated in-game values: Q3=6.2%, Prod3=25% at legendary.
    monkeypatch.setitem(engine.QUAL_BASE_BY_LEVEL, 3, 6.2 / 2.5)
    monkeypatch.setitem(engine.PROD_BASE_BY_LEVEL, 3, 25.0 / 2.5)


@pytest.mark.parametrize("machine,target", [
    ("assembling_machine", 1.251900),
    ("foundry", 4.124319),
    ("em_plant", 7.556346),
    ("cryogenic_plant", 14.505597),
])
def test_konage_optimize_items(machine, target):
    e, _ = efficiency(MACHINES[machine], SystemOutput.ITEMS, ModuleStrategy.OPTIMIZE)
    assert e == pytest.approx(target, abs=1e-5)


def test_lossless_ingredients_at_prod_cap():
    """At +300% productivity the loop is lossless for ingredients (100%)."""
    e, _ = efficiency(
        MACHINES["em_plant"], SystemOutput.INGREDIENTS, ModuleStrategy.OPTIMIZE,
        extra_productivity=250.0,  # 50 base + 250 = 300 cap
    )
    assert e == pytest.approx(100.0, abs=1e-3)
