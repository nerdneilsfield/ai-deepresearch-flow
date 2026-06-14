---- MODULE ProviderRouting ----
EXTENDS Naturals, Sequences, FiniteSets, TLC

(***************************************************************************)
(* Black-box provider routing model.  A route returned to a caller must     *)
(* satisfy the requested capability, active window, cooldown, and quota.    *)
(* The Python bounded checker is the gated MODEL_PROOF for this draft.      *)
(***************************************************************************)

CONSTANTS Candidates, Capabilities, MaxTime, Cooldown

VARIABLES now, remaining, cooldownUntil, lastRoute

Name(c) == c[1]
Capability(c) == c[2]
ActiveFrom(c) == c[3]
ActiveUntil(c) == c[4]
Quota(c) == c[5]

Names == {Name(c): c \in Candidates}
CandidateByName(n) == CHOOSE c \in Candidates: Name(c) = n
Active(c, t) == ActiveFrom(c) <= t /\ t < ActiveUntil(c)

Init ==
  /\ now = 0
  /\ remaining = [n \in Names |-> Quota(CandidateByName(n))]
  /\ cooldownUntil = [n \in Names |-> 0]
  /\ lastRoute = [requestedCapability |-> "", candidate |-> "", at |-> 0, remainingAfter |-> 0]

Route(cap, c) ==
  /\ cap \in Capabilities
  /\ c \in Candidates
  /\ Capability(c) = cap
  /\ Active(c, now)
  /\ cooldownUntil[Name(c)] <= now
  /\ remaining[Name(c)] > 0
  /\ remaining' = [remaining EXCEPT ![Name(c)] = @ - 1]
  /\ lastRoute' = [requestedCapability |-> cap,
                   candidate |-> Name(c),
                   at |-> now,
                   remainingAfter |-> remaining[Name(c)] - 1]
  /\ UNCHANGED <<now, cooldownUntil>>

Tick ==
  /\ now < MaxTime
  /\ now' = now + 1
  /\ cooldownUntil' = [n \in Names |-> IF cooldownUntil[n] > now' THEN cooldownUntil[n] ELSE 0]
  /\ UNCHANGED <<remaining, lastRoute>>

Fail(n) ==
  /\ n \in Names
  /\ cooldownUntil' = [cooldownUntil EXCEPT ![n] = now + Cooldown]
  /\ UNCHANGED <<now, remaining, lastRoute>>

Next == Tick \/ (\E cap \in Capabilities, c \in Candidates: Route(cap, c)) \/ (\E n \in Names: Fail(n))

LastRouteSafe ==
  lastRoute.candidate = "" \/
    LET c == CandidateByName(lastRoute.candidate) IN
      /\ Capability(c) = lastRoute.requestedCapability
      /\ Active(c, lastRoute.at)
      /\ lastRoute.remainingAfter >= 0

QuotaSafe == \A n \in Names: remaining[n] >= 0 /\ remaining[n] <= Quota(CandidateByName(n))
CooldownSafe == \A n \in Names: cooldownUntil[n] = 0 \/ cooldownUntil[n] > now

Inv == LastRouteSafe /\ QuotaSafe /\ CooldownSafe

====
