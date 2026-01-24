# Change: Graceful shutdown for extract

## Why
Users need to stop long-running extraction safely without losing in-flight work. A graceful shutdown should stop new work and wait for in-flight requests to finish before exiting.

## What Changes
- Handle SIGINT/SIGTERM to trigger graceful shutdown during extract.
- Stop dequeuing new documents/stages once shutdown begins.
- Allow in-flight requests to complete and flush outputs/errors before exit.
- Log shutdown state transitions (requested, draining, completed).

## Impact
- Affected specs: paper-extract
- Affected code: extract scheduler loops, signal handling, logging
