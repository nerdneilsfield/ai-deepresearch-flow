[← Back to README](../../README.md)

# From PDFs to a snapshot: process one project with run.sh

This page shows how to turn a batch of PDFs and one BibTeX file into a deployable snapshot database. `utils init-run` generates a `run.sh`; you edit it and run it.

When it finishes, the project directory holds the OCR output, two summary JSON files, the translations, and the snapshot database and static assets under `dist/`.

## Before you start

You need:

- A checkout of the source repository. `run.sh` calls `uv run --project <repository root> deepresearch-flow`, and `init-run` writes the repository root into the script.
- `config.toml`: LLM providers and `main_model`. See "Configuration" in the [README](../../README.md).
- `ocr.toml`: the OCR backend configuration. Copy `ocr.example.toml` and set the token it refers to.
- `tmux`: the full run uses it to run three chains in parallel.
- `mmdc` (the Mermaid command-line tool): the `fix-mermaid-deep_read` step uses it to validate diagrams. Run `npm install` in the repository root. `run.sh` adds `<repository root>/node_modules/.bin` to `PATH`.

## Prepare the project directory

Create a directory by hand and put `pdf/` and exactly one `.bib` file in it:

```text
myproj/
├── pdf/          # all PDFs
└── myproj.bib    # BibTeX; the directory may hold only one .bib
```

All output goes into this directory, and every path is relative. You can zip the whole directory and move it.

## Generate and run

From the repository root, run:

```bash
uv run deepresearch-flow utils init-run ./myproj
```

The command writes `myproj/run.sh`. `init-run` takes no model argument. Every command in the script that calls an LLM carries the placeholder `--model PROVIDER/MODEL`, and you replace each one with a real `provider/model`. Different steps can use different models.

Open `run.sh` and replace each `PROVIDER/MODEL` with a model declared in `config.toml`, for example `openai/gpt-4o-mini`. There are 8 places, in the steps `fix-math`, `simple`, `fix-math-simple`, `deep_read`, `fix-math-deep_read`, `fix-mermaid-deep_read`, `translate`, and `fix-math-translated`.

Running `init-run` again in a directory that already has a `run.sh` overwrites that file, and your edits to the models and parameters are lost. Back up `run.sh` first if you want to keep them.

If a step that is about to run still contains the placeholder, `run.sh` stops before it runs any command and names the step.

Then run:

```bash
./myproj/run.sh
```

### init-run options

| Option | Default | Effect |
|---|---|---|
| `PROJECT` | none, required | The project directory. It must exist and contain `pdf/` and exactly one `.bib` |
| `--target-lang` | `zh` | Target language of the translation |
| `--config` | `config.toml` | LLM configuration. Resolved against the current directory and written into the script as an absolute path |
| `--ocr-config` | `ocr.toml` | OCR configuration, handled like `--config` |
| `--rounds` | `3` | Maximum retry rounds per step for failed items |

Configuration paths are written as absolute paths. After you move the project to another machine, edit the variables `CONFIG`, `OCR_CONFIG`, and `REPO` at the top of `run.sh`.

## The flow

A full run has three stages:

1. The `ocr` chain runs `ocr`, `fix`, `fix-math`, and `organize` in order.
2. Three chains run in parallel in tmux: the `simple` chain, the `deep_read` chain, and the `translate` chain. Each chain runs its steps in order.
3. After all three chains finish, `build` runs.

`embed`, `api push`, and `api update` are not in the script. Run them by hand.

### Checkpoints, interruption, and recovery

Model requests can stop because of timeouts, provider quotas, process crashes, or power loss. The flow saves checkpoints at task boundaries and processes only work that is not confirmed as successful after a restart:

- `paper extract` saves successful paper results. `deep_read` writes stage state under `paper_stage_outputs/`.
- Extraction results and stage state use a temporary file, file `fsync`, an atomic replacement, and a parent-directory `fsync`. This guarantee depends on correct `fsync` behavior from the local filesystem and hardware.
- Successfully translated nodes are written to `.translate_cache/`. The program removes the cache only after every node passes validation and the complete translation is safely published.
- If translation still has failed nodes, the program does not publish a normal translation, does not overwrite an existing normal output, and returns partial failure. A later run keeps and reuses the successful node cache.
- When a provider does not support an idempotency key, the client cannot remove the risk of duplicate requests or billing in the window where the provider may have accepted a request but the result was not persisted. Persisted successful tasks are not automatically requested again because the outer flow was interrupted.

Quota checks and waits belong to the individual step programs. `run.sh` does not provide a global quota scheduler across steps or processes, and in-memory quota state is not preserved across restarts.

`run.sh` owns step order, logs, notifications, and tmux chains. The individual programs own retries, fallback, quota waits, caches, and checkpoints. `logs/<step>.done` records only that the step most recently returned success; it does not prove that input contents, configuration contents, or prompt files are unchanged.

The script starts tmux only after the `ocr` chain finishes. To watch the parallel chains:

```bash
tmux -L drflow-myproj attach -t run
```

`myproj` is the project directory name. Each project uses its own tmux server, so the API keys and `NOTIFY_URL` in your shell reach every chain.

## Run selected steps

List step names on the command line, separated by commas or spaces:

```bash
./myproj/run.sh fix-simple,fix-math-simple
```

When you name steps:

- They run one after another in the given order, without tmux.
- They run again even if `logs/<step>.done` exists.
- The list stops at the first failing step.
- A misspelled step name is rejected before any step runs, and the script prints all step names.

`./myproj/run.sh list` prints all step names.

To stop the whole run from another terminal:

```bash
./myproj/run.sh stop
```

It stops the parallel chains and their child processes in this project's tmux session. OCR and `build` run in the foreground, so this command cannot stop them from another terminal. `kill` (SIGTERM) works for the main process as well. Do not use `kill -INT`: bash ignores SIGINT while it waits for a foreground command.

## Stopping the run

Ctrl-C, Ctrl-Z, and `kill` all stop the whole run, including the three tmux chains. The script:

- stops the tmux session's chain processes and the process groups the current step started;
- marks the current step as failed (`logs/<step>.failed`), so a rerun resumes at that step;
- sends an interrupted notification.

After stopping, fix the cause and run `./myproj/run.sh` again. Steps that already finished are skipped.

## The printed commands

Every command that runs is printed with a `+` prefix, so you can copy it to a terminal and rerun it:

```text
+ drflow recognize fix-math --input ocr -r --in-place --config /path/config.toml --model openai/gpt-4o-mini --workers 4 --batch-size 10 --report logs/fix-math-ocr.json
```

The commands also go to `logs/<step>.log`.

## Knobs you tune by experience

The settings block at the top of the script has four values to adjust:

| Variable | Default | Effect |
|---|---|---|
| `WORKERS` | 4 | Concurrency of `fix-math` and `fix-mermaid` |
| `BATCH_SIZE` | 10 | Formulas or diagrams per request |
| `MAX_CONCURRENCY` | 4 | Concurrent requests of `paper extract` |
| `OCR_WORKERS` | 4 | Files OCR'd at the same time |
| `EXTRACT_TIMEOUT` | 360 | Timeout in seconds per `paper extract` request |

These control concurrency, batch size, and the request timeout. The models are not here: each step has its own `--model` in its command line, and you change that one line.

`EXTRACT_TIMEOUT` has to be generous. A 100k-token document takes about 90 s on `doubao-seed-2.1-pro`, and the multi-stage templates take longer. A request that runs out of time is cut off, and the error report names the timeout.

## After a failure

The result of each step is recorded under `logs/`:

| File | Meaning |
|---|---|
| `logs/<step>.log` | Full output of the step |
| `logs/<step>.done` | The step succeeded |
| `logs/<step>.failed` | The step failed |

In a full run, a failed step ends its chain, and `build` does not run. Fix the cause and run `./myproj/run.sh` again. Steps that have a `.done` file are skipped, so the run resumes at the failed step. To redo a finished step, delete its `.done` file or name the step on the command line.

### Exit codes

The batch commands (`extract`, `translate`, `ocr`, `fix-math`, `fix-mermaid`) share these exit codes:

| Code | Meaning | What `run.sh` does |
|---|---|---|
| 0 | Everything succeeded | The step succeeds |
| 1 | The command itself failed; retrying will not help | The step fails |
| 3 | Outputs and the error report were written, but some items failed | The step fails and does not write `.done`; fix the cause and run it again |

Any other non-zero code counts as a failure.

Each step command starts with one of four retry modes. They match what the commands actually accept:

- `retry` runs the same command again with `--retry-failed`, which processes only the failed items. Only `paper extract`, `recognize fix-math`, and `recognize fix-mermaid` accept that option.
- `retry_stages` runs `deep_read` again with `--retry-failed-stages`, which processes only failed stages.
- `rerun` runs exactly the same command again. `recognize ocr` and `translator translate` have no `--retry-failed`, but they already skip finished files, so running them again is enough.
- `once` only prints a warning and does not retry. `recognize fix` and `recognize organize` cannot retry.

`run.sh` does not convert partial failure into success and does not write `.done` for it. `paper extract`, `fix-math`, and `fix-mermaid` use their error reports to select failed items; `deep_read` uses `--retry-failed-stages` and retries only failed stages. When translation fails, the program keeps the node cache and returns partial failure; rerun the same command to continue.

## Notifications

If the environment variable `NOTIFY_URL` is set, the script sends a message when each chain ends, when any step fails, and when `build` finishes:

```bash
export NOTIFY_URL='https://<notify-host>/api/<uuid>/get'
```

The service is [simple-telegram-notification-bot](https://github.com/nerdneilsfield/simple-telegram-notification-bot). The `<uuid>` in the URL works as a password, so the script reads it only from the environment and never writes it to a file. Without `NOTIFY_URL`, no message is sent. A failed send does not affect the run.

## Step reference

In the table, "Input" and "Output" are paths relative to the project directory. Steps marked "LLM" need the placeholder replaced with a real model.

| Step | Command | LLM | Input | Output | Retry mode |
|---|---|---|---|---|---|
| `ocr` | `recognize ocr` | No | `pdf/` | `ocr/` | `rerun` |
| `fix` | `recognize fix` | No | `ocr/` | modified in place | `once` |
| `fix-math` | `recognize fix-math` | Yes | `ocr/` | modified in place; report `logs/fix-math-ocr.json` | `retry` |
| `organize` | `recognize organize` | No | `ocr/` | `md_simple/`, `md_base64/` | `once` |
| `simple` | `paper extract` | Yes | `md_simple/` | `simple.json`; errors `logs/simple-errors.json` | `retry` |
| `fix-simple` | `recognize fix` | No | `simple.json` | modified in place | `once` |
| `fix-math-simple` | `recognize fix-math` | Yes | `simple.json` | modified in place; report `logs/fix-math-simple.json` | `retry` |
| `deep_read` | `paper extract` | Yes | `md_simple/` | `deep_read.json`; errors `logs/deep_read-errors.json` | `retry_stages` |
| `fix-deep_read` | `recognize fix` | No | `deep_read.json` | modified in place | `once` |
| `fix-math-deep_read` | `recognize fix-math` | Yes | `deep_read.json` | modified in place; report `logs/fix-math-deep_read.json` | `retry` |
| `fix-mermaid-deep_read` | `recognize fix-mermaid` | Yes | `deep_read.json` | modified in place; report `logs/fix-mermaid-deep_read.json` | `retry` |
| `translate` | `translator translate` | Yes | `md_base64/` | `md_base64_translated/` | `rerun` |
| `fix-translated` | `recognize fix` | No | `md_base64_translated/` | modified in place | `once` |
| `fix-math-translated` | `recognize fix-math` | Yes | `md_base64_translated/` | modified in place; report `logs/fix-math-translated.json` | `retry` |
| `build` | `paper db snapshot build` | No | the JSON files above, Markdown, `pdf/`, the `.bib` | `dist/<project>.db`, `dist/<project>-export/` | none |
| `embed` | `paper embed` | Yes | the snapshot and static export from `build` | `dist/<project>_embed/` | requires embedding configuration and a provider |
| `push-api` | `paper db api push --only-api` | Yes | the snapshot, static export, and `remote.toml` | remote API | pushes API data only |
| `push-storage` | `paper db api push --only-storage --storage-bundles` | No | the static export, `remote.toml`, and `7z` | local `dist/bundles/<batch-id>/` and remote `bundles/<batch-id>/` | run `apply.sh` on the remote server |

### Upload split static bundles

`push-storage` does not upload static files one by one. It creates one high-compression solid 7z archive and uploads 50 MB split volumes to WebDAV. The archive uses LZMA2, the highest compression level, and multithreading.

```bash
bash run.sh push-storage
```

The local output looks like this:

```text
dist/bundles/<batch-id>/
├── bundle.7z.001
├── bundle.7z.002
├── apply.sh
└── files.txt
```

The remote directory is below the WebDAV root configured in `remote.toml`:

```text
bundles/<batch-id>/
├── bundle.7z.001
├── bundle.7z.002
└── apply.sh
```

Install `7z` on the WebDAV server, enter the remote batch directory, and run:

```bash
bash apply.sh /absolute/path/to/static-export
```

The script deletes the remote `.7z.*` volumes only after extraction succeeds. It keeps `apply.sh`. The local volumes are never deleted. If extraction fails, the remote volumes remain available for another attempt.

Directory creation and uploads retry network failures and retryable HTTP errors twice, with a fixed 60-second cooldown. Only the failed operation repeats. Successful volumes are not uploaded again, and compression does not restart. Authentication failures stop immediately. Ctrl-C interrupts the cooldown.

Notes:

- `fix-mermaid` works only on `deep_read.json`.
- `simple` and `deep_read` run inside `logs/simple/` and `logs/deep_read/`, because `paper extract` writes its intermediate results to `paper_stage_outputs/` in the current directory.
- If `dist/<project>.db` already exists, `build` adds `--previous-snapshot-db` so the `paper_id` values stay the same after the rebuild.
- `translate` writes debug output to `logs/translator/`.

## Run the steps by hand

Each step in `run.sh` is an ordinary command. To work without the script, copy the line you need. The example below runs the `deep_read` chain from the project directory:

```bash
mkdir -p logs/deep_read
(cd logs/deep_read && uv run deepresearch-flow paper extract \
  --input ../../md_simple --config <path to config.toml> \
  --model PROVIDER/MODEL --prompt-template deep_read \
  --output ../../deep_read.json --errors ../../logs/deep_read-errors.json)

uv run deepresearch-flow recognize fix --input deep_read.json --json --in-place

uv run deepresearch-flow recognize fix-math --input deep_read.json --json --in-place \
  --config <path to config.toml> --model PROVIDER/MODEL --report logs/fix-math-deep_read.json
```

`<path to config.toml>` and `PROVIDER/MODEL` are placeholders; replace them before you run the commands. If a command exits with 3, run the same command again with `--retry-failed` added.

## Next steps

`run.sh` ends at `build`. After that:

- Build the vector index: see semantic search in the [Reference](reference.md).
- Deploy the snapshot and static assets: see the [Deployment Guide](deployment.md).
- Push the snapshot to a remote server or update existing papers: see [API and MCP](api-and-mcp.md).
- Build incrementally, merge, or supplement existing data: see [Advanced Workflows](workflow.md).
