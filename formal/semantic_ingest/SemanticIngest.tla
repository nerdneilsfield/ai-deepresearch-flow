---- MODULE SemanticIngest ----
EXTENDS FiniteSets, TLC

(***************************************************************************)
(* Black-box semantic ingest model.  Searchable index records may appear   *)
(* only after discovery, parse, chunk, and embedding of the same document   *)
(* fingerprint. The Python bounded checker is the gated MODEL_PROOF.       *)
(***************************************************************************)

CONSTANTS Docs, Fingerprints, Chunks

VARIABLES discovered, parsed, chunked, embedded, indexed

DocVersions == Docs \X Fingerprints
ChunkKeys == Docs \X Fingerprints \X Chunks

Init ==
  /\ discovered = Docs \X {"v1"}
  /\ parsed = {}
  /\ chunked = {}
  /\ embedded = {}
  /\ indexed = {}

Rediscover(d) ==
  /\ d \in Docs
  /\ <<d, "v2">> \notin discovered
  /\ discovered' = discovered \cup {<<d, "v2">>}
  /\ UNCHANGED <<parsed, chunked, embedded, indexed>>

Parse(dv) ==
  /\ dv \in discovered
  /\ dv \notin parsed
  /\ parsed' = parsed \cup {dv}
  /\ UNCHANGED <<discovered, chunked, embedded, indexed>>

Chunk(k) ==
  /\ k \in ChunkKeys
  /\ <<k[1], k[2]>> \in parsed
  /\ k \notin chunked
  /\ chunked' = chunked \cup {k}
  /\ UNCHANGED <<discovered, parsed, embedded, indexed>>

Embed(k) ==
  /\ k \in chunked
  /\ k \notin embedded
  /\ embedded' = embedded \cup {k}
  /\ UNCHANGED <<discovered, parsed, chunked, indexed>>

Index(k) ==
  /\ k \in embedded
  /\ k \notin indexed
  /\ indexed' = indexed \cup {k}
  /\ UNCHANGED <<discovered, parsed, chunked, embedded>>

Next ==
  (\E d \in Docs: Rediscover(d)) \/
  (\E dv \in DocVersions: Parse(dv)) \/
  (\E k \in ChunkKeys: Chunk(k) \/ Embed(k) \/ Index(k))

ParsedAfterDiscovered == \A dv \in parsed: dv \in discovered
ChunkedAfterParsed == \A k \in chunked: <<k[1], k[2]>> \in parsed
EmbeddedAfterChunked == \A k \in embedded: k \in chunked
IndexedAfterEmbedded == \A k \in indexed: k \in embedded

Inv == ParsedAfterDiscovered /\ ChunkedAfterParsed /\ EmbeddedAfterChunked /\ IndexedAfterEmbedded

====
