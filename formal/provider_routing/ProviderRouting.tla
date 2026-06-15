---- MODULE ProviderRouting ----
EXTENDS Naturals, FiniteSets, TLC

(***************************************************************************)
(* Black-box provider routing model.  A route returned to a caller must     *)
(* satisfy the requested capability, active window, cooldown, and quota.    *)
(* TLC is the local exhaustive reachable-state checker for this finite      *)
(* model.                                                                   *)
(***************************************************************************)

CONSTANTS ChatFast, EmbedSmall, ChatNight, MaxTime, Cooldown

VARIABLES now, remaining, cooldownUntil, lastRoute

Names == {ChatFast, EmbedSmall, ChatNight}
Capabilities == {"chat", "embedding"}

Capability(n) ==
  IF n = ChatFast THEN "chat"
  ELSE IF n = EmbedSmall THEN "embedding"
  ELSE "chat"

ActiveFrom(n) == IF n = ChatNight THEN 2 ELSE 0
ActiveUntil(n) == 4
Quota(n) == IF n = ChatNight THEN 1 ELSE 2
Active(n, t) == ActiveFrom(n) <= t /\ t < ActiveUntil(n)

Init ==
  /\ now = 0
  /\ remaining = [n \in Names |-> Quota(n)]
  /\ cooldownUntil = [n \in Names |-> 0]
  /\ lastRoute = [requestedCapability |-> "", candidate |-> "", at |-> 0, remainingAfter |-> 0]

Route(cap, n) ==
  /\ cap \in Capabilities
  /\ n \in Names
  /\ Capability(n) = cap
  /\ Active(n, now)
  /\ cooldownUntil[n] <= now
  /\ remaining[n] > 0
  /\ remaining' = [remaining EXCEPT ![n] = @ - 1]
  /\ lastRoute' = [requestedCapability |-> cap,
                   candidate |-> n,
                   at |-> now,
                   remainingAfter |-> remaining[n] - 1]
  /\ UNCHANGED <<now, cooldownUntil>>

Tick ==
  /\ now < MaxTime
  /\ now' = now + 1
  /\ cooldownUntil' = [n \in Names |-> IF cooldownUntil[n] > now' THEN cooldownUntil[n] ELSE 0]
  /\ lastRoute' = [requestedCapability |-> "", candidate |-> "", at |-> 0, remainingAfter |-> 0]
  /\ UNCHANGED remaining

Fail(n) ==
  /\ n \in Names
  /\ cooldownUntil' = [cooldownUntil EXCEPT ![n] = now + Cooldown]
  /\ lastRoute' = [requestedCapability |-> "", candidate |-> "", at |-> 0, remainingAfter |-> 0]
  /\ UNCHANGED <<now, remaining>>

Next == Tick \/ (\E cap \in Capabilities, n \in Names: Route(cap, n)) \/ (\E n \in Names: Fail(n))

LastRouteSafe ==
  lastRoute.candidate = "" \/
    /\ lastRoute.candidate \in Names
    /\ Capability(lastRoute.candidate) = lastRoute.requestedCapability
    /\ Active(lastRoute.candidate, lastRoute.at)
    /\ lastRoute.remainingAfter >= 0

QuotaSafe == \A n \in Names: remaining[n] >= 0 /\ remaining[n] <= Quota(n)
CooldownSafe == \A n \in Names: cooldownUntil[n] = 0 \/ cooldownUntil[n] > now

Inv == LastRouteSafe /\ QuotaSafe /\ CooldownSafe

====
