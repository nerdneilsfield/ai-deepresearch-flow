# Semantic Push Command Design

## Context

`paper db api push` currently mixes three different push surfaces:

- paper metadata/admin API push
- static asset storage push
- semantic vector push from an existing local `embed-db`

That is acceptable for full snapshot publication, but it becomes confusing when the user only wants to push semantic data from a prebuilt local LanceDB index.

The ambiguity becomes worse around indexing:

- `paper db api push --start-idx/--end-idx` currently refers to the paper list derived from the snapshot DB
- semantic rows live in a different unit of ordering
- the remote semantic ingest API replaces data by `(doc_id, template_tag)` group, not by individual row

So a semantic-only workflow should not reuse the paper-oriented command surface.

## Requirements

- Add a dedicated semantic-only CLI command.
- The command must read from an existing local `embed-db` and remote config only.
- The command must support retrying semantic failures from `push-semantic-errors.json`.
- The command must support selecting a chunk window using explicit semantic terminology, not paper terminology.
- The command must not push partial remote groups.
- The command must remain black-box testable.

## Non-Goals

- Reworking `paper db api push` into a universal selector for every push unit.
- Introducing a new remote semantic ingest protocol.
- Supporting static storage push from this command.
- Supporting paper metadata push from this command.

## Recommended Command

Add a new command:

```bash
deepresearch-flow paper db api push-semantic \
  --embed-db ./vectors \
  --config remote.toml \
  --start-chunk-idx 1000 \
  --end-chunk-idx 2000
```

This command is semantic-only:

- no `snapshot-db`
- no `static-export-dir`
- no paper admin push
- no static storage push

## Index Semantics

The new command must avoid the ambiguous `start-idx` naming used by paper-oriented commands.

Use:

- `--start-chunk-idx`
- `--end-chunk-idx`

Semantics:

- 0-based
- `end` is exclusive
- `-1` means “to the end”

Example:

- `--start-chunk-idx 0 --end-chunk-idx 100` selects the first 100 semantic rows
- `--start-chunk-idx 1000` selects from row 1000 to the end

## Stable Chunk Ordering

The command must define a deterministic local semantic row order before applying chunk index slicing.

Recommended sort key:

1. `doc_id`
2. `template_tag`
3. `chunk_type`
4. `chunk_index`
5. `id`

This avoids relying on LanceDB’s physical row order.

## Group Safety

The remote semantic ingest API replaces content by `(doc_id, template_tag)` group.

That means selecting raw chunk rows and pushing only those rows would be unsafe: a chunk window could cut through a group and leave the remote group truncated.

So the command must use a two-step selection model:

1. read and sort all local semantic rows
2. apply the requested chunk window
3. collect the unique `(doc_id, template_tag)` groups touched by that window
4. expand back to the full local rows for those groups
5. batch and push the full group contents

This preserves the user-facing chunk-range workflow while keeping the remote write model correct.

## Retry Behavior

`--retry-failed` should accept `push-semantic-errors.json` only.

For this command:

- static retry reports are invalid
- semantic retry reports are replayed as stored request payloads

To keep semantics simple:

- `--retry-failed` and `--start-chunk-idx/--end-chunk-idx` should be mutually exclusive

Reason:

- retry mode replays previously failed semantic request groups
- chunk-window mode recomputes selection from the current local embed DB
- mixing both creates two competing selection models

## Output and UX

The command should print enough context to make the selected range understandable:

- remote URL
- total local chunk count
- requested chunk window
- selected group count
- expanded chunk count actually pushed
- semantic request count

If the selected window resolves to zero chunks or zero groups, print a clean “Nothing to push” message and exit successfully.

## Integration

Implement this as a sibling command under `paper db api`, not as a flag mode on `push`.

Suggested location:

- `paper db api push-semantic`

Use the existing semantic push building blocks:

- `load_remote_config`
- `load_index_meta`
- `open_store`
- `read_all_chunks`
- `group_chunks_for_push`
- `push_semantic_chunks`
- `write_error_report`

## Files Affected

- `python/deepresearch_flow/paper/db.py`
- `python/deepresearch_flow/paper/tests/test_db_api_push_cli.py`

## Testing Strategy

Black-box CLI tests should cover:

- the command pushes only semantic data
- `--start-chunk-idx/--end-chunk-idx` use chunk-window semantics
- selected chunk windows expand to full `(doc_id, template_tag)` groups before push
- `--retry-failed` replays semantic retry payloads
- `--retry-failed` rejects static retry reports
- range flags reject invalid values
- range flags cannot be combined with `--retry-failed`
- zero selected chunks exits cleanly without pushing

## Success Criteria

- users can push semantic data without involving snapshot DB or storage push
- chunk-range selection is explicit and not confused with paper index selection
- remote semantic writes stay group-safe
- retrying failed semantic batches remains straightforward
