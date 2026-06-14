#!/usr/bin/env python3
"""Bounded finite-state checker for semantic ingest.

The model checks black-box pipeline obligations: a searchable record appears only
after parse, chunk, and embedding for the same document fingerprint.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Iterable, NamedTuple

MODEL_NAME = "semantic_ingest"
MAX_DEPTH = 7
DOCS = ("paper-a", "paper-b")
FINGERPRINTS = ("v1", "v2")


class ChunkKey(NamedTuple):
    doc: str
    fingerprint: str
    chunk: int


@dataclass(frozen=True)
class State:
    discovered: frozenset[tuple[str, str]]
    parsed: frozenset[tuple[str, str]]
    chunked: frozenset[ChunkKey]
    embedded: frozenset[ChunkKey]
    indexed: tuple[ChunkKey, ...]
    last_event: str = "Init"


def initial_state() -> State:
    return State(
        discovered=frozenset((doc, "v1") for doc in DOCS),
        parsed=frozenset(),
        chunked=frozenset(),
        embedded=frozenset(),
        indexed=(),
    )


def describe_key(key: ChunkKey) -> str:
    return f"{key.doc}:{key.fingerprint}:c{key.chunk}"


def describe_state(state: State) -> str:
    parsed = ",".join(f"{doc}:{fp}" for doc, fp in sorted(state.parsed)) or "-"
    chunked = ",".join(describe_key(key) for key in sorted(state.chunked)) or "-"
    embedded = ",".join(describe_key(key) for key in sorted(state.embedded)) or "-"
    indexed = ",".join(describe_key(key) for key in state.indexed) or "-"
    discovered = ",".join(f"{doc}:{fp}" for doc, fp in sorted(state.discovered)) or "-"
    return (
        f"event={state.last_event} discovered=[{discovered}] parsed=[{parsed}] "
        f"chunked=[{chunked}] embedded=[{embedded}] indexed=[{indexed}]"
    )


def rediscover(state: State, doc: str) -> State | None:
    current = {item for item in state.discovered if item[0] == doc}
    if (doc, "v1") in current and (doc, "v2") not in current:
        discovered = frozenset((*state.discovered, (doc, "v2")))
        return State(
            discovered,
            state.parsed,
            state.chunked,
            state.embedded,
            state.indexed,
            f"Rediscover({doc},v2)",
        )
    return None


def parse(state: State, doc: str, fingerprint: str) -> State | None:
    key = (doc, fingerprint)
    if key not in state.discovered or key in state.parsed:
        return None
    return State(
        state.discovered,
        frozenset((*state.parsed, key)),
        state.chunked,
        state.embedded,
        state.indexed,
        f"Parse({doc},{fingerprint})",
    )


def chunk(state: State, doc: str, fingerprint: str) -> State | None:
    if (doc, fingerprint) not in state.parsed:
        return None
    new_chunks = tuple(ChunkKey(doc, fingerprint, chunk_id) for chunk_id in (0, 1))
    if all(key in state.chunked for key in new_chunks):
        return None
    return State(
        state.discovered,
        state.parsed,
        frozenset((*state.chunked, *new_chunks)),
        state.embedded,
        state.indexed,
        f"Chunk({doc},{fingerprint})",
    )


def embed(state: State, key: ChunkKey) -> State | None:
    if key not in state.chunked or key in state.embedded:
        return None
    return State(
        state.discovered,
        state.parsed,
        state.chunked,
        frozenset((*state.embedded, key)),
        state.indexed,
        f"Embed({describe_key(key)})",
    )


def index(state: State, key: ChunkKey, *, inject_bug: bool) -> State | None:
    if key in state.indexed:
        return None
    if inject_bug:
        if key not in state.chunked:
            return None
    elif key not in state.embedded:
        return None
    return State(
        state.discovered,
        state.parsed,
        state.chunked,
        state.embedded,
        (*state.indexed, key),
        f"Index({describe_key(key)})",
    )


def successors(state: State, *, inject_bug: bool) -> Iterable[tuple[str, State]]:
    for doc in DOCS:
        next_state = rediscover(state, doc)
        if next_state is not None:
            yield next_state.last_event, next_state
    for doc, fingerprint in sorted(state.discovered):
        next_state = parse(state, doc, fingerprint)
        if next_state is not None:
            yield next_state.last_event, next_state
        next_state = chunk(state, doc, fingerprint)
        if next_state is not None:
            yield next_state.last_event, next_state
    for key in sorted(state.chunked):
        next_state = embed(state, key)
        if next_state is not None:
            yield next_state.last_event, next_state
        next_state = index(state, key, inject_bug=inject_bug)
        if next_state is not None:
            yield next_state.last_event, next_state


def invariant_violations(state: State) -> list[str]:
    failures: list[str] = []
    indexed_set = set(state.indexed)
    if len(indexed_set) != len(state.indexed):
        failures.append("index contains duplicate searchable records")
    for doc, fingerprint in state.parsed:
        if (doc, fingerprint) not in state.discovered:
            failures.append("parsed document was not discovered")
    for key in state.chunked:
        if (key.doc, key.fingerprint) not in state.parsed:
            failures.append("chunk exists before parse")
    for key in state.embedded:
        if key not in state.chunked:
            failures.append("embedding exists before chunking")
    for key in state.indexed:
        if key not in state.embedded:
            failures.append("index contains a chunk without an embedding")
        if key not in state.chunked:
            failures.append("index contains a chunk that was never produced")
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
        "invariants: discovered-before-parse, parse-before-chunk, chunk-before-embed, embed-before-index"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inject-bug", action="store_true", help="allow indexing before embedding")
    args = parser.parse_args(argv)
    return check(inject_bug=args.inject_bug)


if __name__ == "__main__":
    sys.exit(main())
