#!/usr/bin/env python3
"""Run local Z3-backed finite-universe checks for core state machines."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import z3

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import (
        Action,
        ModelSpec,
        Var,
        assign_all,
        bool_domain,
        check_model,
        count_equals,
        same_except,
    )
else:
    from .common import (
        Action,
        ModelSpec,
        Var,
        assign_all,
        bool_domain,
        check_model,
        count_equals,
        same_except,
    )


def _bool_vars(*names: str) -> tuple[Var, ...]:
    return tuple(Var(name, bool_domain()) for name in names)


def oauth_client_cache_model() -> ModelSpec:
    variables = _bool_vars(
        "registered", "durable", "revoked", "expired", "resource_ok", "issued", "corrupt"
    )

    def register_ok(
        cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
    ) -> z3.BoolRef:
        return z3.And(
            assign_all(
                nxt,
                {
                    "registered": 1,
                    "durable": 1,
                    "revoked": 0,
                    "expired": 0,
                    "resource_ok": 1,
                    "issued": 0,
                    "corrupt": 0,
                },
            )
        )

    def partial_register(
        cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
    ) -> z3.BoolRef:
        return assign_all(
            nxt,
            {
                "registered": 1,
                "durable": 0,
                "revoked": 0,
                "expired": 0,
                "resource_ok": cur["resource_ok"],
                "issued": 0,
                "corrupt": 1,
            },
        )

    def issue(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        preconditions = [cur["registered"] == 1, cur["revoked"] == 0, cur["expired"] == 0]
        if not bug:
            preconditions.extend(
                [cur["durable"] == 1, cur["resource_ok"] == 1, cur["corrupt"] == 0]
            )
        return z3.And(
            *preconditions,
            same_except(cur, nxt, "issued"),
            nxt["issued"] == 1,
        )

    def revoke(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return z3.And(
            same_except(cur, nxt, "revoked", "issued"), nxt["revoked"] == 1, nxt["issued"] == 0
        )

    def expire(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return z3.And(
            same_except(cur, nxt, "expired", "issued"), nxt["expired"] == 1, nxt["issued"] == 0
        )

    def resource_mismatch(
        cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
    ) -> z3.BoolRef:
        return z3.And(
            same_except(cur, nxt, "resource_ok", "issued"),
            nxt["resource_ok"] == 0,
            nxt["issued"] == 0,
        )

    def restart(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return assign_all(
            nxt,
            {
                "registered": z3.If(cur["durable"] == 1, 1, 0),
                "durable": z3.If(cur["durable"] == 1, 1, 0),
                "revoked": cur["revoked"],
                "expired": cur["expired"],
                "resource_ok": cur["resource_ok"],
                "issued": 0,
                "corrupt": 0,
            },
        )

    def invariant(st: dict[str, z3.ArithRef]) -> z3.BoolRef:
        return z3.Implies(
            st["issued"] == 1,
            z3.And(
                st["registered"] == 1,
                st["durable"] == 1,
                st["revoked"] == 0,
                st["expired"] == 0,
                st["resource_ok"] == 1,
                st["corrupt"] == 0,
            ),
        )

    def recover(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return assign_all(
            nxt,
            {
                "registered": 0,
                "durable": 0,
                "revoked": cur["revoked"],
                "expired": cur["expired"],
                "resource_ok": cur["resource_ok"],
                "issued": 0,
                "corrupt": 0,
            },
        )

    return ModelSpec(
        name="oauth_client_cache",
        variables=variables,
        initial_states=((0, 0, 0, 0, 1, 0, 0),),
        actions=(
            Action("register_ok", register_ok),
            Action("partial_register_write", partial_register, fault=True),
            Action("issue", issue),
            Action("revoke", revoke, fault=True),
            Action("expire", expire, fault=True),
            Action("resource_mismatch", resource_mismatch, fault=True),
            Action("restart", restart, fault=True),
        ),
        invariant=invariant,
        recover=recover,
    )


def provider_routing_model() -> ModelSpec:
    variables = (
        Var("request", (0, 1, 2)),
        Var("selected", (0, 1, 2)),
        Var("chat_up", bool_domain()),
        Var("embed_up", bool_domain()),
        Var("chat_quota", bool_domain()),
        Var("embed_quota", bool_domain()),
        Var("degraded", bool_domain()),
    )

    def request_chat(
        cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
    ) -> z3.BoolRef:
        return z3.And(
            same_except(cur, nxt, "request", "selected", "degraded"),
            nxt["request"] == 1,
            nxt["selected"] == 0,
            nxt["degraded"] == 0,
        )

    def request_embed(
        cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
    ) -> z3.BoolRef:
        return z3.And(
            same_except(cur, nxt, "request", "selected", "degraded"),
            nxt["request"] == 2,
            nxt["selected"] == 0,
            nxt["degraded"] == 0,
        )

    def route_chat(
        cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
    ) -> z3.BoolRef:
        pre = z3.And(cur["request"] == 1, cur["chat_up"] == 1, cur["chat_quota"] == 1)
        return z3.And(
            pre,
            same_except(cur, nxt, "selected", "chat_quota", "degraded"),
            nxt["selected"] == 1,
            nxt["chat_quota"] == 0,
            nxt["degraded"] == 0,
        )

    def route_embed(
        cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
    ) -> z3.BoolRef:
        pre = z3.And(cur["request"] == 2, cur["embed_up"] == 1, cur["embed_quota"] == 1)
        selected = 1 if bug else 2
        return z3.And(
            pre,
            same_except(cur, nxt, "selected", "embed_quota", "degraded"),
            nxt["selected"] == selected,
            nxt["embed_quota"] == 0,
            nxt["degraded"] == 0,
        )

    def degrade(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        unavailable = z3.Or(
            z3.And(cur["request"] == 1, z3.Or(cur["chat_up"] == 0, cur["chat_quota"] == 0)),
            z3.And(cur["request"] == 2, z3.Or(cur["embed_up"] == 0, cur["embed_quota"] == 0)),
        )
        return z3.And(
            unavailable,
            same_except(cur, nxt, "selected", "degraded"),
            nxt["selected"] == 0,
            nxt["degraded"] == 1,
        )

    def outage(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return z3.And(
            same_except(cur, nxt, "chat_up", "selected"), nxt["chat_up"] == 0, nxt["selected"] == 0
        )

    def reset(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return assign_all(
            nxt,
            {
                "request": 0,
                "selected": 0,
                "chat_up": 1,
                "embed_up": 1,
                "chat_quota": 1,
                "embed_quota": 1,
                "degraded": 0,
            },
        )

    def invariant(st: dict[str, z3.ArithRef]) -> z3.BoolRef:
        return z3.And(
            z3.Implies(
                st["selected"] == 1,
                z3.And(
                    st["request"] == 1,
                    st["chat_up"] == 1,
                    st["chat_quota"] == 0,
                    st["degraded"] == 0,
                ),
            ),
            z3.Implies(
                st["selected"] == 2,
                z3.And(
                    st["request"] == 2,
                    st["embed_up"] == 1,
                    st["embed_quota"] == 0,
                    st["degraded"] == 0,
                ),
            ),
            z3.Implies(st["degraded"] == 1, st["selected"] == 0),
        )

    def recover(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return assign_all(
            nxt,
            {
                "request": 0,
                "selected": 0,
                "chat_up": cur["chat_up"],
                "embed_up": cur["embed_up"],
                "chat_quota": cur["chat_quota"],
                "embed_quota": cur["embed_quota"],
                "degraded": 1,
            },
        )

    return ModelSpec(
        name="provider_routing",
        variables=variables,
        initial_states=((0, 0, 1, 1, 1, 1, 0),),
        actions=(
            Action("request_chat", request_chat),
            Action("request_embed", request_embed),
            Action("route_chat", route_chat),
            Action("route_embed", route_embed),
            Action("degrade_unavailable", degrade, fault=True),
            Action("provider_outage", outage, fault=True),
            Action("reset", reset, fault=True),
        ),
        invariant=invariant,
        recover=recover,
    )


def semantic_ingest_model() -> ModelSpec:
    variables = _bool_vars(
        "discovered", "parsed", "chunked", "embedded", "indexed", "committed", "failed", "visible"
    )

    def set_stage(name: str, required: str | None = None):
        def relation(
            cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
        ) -> z3.BoolRef:
            pre = cur[required] == 1 if required else z3.BoolVal(True)
            return z3.And(pre, cur["failed"] == 0, same_except(cur, nxt, name), nxt[name] == 1)

        return relation

    def index(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        required = "chunked" if bug else "embedded"
        return z3.And(
            cur[required] == 1,
            cur["failed"] == 0,
            same_except(cur, nxt, "indexed"),
            nxt["indexed"] == 1,
        )

    def commit(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return z3.And(
            cur["indexed"] == 1,
            cur["failed"] == 0,
            same_except(cur, nxt, "committed", "visible"),
            nxt["committed"] == 1,
            nxt["visible"] == 1,
        )

    def fail(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return z3.And(
            same_except(cur, nxt, "failed", "committed", "visible"),
            nxt["failed"] == 1,
            nxt["committed"] == 0,
            nxt["visible"] == 0,
        )

    def invariant(st: dict[str, z3.ArithRef]) -> z3.BoolRef:
        return z3.And(
            z3.Implies(st["parsed"] == 1, st["discovered"] == 1),
            z3.Implies(st["chunked"] == 1, st["parsed"] == 1),
            z3.Implies(st["embedded"] == 1, st["chunked"] == 1),
            z3.Implies(st["indexed"] == 1, st["embedded"] == 1),
            z3.Implies(st["committed"] == 1, z3.And(st["indexed"] == 1, st["failed"] == 0)),
            z3.Implies(st["visible"] == 1, z3.And(st["committed"] == 1, st["failed"] == 0)),
            z3.Implies(st["failed"] == 1, z3.And(st["committed"] == 0, st["visible"] == 0)),
        )

    def recover(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return assign_all(
            nxt,
            {
                "discovered": 0,
                "parsed": 0,
                "chunked": 0,
                "embedded": 0,
                "indexed": 0,
                "committed": 0,
                "failed": 1,
                "visible": 0,
            },
        )

    return ModelSpec(
        name="semantic_ingest",
        variables=variables,
        initial_states=((0, 0, 0, 0, 0, 0, 0, 0),),
        actions=(
            Action("discover", set_stage("discovered")),
            Action("parse", set_stage("parsed", "discovered")),
            Action("chunk", set_stage("chunked", "parsed")),
            Action("embed", set_stage("embedded", "chunked")),
            Action("index", index),
            Action("commit", commit),
            Action("fail", fail, fault=True),
        ),
        invariant=invariant,
        recover=recover,
    )


def translation_scheduler_model() -> ModelSpec:
    variables = (
        Var("node0", (0, 1, 2, 3, 4, 5)),
        Var("node1", (0, 1, 2, 3, 4, 5)),
        Var("fallback_enabled", bool_domain()),
        Var("cancelled", bool_domain()),
    )

    def start_node(name: str):
        def relation(
            cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
        ) -> z3.BoolRef:
            no_inflight = count_equals(cur, ("node0", "node1"), 1) == 0
            pre = z3.And(
                z3.Or(cur[name] == 0, cur[name] == 2, cur[name] == 3), cur["cancelled"] == 0
            )
            if not bug:
                pre = z3.And(pre, no_inflight)
            return z3.And(pre, same_except(cur, nxt, name), nxt[name] == 1)

        return relation

    def complete_node(name: str):
        def relation(
            cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
        ) -> z3.BoolRef:
            return z3.And(cur[name] == 1, same_except(cur, nxt, name), nxt[name] == 4)

        return relation

    def fail_node(name: str):
        def relation(
            cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
        ) -> z3.BoolRef:
            return z3.And(cur[name] == 1, same_except(cur, nxt, name), nxt[name] == 2)

        return relation

    def fallback_node(name: str):
        def relation(
            cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
        ) -> z3.BoolRef:
            return z3.And(
                cur[name] == 2,
                cur["fallback_enabled"] == 1,
                same_except(cur, nxt, name),
                nxt[name] == 3,
            )

        return relation

    def terminal_fail(name: str):
        def relation(
            cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
        ) -> z3.BoolRef:
            return z3.And(
                cur[name] == 2,
                cur["fallback_enabled"] == 0,
                same_except(cur, nxt, name),
                nxt[name] == 5,
            )

        return relation

    def enable_fallback(
        cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool
    ) -> z3.BoolRef:
        return z3.And(same_except(cur, nxt, "fallback_enabled"), nxt["fallback_enabled"] == 1)

    def cancel(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return assign_all(
            nxt,
            {
                "node0": z3.If(cur["node0"] == 4, 4, 5),
                "node1": z3.If(cur["node1"] == 4, 4, 5),
                "fallback_enabled": cur["fallback_enabled"],
                "cancelled": 1,
            },
        )

    def invariant(st: dict[str, z3.ArithRef]) -> z3.BoolRef:
        return z3.And(
            count_equals(st, ("node0", "node1"), 1) <= 1,
            z3.Implies(st["cancelled"] == 1, count_equals(st, ("node0", "node1"), 1) == 0),
            z3.Implies(z3.Or(st["node0"] == 3, st["node1"] == 3), st["fallback_enabled"] == 1),
        )

    def recover(cur: dict[str, z3.ArithRef], nxt: dict[str, z3.ArithRef], bug: bool) -> z3.BoolRef:
        return assign_all(
            nxt,
            {
                "node0": z3.If(cur["node0"] == 4, 4, 5),
                "node1": z3.If(cur["node1"] == 4, 4, 5),
                "fallback_enabled": cur["fallback_enabled"],
                "cancelled": 1,
            },
        )

    return ModelSpec(
        name="translation_scheduler",
        variables=variables,
        initial_states=((0, 0, 0, 0),),
        actions=(
            Action("start_node0", start_node("node0")),
            Action("start_node1", start_node("node1")),
            Action("complete_node0", complete_node("node0")),
            Action("complete_node1", complete_node("node1")),
            Action("fail_node0", fail_node("node0"), fault=True),
            Action("fail_node1", fail_node("node1"), fault=True),
            Action("fallback_node0", fallback_node("node0")),
            Action("fallback_node1", fallback_node("node1")),
            Action("terminal_fail_node0", terminal_fail("node0")),
            Action("terminal_fail_node1", terminal_fail("node1")),
            Action("enable_fallback", enable_fallback, fault=True),
            Action("cancel", cancel, fault=True),
        ),
        invariant=invariant,
        recover=recover,
    )


MODELS = (
    oauth_client_cache_model,
    provider_routing_model,
    semantic_ingest_model,
    translation_scheduler_model,
)


def run_all(*, inject_bug: bool) -> dict[str, Any]:
    models = [check_model(factory(), inject_bug=inject_bug) for factory in MODELS]
    status = "pass" if all(model["status"] == "pass" for model in models) else "fail"
    return {
        "toolchain": "local-z3-finite-universe",
        "solver": "z3",
        "status": status,
        "models": models,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inject-bug", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = run_all(inject_bug=args.inject_bug)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for model in payload["models"]:
            print(
                "SMT_MODEL {model} {status} solver=z3 universe={universe_states} "
                "reachable={reachable_states} transitions={transitions_checked} "
                "recovery={universe_recovery_violation}".format(
                    model=model["model"],
                    status=model["status"].upper(),
                    universe_states=model["state_space"]["universe_states"],
                    reachable_states=model["state_space"]["reachable_states"],
                    transitions_checked=model["state_space"]["transitions_checked"],
                    universe_recovery_violation=model["queries"]["universe_recovery_violation"],
                )
            )
            if model["status"] != "pass":
                print(json.dumps(model.get("counterexample"), indent=2, sort_keys=True))
        print(f"SMT_MODEL check_all_smt_models {payload['status'].upper()} solver=z3")
    return 0 if payload["status"] == "pass" and not args.inject_bug else 1


if __name__ == "__main__":
    sys.exit(main())
