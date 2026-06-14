#!/usr/bin/env python3
"""Bounded finite-state checker for the translation scheduler.

The model checks black-box scheduler guarantees: capacity limits, exclusive node
leases, and legal retry/fallback ordering.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Iterable, NamedTuple

MODEL_NAME = "translation_scheduler"
MAX_DEPTH = 7
NODES = (0, 1)
GLOBAL_LIMIT = 1
STAGES = ("main", "retry", "fallback")


class Lease(NamedTuple):
    node: int
    stage: str


@dataclass(frozen=True)
class State:
    pending: frozenset[int]
    retry_ready: frozenset[int]
    fallback_ready: frozenset[int]
    inflight: tuple[Lease, ...]
    done: frozenset[int]
    failed: frozenset[int]
    last_event: str = "Init"


def initial_state() -> State:
    return State(
        pending=frozenset(NODES),
        retry_ready=frozenset(),
        fallback_ready=frozenset(),
        inflight=(),
        done=frozenset(),
        failed=frozenset(),
    )


def describe_state(state: State) -> str:
    inflight = ",".join(f"{lease.node}:{lease.stage}" for lease in state.inflight) or "-"
    pending = ",".join(map(str, sorted(state.pending))) or "-"
    retry = ",".join(map(str, sorted(state.retry_ready))) or "-"
    fallback = ",".join(map(str, sorted(state.fallback_ready))) or "-"
    done = ",".join(map(str, sorted(state.done))) or "-"
    failed = ",".join(map(str, sorted(state.failed))) or "-"
    return (
        f"event={state.last_event} pending=[{pending}] retry=[{retry}] fallback=[{fallback}] "
        f"inflight=[{inflight}] done=[{done}] failed=[{failed}]"
    )


def lease_nodes(state: State) -> set[int]:
    return {lease.node for lease in state.inflight}


def can_start(state: State, node: int, stage: str, *, inject_bug: bool) -> bool:
    if node in lease_nodes(state) or node in state.done or node in state.failed:
        return False
    if not inject_bug and len(state.inflight) >= GLOBAL_LIMIT:
        return False
    if stage == "main":
        return node in state.pending
    if stage == "retry":
        return node in state.retry_ready
    if stage == "fallback":
        return node in state.fallback_ready
    return False


def start(state: State, node: int, stage: str, *, inject_bug: bool) -> State | None:
    if not can_start(state, node, stage, inject_bug=inject_bug):
        return None
    pending = set(state.pending)
    retry_ready = set(state.retry_ready)
    fallback_ready = set(state.fallback_ready)
    pending.discard(node)
    retry_ready.discard(node)
    fallback_ready.discard(node)
    lease = Lease(node=node, stage=stage)
    return State(
        pending=frozenset(pending),
        retry_ready=frozenset(retry_ready),
        fallback_ready=frozenset(fallback_ready),
        inflight=tuple(sorted((*state.inflight, lease))),
        done=state.done,
        failed=state.failed,
        last_event=f"Start({node},{stage})",
    )


def complete(state: State, lease: Lease) -> State | None:
    if lease not in state.inflight:
        return None
    inflight = tuple(item for item in state.inflight if item != lease)
    return State(
        pending=state.pending,
        retry_ready=state.retry_ready,
        fallback_ready=state.fallback_ready,
        inflight=inflight,
        done=frozenset((*state.done, lease.node)),
        failed=state.failed,
        last_event=f"Complete({lease.node},{lease.stage})",
    )


def fail_attempt(state: State, lease: Lease) -> State | None:
    if lease not in state.inflight:
        return None
    inflight = tuple(item for item in state.inflight if item != lease)
    retry_ready = set(state.retry_ready)
    fallback_ready = set(state.fallback_ready)
    failed = set(state.failed)
    if lease.stage == "main":
        retry_ready.add(lease.node)
        event = f"FailToRetry({lease.node})"
    elif lease.stage == "retry":
        fallback_ready.add(lease.node)
        event = f"FailToFallback({lease.node})"
    else:
        failed.add(lease.node)
        event = f"FailTerminal({lease.node})"
    return State(
        pending=state.pending,
        retry_ready=frozenset(retry_ready),
        fallback_ready=frozenset(fallback_ready),
        inflight=inflight,
        done=state.done,
        failed=frozenset(failed),
        last_event=event,
    )


def successors(state: State, *, inject_bug: bool) -> Iterable[tuple[str, State]]:
    for node in NODES:
        for stage in STAGES:
            next_state = start(state, node, stage, inject_bug=inject_bug)
            if next_state is not None:
                yield next_state.last_event, next_state
    for lease in state.inflight:
        next_state = complete(state, lease)
        if next_state is not None:
            yield next_state.last_event, next_state
        next_state = fail_attempt(state, lease)
        if next_state is not None:
            yield next_state.last_event, next_state


def invariant_violations(state: State) -> list[str]:
    failures: list[str] = []
    if len(state.inflight) > GLOBAL_LIMIT:
        failures.append("global provider concurrency limit exceeded")
    if len(lease_nodes(state)) != len(state.inflight):
        failures.append("a node has more than one active scheduler lease")
    terminal_overlap = state.done & state.failed
    if terminal_overlap:
        failures.append("node is both done and failed")
    active_terminal = lease_nodes(state) & (state.done | state.failed)
    if active_terminal:
        failures.append("terminal node still has an active lease")
    if state.pending & (state.retry_ready | state.fallback_ready | state.done | state.failed):
        failures.append("pending node also appears in a later state")
    if state.retry_ready & (state.fallback_ready | state.done | state.failed):
        failures.append("retry-ready node also appears in a later state")
    if state.fallback_ready & (state.done | state.failed):
        failures.append("fallback-ready node also appears terminal")
    for lease in state.inflight:
        if lease.stage not in STAGES:
            failures.append("unknown stage lease")
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
    start_state = initial_state()
    queue: list[list[tuple[str, State]]] = [[("Init", start_state)]]
    seen: set[State] = {start_state}
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
    print("invariants: global capacity, exclusive leases, terminal separation, legal stage sets")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inject-bug", action="store_true", help="ignore the global concurrency limit"
    )
    args = parser.parse_args(argv)
    return check(inject_bug=args.inject_bug)


if __name__ == "__main__":
    sys.exit(main())
