#!/usr/bin/env python3
"""Bounded finite-state checker for provider routing.

The model is black-box: it checks externally visible route choices against
capability, active-window, cooldown, and quota constraints.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Iterable, NamedTuple

MODEL_NAME = "provider_routing"
MAX_DEPTH = 6
MAX_TIME = 3
COOLDOWN = 2
REQUEST_CAPABILITIES = ("chat", "embedding")


class Candidate(NamedTuple):
    name: str
    capability: str
    active_from: int
    active_until: int
    quota: int
    weight: int


CANDIDATES = (
    Candidate("chat_fast", "chat", 0, 4, 2, 1),
    Candidate("embed_small", "embedding", 0, 4, 2, 1),
    Candidate("chat_night", "chat", 2, 4, 1, 1),
)
CANDIDATE_BY_NAME = {candidate.name: candidate for candidate in CANDIDATES}


class LastRoute(NamedTuple):
    requested_capability: str
    candidate: str
    at: int
    remaining_after: int


@dataclass(frozen=True)
class State:
    now: int
    remaining: tuple[tuple[str, int], ...]
    cooldown_until: tuple[tuple[str, int], ...]
    last_route: LastRoute | None = None


def map_from_tuple(items: tuple[tuple[str, int], ...]) -> dict[str, int]:
    return dict(items)


def freeze(items: dict[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((key, value) for key, value in items.items() if value > 0))


def initial_state() -> State:
    return State(
        now=0,
        remaining=tuple((candidate.name, candidate.quota) for candidate in CANDIDATES),
        cooldown_until=(),
    )


def is_active(candidate: Candidate, now: int) -> bool:
    return candidate.active_from <= now < candidate.active_until


def describe_state(state: State) -> str:
    remaining = ",".join(f"{name}:{value}" for name, value in state.remaining) or "-"
    cooldown = ",".join(f"{name}@{until}" for name, until in state.cooldown_until) or "-"
    last = "-"
    if state.last_route is not None:
        last = (
            f"{state.last_route.requested_capability}=>{state.last_route.candidate}"
            f" rem={state.last_route.remaining_after} at={state.last_route.at}"
        )
    return f"now={state.now} remaining=[{remaining}] cooldown=[{cooldown}] last={last}"


def route(state: State, capability: str, *, inject_bug: bool) -> State | None:
    remaining = map_from_tuple(state.remaining)
    cooldown = map_from_tuple(state.cooldown_until)
    ordered = sorted(CANDIDATES, key=lambda candidate: (-candidate.weight, candidate.name))
    for candidate in ordered:
        if not inject_bug and candidate.capability != capability:
            continue
        if not is_active(candidate, state.now):
            continue
        if cooldown.get(candidate.name, 0) > state.now:
            continue
        available = remaining.get(candidate.name, 0)
        if available <= 0:
            continue
        remaining[candidate.name] = available - 1
        return State(
            now=state.now,
            remaining=freeze(remaining),
            cooldown_until=state.cooldown_until,
            last_route=LastRoute(
                requested_capability=capability,
                candidate=candidate.name,
                at=state.now,
                remaining_after=available - 1,
            ),
        )
    return None


def tick(state: State) -> State | None:
    if state.now >= MAX_TIME:
        return None
    next_now = state.now + 1
    cooldown = {
        name: until
        for name, until in map_from_tuple(state.cooldown_until).items()
        if until > next_now
    }
    return State(
        now=next_now,
        remaining=state.remaining,
        cooldown_until=freeze(cooldown),
        last_route=None,
    )


def mark_failure(state: State, candidate_name: str) -> State | None:
    if candidate_name not in CANDIDATE_BY_NAME:
        return None
    cooldown = map_from_tuple(state.cooldown_until)
    cooldown[candidate_name] = state.now + COOLDOWN
    return State(
        now=state.now,
        remaining=state.remaining,
        cooldown_until=freeze(cooldown),
        last_route=None,
    )


def successors(state: State, *, inject_bug: bool) -> Iterable[tuple[str, State]]:
    for capability in REQUEST_CAPABILITIES:
        next_state = route(state, capability, inject_bug=inject_bug)
        if next_state is not None:
            yield f"Route({capability})", next_state
    next_state = tick(state)
    if next_state is not None:
        yield "Tick", next_state
    for candidate in CANDIDATES:
        next_state = mark_failure(state, candidate.name)
        if next_state is not None:
            yield f"Fail({candidate.name})", next_state


def invariant_violations(state: State) -> list[str]:
    failures: list[str] = []
    remaining = map_from_tuple(state.remaining)
    cooldown = map_from_tuple(state.cooldown_until)
    for name, value in remaining.items():
        candidate = CANDIDATE_BY_NAME[name]
        if value < 0 or value > candidate.quota:
            failures.append("candidate quota moved outside declared bounds")
    for name, until in cooldown.items():
        if until <= state.now:
            failures.append("expired cooldown remains externally visible")
    if state.last_route is not None:
        candidate = CANDIDATE_BY_NAME[state.last_route.candidate]
        if candidate.capability != state.last_route.requested_capability:
            failures.append("route capability differs from requested capability")
        if not is_active(candidate, state.last_route.at):
            failures.append("route selected a provider outside its active window")
        if cooldown.get(candidate.name, 0) > state.last_route.at:
            failures.append("route selected a provider that was cooling down")
        if state.last_route.remaining_after < 0:
            failures.append("route consumed beyond provider quota")
    return sorted(set(failures))


def print_counterexample(path: list[tuple[str, State]], failures: list[str]) -> None:
    print(f"MODEL_PROOF {MODEL_NAME} FAIL")
    print("invariant violations:")
    for failure in failures:
        print(f"- {failure}")
    print("counterexample trace:")
    print(f"  0. Init: {describe_state(path[0][1])}")
    for idx, (action, state) in enumerate(path[1:], start=1):
        print(f"  {idx}. {action}: {describe_state(state)}")


def check(*, inject_bug: bool) -> int:
    start = initial_state()
    queue: list[list[tuple[str, State]]] = [[("Init", start)]]
    seen: set[State] = {start}
    transitions = 0
    while queue:
        path = queue.pop(0)
        state = path[-1][1]
        failures = invariant_violations(state)
        if failures:
            print_counterexample(path, failures)
            return 1
        if len(path) - 1 >= MAX_DEPTH:
            continue
        for action, next_state in successors(state, inject_bug=inject_bug):
            transitions += 1
            if next_state not in seen:
                seen.add(next_state)
                queue.append([*path, (action, next_state)])
    print(
        f"MODEL_PROOF {MODEL_NAME} PASS depth={MAX_DEPTH} "
        f"states={len(seen)} transitions={transitions}"
    )
    print("invariants: requested capability, active route, cooldown exclusion, quota bounds")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inject-bug", action="store_true", help="explore capability-blind routing"
    )
    args = parser.parse_args(argv)
    return check(inject_bug=args.inject_bug)


if __name__ == "__main__":
    sys.exit(main())
