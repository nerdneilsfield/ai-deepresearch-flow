---- MODULE OAuthClientCache ----
EXTENDS Naturals, FiniteSets, TLC

(***************************************************************************)
(* Black-box OAuth/MCP authorization model.  The cache portion models       *)
(* scoped client lookup.  The OAuth portion explicitly distinguishes a       *)
(* malformed missing client from a syntactically recoverable dynamic client  *)
(* whose DCR cache entry was lost.  Recovery may recreate a persisted or     *)
(* memory-only registration, but it must only restart authorization; it      *)
(* must never issue a token until GitHub auth, PKCE, redirect, and resource  *)
(* checks are all valid.                                                    *)
(***************************************************************************)

CONSTANTS Principals, Scopes, MaxTime, TTL

VARIABLES now,
          revoked,
          cache,
          lastReturn,
          clientState,
          resourceOk,
          pkceOk,
          redirectOk,
          githubAuthOk,
          reauthPending,
          issued

CacheKeys == Principals \X Scopes
EmptyEntry == [owner |-> "", scope |-> "", expiresAt |-> 0]
EmptyReturn == [requestedOwner |-> "", requestedScope |-> "", owner |-> "", scope |-> "", expiresAt |-> 0]
RecoveredStates == {"recovered_persisted", "recovered_memory_only"}
GoodClientStates == {"registered_durable"} \cup RecoveredStates
MissingClientStates == {"missing_recoverable", "missing_malformed"}

Init ==
  /\ now = 0
  /\ revoked = {}
  /\ cache = [k \in CacheKeys |-> EmptyEntry]
  /\ lastReturn = EmptyReturn
  /\ clientState = "registered_durable"
  /\ resourceOk = TRUE
  /\ pkceOk = TRUE
  /\ redirectOk = TRUE
  /\ githubAuthOk = FALSE
  /\ reauthPending = FALSE
  /\ issued = FALSE

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
  /\ UNCHANGED <<now, revoked, clientState, resourceOk, pkceOk, redirectOk,
                  githubAuthOk, reauthPending, issued>>

Tick ==
  /\ now < MaxTime
  /\ now' = now + 1
  /\ cache' = [k \in CacheKeys |-> IF /\ cache[k].expiresAt > now'
                                      /\ cache[k].owner \notin revoked
                                   THEN cache[k]
                                   ELSE EmptyEntry]
  /\ lastReturn' = EmptyReturn
  /\ UNCHANGED <<revoked, clientState, resourceOk, pkceOk, redirectOk,
                  githubAuthOk, reauthPending, issued>>

Revoke(p) ==
  /\ p \notin revoked
  /\ revoked' = revoked \cup {p}
  /\ cache' = [k \in CacheKeys |-> IF cache[k].owner = p THEN EmptyEntry ELSE cache[k]]
  /\ lastReturn' = EmptyReturn
  /\ issued' = FALSE
  /\ UNCHANGED <<now, clientState, resourceOk, pkceOk, redirectOk, githubAuthOk, reauthPending>>

LostClientRecoverable ==
  /\ clientState \in GoodClientStates
  /\ clientState' = "missing_recoverable"
  /\ githubAuthOk' = FALSE
  /\ reauthPending' = FALSE
  /\ issued' = FALSE
  /\ lastReturn' = EmptyReturn
  /\ UNCHANGED <<now, revoked, cache, resourceOk, pkceOk, redirectOk>>

MalformedClientRequest ==
  /\ clientState' = "missing_malformed"
  /\ githubAuthOk' = FALSE
  /\ reauthPending' = FALSE
  /\ issued' = FALSE
  /\ lastReturn' = EmptyReturn
  /\ UNCHANGED <<now, revoked, cache, resourceOk, pkceOk, redirectOk>>

RecoverMissingPersisted ==
  /\ clientState = "missing_recoverable"
  /\ resourceOk
  /\ redirectOk
  /\ clientState' = "recovered_persisted"
  /\ githubAuthOk' = FALSE
  /\ reauthPending' = TRUE
  /\ issued' = FALSE
  /\ lastReturn' = EmptyReturn
  /\ UNCHANGED <<now, revoked, cache, resourceOk, pkceOk, redirectOk>>

RecoverMissingMemoryOnly ==
  /\ clientState = "missing_recoverable"
  /\ resourceOk
  /\ redirectOk
  /\ clientState' = "recovered_memory_only"
  /\ githubAuthOk' = FALSE
  /\ reauthPending' = TRUE
  /\ issued' = FALSE
  /\ lastReturn' = EmptyReturn
  /\ UNCHANGED <<now, revoked, cache, resourceOk, pkceOk, redirectOk>>

GithubCallbackOk ==
  /\ clientState \in GoodClientStates
  /\ resourceOk
  /\ redirectOk
  /\ clientState' = clientState
  /\ githubAuthOk' = TRUE
  /\ reauthPending' = FALSE
  /\ issued' = FALSE
  /\ lastReturn' = EmptyReturn
  /\ UNCHANGED <<now, revoked, cache, resourceOk, pkceOk, redirectOk>>

TokenAfterReauth ==
  /\ clientState \in GoodClientStates
  /\ resourceOk
  /\ pkceOk
  /\ redirectOk
  /\ githubAuthOk
  /\ ~reauthPending
  /\ clientState' = clientState
  /\ issued' = TRUE
  /\ lastReturn' = EmptyReturn
  /\ UNCHANGED <<now, revoked, cache, resourceOk, pkceOk, redirectOk, githubAuthOk, reauthPending>>

ResourceMismatch ==
  /\ resourceOk
  /\ resourceOk' = FALSE
  /\ issued' = FALSE
  /\ lastReturn' = EmptyReturn
  /\ UNCHANGED <<now, revoked, cache, clientState, pkceOk, redirectOk, githubAuthOk, reauthPending>>

PkceMismatch ==
  /\ pkceOk
  /\ pkceOk' = FALSE
  /\ issued' = FALSE
  /\ lastReturn' = EmptyReturn
  /\ UNCHANGED <<now, revoked, cache, clientState, resourceOk, redirectOk, githubAuthOk, reauthPending>>

RedirectMismatch ==
  /\ redirectOk
  /\ redirectOk' = FALSE
  /\ issued' = FALSE
  /\ lastReturn' = EmptyReturn
  /\ UNCHANGED <<now, revoked, cache, clientState, resourceOk, pkceOk, githubAuthOk, reauthPending>>

Next ==
  \/ Tick
  \/ (\E p \in Principals, s \in Scopes: Acquire(p, s))
  \/ (\E p \in Principals: Revoke(p))
  \/ LostClientRecoverable
  \/ MalformedClientRequest
  \/ RecoverMissingPersisted
  \/ RecoverMissingMemoryOnly
  \/ GithubCallbackOk
  \/ TokenAfterReauth
  \/ ResourceMismatch
  \/ PkceMismatch
  \/ RedirectMismatch

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

TokenSafe ==
  ~issued \/
    /\ clientState \in GoodClientStates
    /\ resourceOk
    /\ pkceOk
    /\ redirectOk
    /\ githubAuthOk
    /\ ~reauthPending

MissingSafe ==
  clientState \notin MissingClientStates \/
    /\ ~issued
    /\ ~reauthPending
    /\ ~githubAuthOk

Inv == ScopedReturn /\ CacheSafe /\ TokenSafe /\ MissingSafe

====
