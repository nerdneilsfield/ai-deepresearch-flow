---- MODULE OAuthClientCache ----
EXTENDS Naturals, FiniteSets, TLC

(***************************************************************************)
(* Black-box OAuth client cache model.  The observable contract is that a   *)
(* returned client matches the requested principal and scope, is unexpired, *)
(* and does not survive credential revocation.  The Python bounded checker  *)
(* is the gated MODEL_PROOF for this draft.                                *)
(***************************************************************************)

CONSTANTS Principals, Scopes, MaxTime, TTL

VARIABLES now, revoked, cache, lastReturn

CacheKeys == Principals \X Scopes

Init ==
  /\ now = 0
  /\ revoked = {}
  /\ cache = [k \in CacheKeys |-> [owner |-> "", scope |-> "", expiresAt |-> 0]]
  /\ lastReturn = [requestedOwner |-> "", requestedScope |-> "", owner |-> "", scope |-> "", expiresAt |-> 0]

LiveEntry(e) == /\ e.expiresAt > now /\ e.owner \notin revoked

Acquire(p, s) ==
  /\ p \notin revoked
  /\ LET k == <<p, s>>
         e == cache[k]
         nextEntry == IF LiveEntry(e) THEN e ELSE [owner |-> p, scope |-> s, expiresAt |-> now + TTL]
     IN
       /\ cache' = [cache EXCEPT ![k] = nextEntry]
       /\ lastReturn' = [requestedOwner |-> p,
                         requestedScope |-> s,
                         owner |-> nextEntry.owner,
                         scope |-> nextEntry.scope,
                         expiresAt |-> nextEntry.expiresAt]
  /\ UNCHANGED <<now, revoked>>

Tick ==
  /\ now < MaxTime
  /\ now' = now + 1
  /\ cache' = [k \in CacheKeys |-> IF /\ cache[k].expiresAt > now'
                                      /\ cache[k].owner \notin revoked
                                   THEN cache[k]
                                   ELSE [owner |-> "", scope |-> "", expiresAt |-> 0]]
  /\ UNCHANGED <<revoked, lastReturn>>

Revoke(p) ==
  /\ p \notin revoked
  /\ revoked' = revoked \cup {p}
  /\ cache' = [k \in CacheKeys |-> IF cache[k].owner = p
                                   THEN [owner |-> "", scope |-> "", expiresAt |-> 0]
                                   ELSE cache[k]]
  /\ UNCHANGED <<now, lastReturn>>

Next == Tick \/ (\E p \in Principals, s \in Scopes: Acquire(p, s)) \/ (\E p \in Principals: Revoke(p))

ScopedReturn ==
  lastReturn.owner = "" \/
    /\ lastReturn.owner = lastReturn.requestedOwner
    /\ lastReturn.scope = lastReturn.requestedScope
    /\ lastReturn.expiresAt > now
    /\ lastReturn.owner \notin revoked

CacheSafe ==
  \A p \in Principals, s \in Scopes:
    LET e == cache[<<p, s>>] IN
      e.owner = "" \/ (/\ e.owner = p /\ e.scope = s /\ e.expiresAt > now /\ e.owner \notin revoked)

Inv == ScopedReturn /\ CacheSafe

====
