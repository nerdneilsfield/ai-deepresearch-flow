"""CLI client for the restricted Agent pipeline API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import httpx


def _client(base_url: str, token: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )


def _emit(payload: Any) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _request_error(response: httpx.Response) -> click.ClickException:
    try:
        payload = response.json()
    except ValueError:
        payload = {"status": response.status_code, "body": response.text}
    return click.ClickException(json.dumps(payload, ensure_ascii=False))


@click.group("agent")
def agent() -> None:
    """Submit and monitor papers through the Agent API."""


@agent.command("submit")
@click.option("--api", "api_url", envvar="PAPER_DB_AGENT_API", required=True)
@click.option("--token", envvar="PAPER_DB_AGENT_TOKEN", required=True)
@click.option("--pdf", "pdf_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--idempotency-key", required=True)
@click.option("--bibtex", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--ocr-model")
@click.option("--extract-model")
@click.option("--translate-model")
def submit(
    api_url: str,
    token: str,
    pdf_path: Path,
    idempotency_key: str,
    bibtex: Path | None,
    ocr_model: str | None,
    extract_model: str | None,
    translate_model: str | None,
) -> None:
    """Submit one PDF without invoking processing subcommands locally."""
    data = {
        key: value
        for key, value in (
            ("ocr_model", ocr_model),
            ("extract_model", extract_model),
            ("translate_model", translate_model),
        )
        if value
    }
    files: list[tuple[str, tuple[str, Any, str]]] = [
        ("pdfs", (pdf_path.name, pdf_path.open("rb"), "application/pdf"))
    ]
    bib_file = None
    try:
        if bibtex is not None:
            bib_file = bibtex.open("rb")
            files.append(("bibtex", (bibtex.name, bib_file, "text/plain")))
        with _client(api_url, token) as client:
            response = client.post(
                "/submissions",
                headers={"Idempotency-Key": idempotency_key},
                data=data,
                files=files,
            )
        if response.is_error:
            raise _request_error(response)
        _emit(response.json())
    finally:
        for _name, (_filename, stream, _media_type) in files:
            stream.close()


def _get(api_url: str, token: str, path: str) -> None:
    with _client(api_url, token) as client:
        response = client.get(path)
    if response.is_error:
        raise _request_error(response)
    _emit(response.json())


@agent.command("status")
@click.argument("job_id")
@click.option("--api", "api_url", envvar="PAPER_DB_AGENT_API", required=True)
@click.option("--token", envvar="PAPER_DB_AGENT_TOKEN", required=True)
def status(job_id: str, api_url: str, token: str) -> None:
    """Read one owned job status."""
    _get(api_url, token, f"/jobs/{job_id}")


@agent.command("submission")
@click.argument("submission_id")
@click.option("--api", "api_url", envvar="PAPER_DB_AGENT_API", required=True)
@click.option("--token", envvar="PAPER_DB_AGENT_TOKEN", required=True)
def submission(submission_id: str, api_url: str, token: str) -> None:
    """Read one owned submission and its jobs."""
    _get(api_url, token, f"/submissions/{submission_id}")


@agent.command("retry")
@click.argument("job_id")
@click.option("--api", "api_url", envvar="PAPER_DB_AGENT_API", required=True)
@click.option("--token", envvar="PAPER_DB_AGENT_TOKEN", required=True)
def retry(job_id: str, api_url: str, token: str) -> None:
    """Retry a failed owned job using its existing checkpoint."""
    with _client(api_url, token) as client:
        response = client.post(f"/jobs/{job_id}/retry", json={})
    if response.is_error:
        raise _request_error(response)
    _emit(response.json())


@agent.command("cancel")
@click.argument("job_id")
@click.option("--api", "api_url", envvar="PAPER_DB_AGENT_API", required=True)
@click.option("--token", envvar="PAPER_DB_AGENT_TOKEN", required=True)
def cancel(job_id: str, api_url: str, token: str) -> None:
    """Request cancellation of an owned job."""
    with _client(api_url, token) as client:
        response = client.post(f"/jobs/{job_id}/cancel")
    if response.is_error:
        raise _request_error(response)
    _emit(response.json())
