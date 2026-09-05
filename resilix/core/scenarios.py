"""ResiliX scenario registry.

Scenarios describe how load changes over time (baseline -> ramp -> load ->
cooldown -> recovery). This module provides a named catalogue and parsing of
scenario definitions coming from the YAML/JSON configuration files.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import Scenario, ScenarioPhase

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
_REGISTRY: Dict[str, Any] = {
    "ramp-up": lambda **kw: Scenario.ramp_up(**kw),
    "sustained-load": lambda **kw: Scenario.sustained_load(**kw),
    "spike": lambda **kw: Scenario.spike(**kw),
    "recovery": lambda **kw: Scenario.recovery(**kw),
    "baseline": lambda **kw: Scenario.custom(
        name="baseline",
        description="Light, brief load used to measure a stable baseline.",
        phases=[
            ScenarioPhase("baseline", 10, 20.0, 8, "baseline"),
            ScenarioPhase("cooldown", 5, 5.0, 4, "cooldown"),
        ],
        **kw,
    ),
}


def get_scenario(name: str, **kwargs: Any) -> Scenario:
    """Return the scenario registered under *name*.

    Raises KeyError when the scenario does not exist.
    """
    key = name.lower()
    if key not in _REGISTRY:
        raise KeyError(
            f"Unknown scenario '{name}'. Available: {', '.join(list_scenarios())}"
        )
    return _REGISTRY[key](**kwargs)


def list_scenarios() -> List[str]:
    return sorted(_REGISTRY.keys())


def scenario_from_dict(data: Dict[str, Any], name: Optional[str] = None) -> Scenario:
    """Parse a scenario definition from configuration.

    Expected shape::

        scenario:
          name: ramp-up
          description: ...
          start_rate: 10
          max_rate: 200
          concurrency: 20
          phases:
            - {name: baseline, duration_sec: 5, rate_per_sec: 2, kind: baseline}
    """
    sname = data.get("name") or name or "custom"
    phases_data = data.get("phases")
    if phases_data:
        phases = [
            ScenarioPhase(
                name=str(p.get("name", f"phase-{i}")),
                duration_sec=float(p.get("duration_sec", 10)),
                rate_per_sec=_opt_float(p.get("rate_per_sec")),
                concurrency=_opt_int(p.get("concurrency")),
                kind=str(p.get("kind", "load")),
            )
            for i, p in enumerate(phases_data)
        ]
        return Scenario.custom(
            name=sname,
            phases=phases,
            start_rate=float(data.get("start_rate", 10.0)),
            max_rate=float(data.get("max_rate", 200.0)),
            concurrency=int(data.get("concurrency", 20)),
            mix=data.get("mix", {}),
        )
    # Fall back to a built-in scenario with overrides.
    builtin = data.get("name", "ramp-up")
    return get_scenario(
        builtin,
        start_rate=float(data.get("start_rate", 10.0)),
        max_rate=float(data.get("max_rate", 200.0)),
        concurrency=int(data.get("concurrency", 20)),
    )


def _opt_float(value: Any) -> Optional[float]:
    return float(value) if value is not None else None


def _opt_int(value: Any) -> Optional[int]:
    return int(value) if value is not None else None