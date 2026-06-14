#!/usr/bin/env python3
"""Bounded finite-state checker for the OAuth client cache model.

This is intentionally dependency-free and black-box: the model talks only about
observable cache behavior (request, returned client, expiry, revocation), not any
production implementation details.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Iterable, NamedTuple

MODEL_NAME = "oauth_client_cache"
MAX_DEPTH = 6
MAX_TIME = 3
TTL = 2
PRINCIPALS = ("alice", "bob")
SCOPES = ("read", "write")


class Entry(NamedTuple):
    owner: str
    scope: str
    expires_at: int


class LastReturn(NamedTuple):
    owner: str
    scope: str
    requested_owner: str
    requested_scope: str
    expires_at: int


@dataclass(frozen=True)
class State:
    now: int
    revoked: frozenset[str]
    cache: tuple[tuple[tuple[str, str], Entry], ...]
    last_return: LastReturn | None = None


def cache_to_dict(state: State) -> dict[tuple[str, str], Entry]:
    return dict(state.cache)


def freeze_cache(cache: dict[tuple[str, str], Entry]) -> tuple[tuple[tuple[str, str], Entry], ...]:
    return tuple(sorted(cache.items()))


def initial_state() -> State:
    return State(now=0, revoked=frozenset(), cache=())


def describe_state(state: State) -> str:
    entries = ", ".join(
        f"{owner}:{scope}->{entry.owner}:{entry.scope}@{entry.expires_at}"
        for (owner, scope), entry in state.cache
    )
    revoked = ",".join(sorted(state.revoked)) or "-"
    last = "-"
    if state.last_return is not None:
        last = (
            f"{state.last_return.requested_owner}:{state.last_return.requested_scope}"
            f"=>{state.last_return.owner}:{state.last_return.scope}"
            f"@{state.last_return.expires_at}"
        )
    return f"now={state.now} revoked={revoked} cache=[{entries}] last={last}"


def acquire(state: State, owner: str, scope: str, *, inject_bug: bool) -> State | None:
    if owner in state.revoked:
        return None
    cache = cache_to_dict(state)
    key = (owner, "*") if inject_bug else (owner, scope)
    entry = cache.get(key)
    if entry is None or entry.expires_at <= state.now or entry.owner in state.revoked:
        entry = Entry(owner=owner, scope=scope, expires_at=state.now + TTL)
        cache[key] = entry
    return State(
        now=state.now,
        revoked=state.revoked,
        cache=freeze_cache(cache),
        last_return=LastReturn(
            owner=entry.owner,
            scope=entry.scope,
            requested_owner=owner,
            requested_scope=scope,
            expires_at=entry.expires_at,
        ),
    )


def tick(state: State) -> State | None:
    if state.now >= MAX_TIME:
        return None
    next_now = state.now + 1
    cache = {
        key: entry
        for key, entry in cache_to_dict(state).items()
        if entry.expires_at > next_now and entry.owner not in state.revoked
    }
    return State(
        now=next_now,
        revoked=state.revoked,
        cache=freeze_cache(cache),
        last_return=None,
    )


def revoke(state: State, owner: str) -> State | None:
    if owner in state.revoked:
        return None
    revoked = frozenset((*state.revoked, owner))
    cache = {key: entry for key, entry in cache_to_dict(state).items() if entry.owner != owner}
    return State(
        now=state.now,
        revoked=revoked,
        cache=freeze_cache(cache),
        last_return=None,
    )


def successors(state: State, *, inject_bug: bool) -> Iterable[tuple[str, State]]:
    for owner in PRINCIPALS:
        for scope in SCOPES:
            next_state = acquire(state, owner, scope, inject_bug=inject_bug)
            if next_state is not None:
                yield f"Acquire({owner},{scope})", next_state
    next_state = tick(state)
    if next_state is not None:
        yield "Tick", next_state
    for owner in PRINCIPALS:
        next_state = revoke(state, owner)
        if next_state is not None:
            yield f"Revoke({owner})", next_state


def invariant_violations(state: State) -> list[str]:
    failures: list[str] = []
    for (owner, scope), entry in state.cache:
        if owner != entry.owner or (scope != "*" and scope != entry.scope):
            failures.append("cache entry is not partitioned by request owner and scope")
        if entry.expires_at <= state.now:
            failures.append("cache retains an expired client")
        if entry.owner in state.revoked:
            failures.append("cache retains a client for a revoked principal")
    if state.last_return is not None:
        if state.last_return.owner != state.last_return.requested_owner:
            failures.append("returned client owner differs from requested owner")
        if state.last_return.scope != state.last_return.requested_scope:
            failures.append("returned client scope differs from requested scope")
        if state.last_return.expires_at <= state.now:
            failures.append("returned client is expired")
        if state.last_return.owner in state.revoked:
            failures.append("returned client belongs to a revoked principal")
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
    print(
        "invariants: scoped cache partition, no expired clients, no revoked clients, valid returns"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inject-bug", action="store_true", help="explore a scope-collision bug")
    args = parser.parse_args(argv)
    return check(inject_bug=args.inject_bug)


if __name__ == "__main__":
    sys.exit(main())
