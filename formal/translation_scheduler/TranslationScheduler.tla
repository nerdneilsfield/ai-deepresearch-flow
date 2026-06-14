---- MODULE TranslationScheduler ----
EXTENDS Naturals, FiniteSets, TLC

(***************************************************************************)
(* Black-box translation scheduler model.  It checks external scheduling   *)
(* guarantees: no provider over-capacity, at most one active lease per node,*)
(* and disjoint terminal states. The Python checker is the gated proof.     *)
(***************************************************************************)

CONSTANTS Nodes, GlobalLimit

VARIABLES pending, retryReady, fallbackReady, inflight, done, failed

Stages == {"main", "retry", "fallback"}
LeaseSpace == Nodes \X Stages
LeaseNode(l) == l[1]
LeaseStage(l) == l[2]
InflightNodes == {LeaseNode(l): l \in inflight}

Init ==
  /\ pending = Nodes
  /\ retryReady = {}
  /\ fallbackReady = {}
  /\ inflight = {}
  /\ done = {}
  /\ failed = {}

Start(n, stage) ==
  /\ n \in Nodes
  /\ stage \in Stages
  /\ n \notin InflightNodes \cup done \cup failed
  /\ Cardinality(inflight) < GlobalLimit
  /\ IF stage = "main" THEN n \in pending ELSE IF stage = "retry" THEN n \in retryReady ELSE n \in fallbackReady
  /\ inflight' = inflight \cup {<<n, stage>>}
  /\ pending' = pending \ {n}
  /\ retryReady' = retryReady \ {n}
  /\ fallbackReady' = fallbackReady \ {n}
  /\ UNCHANGED <<done, failed>>

Complete(l) ==
  /\ l \in inflight
  /\ inflight' = inflight \ {l}
  /\ done' = done \cup {LeaseNode(l)}
  /\ UNCHANGED <<pending, retryReady, fallbackReady, failed>>

FailAttempt(l) ==
  /\ l \in inflight
  /\ inflight' = inflight \ {l}
  /\ IF LeaseStage(l) = "main" THEN
        /\ retryReady' = retryReady \cup {LeaseNode(l)}
        /\ fallbackReady' = fallbackReady
        /\ failed' = failed
     ELSE IF LeaseStage(l) = "retry" THEN
        /\ retryReady' = retryReady
        /\ fallbackReady' = fallbackReady \cup {LeaseNode(l)}
        /\ failed' = failed
     ELSE
        /\ retryReady' = retryReady
        /\ fallbackReady' = fallbackReady
        /\ failed' = failed \cup {LeaseNode(l)}
  /\ UNCHANGED <<pending, done>>

Next ==
  (\E n \in Nodes, stage \in Stages: Start(n, stage)) \/
  (\E l \in LeaseSpace: Complete(l) \/ FailAttempt(l))

CapacitySafe == Cardinality(inflight) <= GlobalLimit
ExclusiveLease == Cardinality(InflightNodes) = Cardinality(inflight)
TerminalDisjoint == done \cap failed = {}
NoActiveTerminal == InflightNodes \cap (done \cup failed) = {}
StateSetsDisjoint ==
  /\ pending \cap (retryReady \cup fallbackReady \cup done \cup failed) = {}
  /\ retryReady \cap (fallbackReady \cup done \cup failed) = {}
  /\ fallbackReady \cap (done \cup failed) = {}

Inv == CapacitySafe /\ ExclusiveLease /\ TerminalDisjoint /\ NoActiveTerminal /\ StateSetsDisjoint

====
