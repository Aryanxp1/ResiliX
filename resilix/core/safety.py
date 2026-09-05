"""ResiliX central safety / scope engine.

Every test must pass through this layer before and during execution.
It provides:

* Target allowlisting  - only explicitly authorized hosts/domains are testable.
* Hard limits         - duration, concurrency, rate, total operations, payload
                         size and connection counts are bounded centrally.
* Emergency stop      - a global stop signal that all workers observe.

Engines and controllers consult this module; they cannot bypass it. The
platform is intended for authorized, isolated lab environments only.
"""
from __future__ import annotations

import ipaddress
import threading
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set

from .models import SafetyConfig, Target, TestConfig


class SafetyViolation(Exception):
    """Raised when a test configuration exceeds the central safety limits."""


class UnauthorizedTarget(Exception):
    """Raised when a target is not present in the authorized allowlist."""


class EmergencyStop:
    """A process-wide, thread-safe emergency stop flag.

    All workers poll :meth:`is_set` between operations and terminate
    promptly when it becomes set. Ctrl+C on the CLI triggers this.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def trigger(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    def is_set(self) -> bool:
        return self._event.is_set()

    @property
    def event(self) -> threading.Event:
        return self._event

    def __deepcopy__(self, memo):
        """A process-wide stop flag is shared, never duplicated.

        ``copy.deepcopy`` of a :class:`TestConfig` (e.g. during safety
        clamping) must not copy the underlying threading lock; the same
        signal must remain visible to every engine.
        """
        return self


#: Loopback addresses always considered authorized for local development.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _default_loopback_networks() -> List[ipaddress._BaseNetwork]:
    return [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("::1/128"),
    ]


@dataclass
class AuthorizedTargets:
    """The set of targets this deployment is permitted to test."""
    hosts: Set[str] = field(default_factory=set)          # exact hostnames
    ips: Set[str] = field(default_factory=set)            # exact IP addresses
    networks: List[ipaddress._BaseNetwork] = field(default_factory=list)
    allow_private_lab: bool = False

    @classmethod
    def from_lists(cls, hosts: Optional[Iterable[str]] = None,
                   networks: Optional[Iterable[str]] = None,
                   ips: Optional[Iterable[str]] = None,
                   allow_private_lab: bool = False) -> "AuthorizedTargets":
        auth = cls(
            hosts=set(h.lower() for h in (hosts or []) if h),
            ips=set(ips or []),
            allow_private_lab=allow_private_lab,
        )
        for net in networks or []:
            try:
                auth.networks.append(ipaddress.ip_network(net, strict=False))
            except ValueError:
                # Not a network string; treat as a host.
                if ":" in net or _looks_like_ip(net):
                    auth.ips.add(net)
                else:
                    auth.hosts.add(net.lower())
        # Always include loopback so local testing works out of the box.
        auth.hosts |= _LOOPBACK_HOSTS
        auth.networks.extend(_default_loopback_networks())
        if allow_private_lab:
            auth.networks.extend(_private_lab_networks())
        return auth

    def is_authorized(self, host: str) -> bool:
        host = (host or "").strip().lower()
        if not host:
            return False
        if host in self.hosts or host in self.ips:
            return True
        # Resolving hostnames to IPs is intentionally NOT performed here to
        # avoid accidental authorization of a remote host; exact matches only.
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            # A hostname that is not explicitly allowlisted is not authorized.
            return False
        return any(addr in net for net in self.networks)


def _looks_like_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def _private_lab_networks() -> List[ipaddress._BaseNetwork]:
    return [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("169.254.0.0/16"),   # link-local
        ipaddress.ip_network("100.64.0.0/10"),    # CGNAT
        ipaddress.ip_network("fc00::/7"),         # ULA
        ipaddress.ip_network("fe80::/10"),        # link-local v6
    ]


class SafetyManager:
    """Central gatekeeper that validates targets and enforces hard limits."""

    def __init__(self, authorized: Optional[AuthorizedTargets] = None,
                 emergency_stop: Optional[EmergencyStop] = None) -> None:
        # Defaults to loopback-authorized targets so local testing works
        # out of the box; explicit allowlists are always honored.
        self.authorized = authorized or AuthorizedTargets.from_lists()
        self.emergency_stop = emergency_stop or EmergencyStop()

    # ------------------------------------------------------------------
    # Target authorization
    # ------------------------------------------------------------------
    def assert_authorized(self, target: Target) -> None:
        if not self.authorized.is_authorized(target.host):
            raise UnauthorizedTarget(
                f"Target host '{target.host}' is not in the authorized "
                f"allowlist. Add it to config/allowed_targets.yaml before "
                f"testing. Refusing to proceed."
            )

    def is_authorized(self, host: str) -> bool:
        return self.authorized.is_authorized(host)

    # ------------------------------------------------------------------
    # Hard-limit enforcement
    # ------------------------------------------------------------------
    def enforce_limits(self, config: TestConfig) -> None:
        """Validate a test config against the central safety limits.

        Raises SafetyViolation if any requested limit exceeds the safety
        maximum. Returns normally otherwise (the config is then clamped in
        :meth:`clamp_to_safety`).
        """
        s = config.safety
        if config.scenario.max_rate > s.max_rate_per_sec + 1e-9:
            raise SafetyViolation(
                f"Scenario max rate {config.scenario.max_rate}/s exceeds the "
                f"central safety limit of {s.max_rate_per_sec}/s."
            )
        if config.scenario.concurrency > s.max_concurrency:
            raise SafetyViolation(
                f"Scenario concurrency {config.scenario.concurrency} exceeds "
                f"the central safety limit of {s.max_concurrency}."
            )
        if config.scenario.total_duration > s.max_duration_sec + 1e-9:
            raise SafetyViolation(
                f"Scenario total duration {config.scenario.total_duration:.1f}s "
                f"exceeds the central safety limit of {s.max_duration_sec}s."
            )
        for p in config.scenario.phases:
            rate = p.effective_rate(config.scenario.max_rate)
            if rate > s.max_rate_per_sec + 1e-9:
                raise SafetyViolation(
                    f"Phase '{p.name}' rate {rate}/s exceeds the central "
                    f"safety limit of {s.max_rate_per_sec}/s."
                )
            conc = p.effective_concurrency(config.scenario.concurrency)
            if conc > s.max_concurrency:
                raise SafetyViolation(
                    f"Phase '{p.name}' concurrency {conc} exceeds the central "
                    f"safety limit of {s.max_concurrency}."
                )

    def clamp_to_safety(self, config: TestConfig) -> TestConfig:
        """Return a copy of the config whose values respect the hard limits."""
        import copy
        clamped = copy.deepcopy(config)
        clamped.scenario.clamp_to_safety(clamped.safety)
        return clamped

    def validate_all(self, config: TestConfig) -> None:
        """Run full pre-flight validation: target + limits."""
        self.assert_authorized(config.target)
        self.enforce_limits(config)


def default_safety_config() -> SafetyConfig:
    """A conservative default safety configuration for lab use."""
    return SafetyConfig(
        max_duration_sec=120.0,
        max_concurrency=40,
        max_rate_per_sec=300.0,
        max_total_operations=200_000,
        max_payload_bytes=64 * 1024,
        max_connections=100,
        allow_emergency_stop=True,
        require_authorized_target=True,
    )
