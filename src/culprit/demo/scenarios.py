"""Registry of demo scenarios.

* ``churn`` — the original golden path. The offline :class:`GoldenPathPolicy` knows how to fix it, so
  it doubles as a deterministic integration test of the whole agent loop.
* ``fraud`` — the *secondary*, unseen regression. No offline policy knows its solution; it exists to
  evaluate a real model with ``culprit evaluate --scenario fraud``.
"""

from __future__ import annotations

from pathlib import Path

from culprit.demo import fraud_generator, generator
from culprit.demo.builder import Scenario, build_repository

SCENARIOS: dict[str, Scenario] = {
    generator.SCENARIO.key: generator.SCENARIO,
    fraud_generator.SCENARIO.key: fraud_generator.SCENARIO,
}
SECONDARY_SCENARIO = fraud_generator.SCENARIO.key


def get_scenario(key: str) -> Scenario:
    try:
        return SCENARIOS[key]
    except KeyError:
        raise KeyError(f"unknown scenario '{key}' (available: {', '.join(SCENARIOS)})") from None


def generate_scenario(
    key: str,
    dest: Path | str | None = None,
    force: bool = False,
    quiet: bool = False,
    drop_latest_nightly: bool = False,
) -> dict:
    scenario = get_scenario(key)
    return build_repository(
        scenario,
        dest or scenario.default_dest,
        force=force,
        quiet=quiet,
        drop_latest_nightly=drop_latest_nightly,
    )
