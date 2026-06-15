#!/usr/bin/env python3
"""SMT-backed finite-universe state machine checking helpers.

The helper intentionally checks only observable model states and transitions. It
is not a production-code proof. For every model, it does two machine-checkable
things over a declared finite universe:

1. Compute the reachable fixed point from the model's initial states by asking
   Z3 for every possible next state of every transition.
2. Check that a recovery/degrade transition maps every state in the finite
   universe to an invariant-satisfying fail-closed state.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import product
from typing import Any

import z3

State = tuple[int, ...]
StateMap = dict[str, z3.ArithRef]
Relation = Callable[[StateMap, StateMap, bool], z3.BoolRef]
Invariant = Callable[[StateMap], z3.BoolRef]


@dataclass(frozen=True)
class Var:
    name: str
    values: tuple[int, ...]


@dataclass(frozen=True)
class Action:
    name: str
    relation: Relation
    fault: bool = False


@dataclass(frozen=True)
class ModelSpec:
    name: str
    variables: tuple[Var, ...]
    initial_states: tuple[State, ...]
    actions: tuple[Action, ...]
    invariant: Invariant
    recover: Relation
    unmodeled_faults: tuple[str, ...] = ()


def bool_domain() -> tuple[int, int]:
    return (0, 1)


def domain_constraints(variables: Sequence[Var], state: StateMap) -> list[z3.BoolRef]:
    return [z3.Or(*(state[var.name] == value for value in var.values)) for var in variables]


def state_vars(variables: Sequence[Var], prefix: str) -> StateMap:
    return {var.name: z3.Int(f"{prefix}_{var.name}") for var in variables}


def fix_state(variables: Sequence[Var], state_vars_: StateMap, state: State) -> list[z3.BoolRef]:
    return [state_vars_[var.name] == value for var, value in zip(variables, state, strict=True)]


def eval_state(variables: Sequence[Var], model: z3.ModelRef, state_vars_: StateMap) -> State:
    return tuple(
        model.eval(state_vars_[var.name], model_completion=True).as_long() for var in variables
    )


def block_state(variables: Sequence[Var], state_vars_: StateMap, state: State) -> z3.BoolRef:
    return z3.Or(
        *(state_vars_[var.name] != value for var, value in zip(variables, state, strict=True))
    )


def all_states(spec: ModelSpec) -> list[State]:
    return list(product(*(var.values for var in spec.variables)))


def same_except(current: StateMap, next_: StateMap, *changed: str) -> z3.BoolRef:
    changed_set = set(changed)
    return z3.And(
        *(next_[name] == value for name, value in current.items() if name not in changed_set)
    )


def assign_all(next_: StateMap, values: dict[str, int | z3.ArithRef]) -> z3.BoolRef:
    return z3.And(*(next_[name] == value for name, value in values.items()))


def count_equals(state: StateMap, names: Iterable[str], value: int) -> z3.ArithRef:
    return z3.Sum(*(z3.If(state[name] == value, 1, 0) for name in names))


def enumerate_relation_next_states(
    spec: ModelSpec,
    current_state: State,
    relation: Relation,
    *,
    inject_bug: bool,
) -> list[State]:
    current = state_vars(spec.variables, "cur")
    next_ = state_vars(spec.variables, "next")
    solver = z3.Solver()
    solver.add(*domain_constraints(spec.variables, current))
    solver.add(*domain_constraints(spec.variables, next_))
    solver.add(*fix_state(spec.variables, current, current_state))
    solver.add(relation(current, next_, inject_bug))

    states: list[State] = []
    while solver.check() == z3.sat:
        model = solver.model()
        state = eval_state(spec.variables, model, next_)
        states.append(state)
        solver.add(block_state(spec.variables, next_, state))
    return states


def invariant_violation(spec: ModelSpec, state: State) -> dict[str, int] | None:
    current = state_vars(spec.variables, "inv")
    solver = z3.Solver()
    solver.add(*domain_constraints(spec.variables, current))
    solver.add(*fix_state(spec.variables, current, state))
    solver.add(z3.Not(spec.invariant(current)))
    if solver.check() != z3.sat:
        return None
    return {var.name: value for var, value in zip(spec.variables, state, strict=True)}


def recovery_violation(spec: ModelSpec, state: State) -> dict[str, Any] | None:
    current = state_vars(spec.variables, "rec_cur")
    next_ = state_vars(spec.variables, "rec_next")
    solver = z3.Solver()
    solver.add(*domain_constraints(spec.variables, current))
    solver.add(*domain_constraints(spec.variables, next_))
    solver.add(*fix_state(spec.variables, current, state))
    solver.add(spec.recover(current, next_, False))
    solver.add(z3.Not(spec.invariant(next_)))
    if solver.check() == z3.sat:
        model = solver.model()
        next_state = eval_state(spec.variables, model, next_)
        return {
            "from": {var.name: value for var, value in zip(spec.variables, state, strict=True)},
            "to": {var.name: value for var, value in zip(spec.variables, next_state, strict=True)},
        }

    total = z3.Solver()
    total.add(*domain_constraints(spec.variables, current))
    total.add(*domain_constraints(spec.variables, next_))
    total.add(*fix_state(spec.variables, current, state))
    total.add(spec.recover(current, next_, False))
    if total.check() != z3.sat:
        return {
            "from": {var.name: value for var, value in zip(spec.variables, state, strict=True)},
            "to": None,
            "reason": "recovery relation is not total for this universe state",
        }
    return None


def check_model(spec: ModelSpec, *, inject_bug: bool = False) -> dict[str, Any]:
    universe = all_states(spec)
    reachable: set[State] = set(spec.initial_states)
    queue: deque[State] = deque(spec.initial_states)
    transitions_checked = 0
    counterexample: dict[str, Any] | None = None

    while queue and counterexample is None:
        state = queue.popleft()
        violation = invariant_violation(spec, state)
        if violation is not None:
            counterexample = {"kind": "reachable_invariant", "state": violation}
            break
        for action in spec.actions:
            next_states = enumerate_relation_next_states(
                spec, state, action.relation, inject_bug=inject_bug
            )
            transitions_checked += len(next_states)
            for next_state in next_states:
                violation = invariant_violation(spec, next_state)
                if violation is not None:
                    counterexample = {
                        "kind": "transition_invariant",
                        "action": action.name,
                        "from": {
                            var.name: value
                            for var, value in zip(spec.variables, state, strict=True)
                        },
                        "to": violation,
                    }
                    break
                if next_state not in reachable:
                    reachable.add(next_state)
                    queue.append(next_state)
            if counterexample is not None:
                break

    recovery_counterexample = None
    for state in universe:
        recovery_counterexample = recovery_violation(spec, state)
        if recovery_counterexample is not None:
            break

    status = "pass" if counterexample is None and recovery_counterexample is None else "fail"
    result: dict[str, Any] = {
        "model": spec.name,
        "solver": "z3",
        "status": status,
        "state_space": {
            "universe_states": len(universe),
            "reachable_states": len(reachable),
            "all_states_checked": len(universe),
            "recovery_states_checked": len(universe)
            if recovery_counterexample is None
            else universe.index(state) + 1,
            "transitions_checked": transitions_checked,
            "actions": len(spec.actions),
            "fault_actions": sum(1 for action in spec.actions if action.fault),
            "closure": "fixed_point" if not queue else "stopped_on_counterexample",
        },
        "queries": {
            "reachable_invariant_violation": "unsat" if counterexample is None else "sat",
            "universe_recovery_violation": "unsat" if recovery_counterexample is None else "sat",
        },
        "unmodeled_faults": list(spec.unmodeled_faults),
    }
    if counterexample is not None:
        result["counterexample"] = counterexample
    elif recovery_counterexample is not None:
        result["counterexample"] = {
            "kind": "universe_recovery",
            **recovery_counterexample,
        }
    return result
