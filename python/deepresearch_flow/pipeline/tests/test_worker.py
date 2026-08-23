from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from deepresearch_flow.pipeline import ArtifactStore, PipelineConfig
from deepresearch_flow.pipeline.config import ModelAllowlist
from deepresearch_flow.pipeline.ingestion import BatchIngestor, UploadPart
from deepresearch_flow.pipeline.state import PipelineState
from deepresearch_flow.pipeline.worker import (
    PROCESSING_STEPS,
    PipelineWorker,
    build_production_adapters,
)


class FakeAdapters:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def ocr(self, pdf_path: Path, model_key: str) -> str:
        assert pdf_path.read_bytes().startswith(b"%PDF-")
        self.calls.append(("ocr", model_key))
        return "# Tiny paper\n\nSource text."

    def source_repair(self, markdown: str, **kwargs: object) -> str:
        self.calls.append(("source_repair", kwargs.get("model_key")))
        return markdown + "\n"

    def math_repair(self, markdown: str, **kwargs: object) -> str:
        self.calls.append(("math_repair", kwargs.get("model_key")))
        return markdown

    def organize(self, markdown: str, **kwargs: object) -> str:
        self.calls.append(("organize", kwargs.get("model_key")))
        return markdown.strip() + "\n"

    def extract(self, markdown: str, model_key: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("extract", model_key))
        return {"title": "Tiny paper", "source": markdown}

    def validate(self, summary: dict[str, object], **kwargs: object) -> bool:
        self.calls.append(("validation", kwargs.get("model_key")))
        return True

    def summary_repair(self, summary: dict[str, object], **kwargs: object) -> dict[str, object]:
        self.calls.append(("summary_repair", kwargs.get("model_key")))
        return summary

    def translate(self, markdown: str, model_key: str, **kwargs: object) -> str:
        self.calls.append(("translate", model_key))
        return "翻译：" + markdown

    def translation_repair(self, markdown: str, **kwargs: object) -> str:
        self.calls.append(("translation_repair", kwargs.get("model_key")))
        return markdown


def _setup(tmp_path: Path, *, heartbeat_seconds: int = 30, validation_retry_limit: int = 2) -> tuple[PipelineConfig, PipelineState, ArtifactStore, str]:
    config = PipelineConfig(
        enabled=True,
        work_dir=str(tmp_path / "work"),
        snapshot_root=str(tmp_path / "formal"),
        queue_db=str(tmp_path / "queue.sqlite3"),
        ocr=ModelAllowlist(("ocr-a", "ocr-b"), "ocr-a"),
        extract=ModelAllowlist(("extract-a",), "extract-a"),
        translate=ModelAllowlist(("translate-a",), "translate-a"),
        heartbeat_seconds=heartbeat_seconds,
        validation_retry_limit=validation_retry_limit,
    )
    artifacts = ArtifactStore(tmp_path / "work", tmp_path / "formal")
    state = PipelineState(tmp_path / "queue.sqlite3", artifact_store=artifacts)
    service = BatchIngestor(config, state, artifacts)
    result = service.ingest(
        [UploadPart("tiny.pdf", BytesIO(b"%PDF-1.7 tiny"))],
        selected_models={"ocr": "ocr-a", "extract": "extract-a", "translate": "translate-a"},
    )
    return config, state, artifacts, result.jobs[0]


def test_worker_runs_fixed_steps_and_emits_protected_preview(tmp_path: Path) -> None:
    config, state, artifacts, job_id = _setup(tmp_path)
    adapters = FakeAdapters()

    result = PipelineWorker(config, state, artifacts, adapters=adapters, worker_id="test-worker").run_job(job_id)

    assert result.status == "review_ready"
    assert result.preview_digest
    assert [name for name, _ in adapters.calls] == list(PROCESSING_STEPS[:-1])
    assert [model for name, model in adapters.calls if name in {"ocr", "extract", "translate"}] == [
        "ocr-a",
        "extract-a",
        "translate-a",
    ]
    assert all(model is None for name, model in adapters.calls if name not in {"ocr", "extract", "translate"})
    assert result.preview.source_markdown.read_text(encoding="utf-8").startswith("# Tiny paper")
    assert json.loads(result.preview.summary_json.read_text(encoding="utf-8"))["title"] == "Tiny paper"
    assert result.preview.translated_markdown.read_text(encoding="utf-8").startswith("翻译：")
    assert result.preview.pdf.read_bytes().startswith(b"%PDF-")
    assert state.step_artifact(job_id, "preview") is not None
    assert state.get_job(job_id)["preview_digest"] == result.preview_digest


def test_worker_failure_is_public_safe_and_retry_resumes_failed_step(tmp_path: Path) -> None:
    config, state, artifacts, job_id = _setup(tmp_path)
    adapters = FakeAdapters()
    original = adapters.source_repair

    def fail_source(markdown: str, **kwargs: object) -> str:
        adapters.calls.append(("source_repair", kwargs.get("model_key")))
        raise RuntimeError("provider token=secret-token body={secret} /tmp/private.pdf")

    adapters.source_repair = fail_source  # type: ignore[method-assign]
    failed = PipelineWorker(config, state, artifacts, adapters=adapters, worker_id="first").run_job(job_id)

    assert failed.status == "failed"
    assert failed.failed_step == "source_repair"
    attempt = state.list_attempts(job_id, "source_repair")[-1]
    assert attempt["error_type"] == "runtimeerror"
    assert attempt["retryable"] is True
    assert "secret-token" not in str(attempt["error"])
    assert "/tmp/private.pdf" not in str(attempt["error"])

    adapters.source_repair = original  # type: ignore[method-assign]
    resumed = PipelineWorker(config, state, artifacts, adapters=adapters, worker_id="second").run_job(job_id)

    assert resumed.status == "review_ready"
    names = [name for name, _ in adapters.calls]
    assert names.count("ocr") == 1
    assert names.count("source_repair") == 2


@pytest.mark.parametrize("failed_step", list(PROCESSING_STEPS[:-1]))
def test_each_remote_step_failure_is_recorded(tmp_path: Path, failed_step: str) -> None:
    config, state, artifacts, job_id = _setup(tmp_path / failed_step)
    adapters = FakeAdapters()
    adapter_name = "validate" if failed_step == "validation" else failed_step
    original = getattr(adapters, adapter_name)

    def fail(value: Any, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"{failed_step} failure")

    setattr(adapters, adapter_name, fail)
    result = PipelineWorker(config, state, artifacts, adapters=adapters, worker_id=f"fail-{failed_step}").run_job(job_id)

    assert result.status == "failed"
    assert result.failed_step == failed_step
    assert state.list_attempts(job_id, failed_step)[-1]["status"] == "failed"
    assert state.list_attempts(job_id, failed_step)[-1]["duration_ms"] is not None
    setattr(adapters, adapter_name, original)


def test_worker_replays_from_corrupt_checkpoint(tmp_path: Path) -> None:
    config, state, artifacts, job_id = _setup(tmp_path)
    first_adapters = FakeAdapters()
    first = PipelineWorker(config, state, artifacts, adapters=first_adapters, worker_id="first").run_job(job_id)
    assert first.status == "review_ready"
    ocr = artifacts.resolve(job_id, "ocr")
    assert ocr is not None
    ocr.path.write_bytes(b"corrupt")

    second_adapters = FakeAdapters()
    second = PipelineWorker(config, state, artifacts, adapters=second_adapters, worker_id="restart").run_job(job_id)

    assert second.status == "review_ready"
    assert [name for name, _ in second_adapters.calls] == list(PROCESSING_STEPS[:-1])
    assert state.step_artifact(job_id, "ocr") is not None


def test_validation_retry_is_bounded_and_visible(tmp_path: Path) -> None:
    config, state, artifacts, job_id = _setup(tmp_path, validation_retry_limit=2)
    adapters = FakeAdapters()
    calls: list[int] = []

    def invalid(summary: dict[str, object], *, attempt: int = 0, **kwargs: object) -> bool:
        calls.append(attempt)
        return False

    adapters.validate = invalid
    result = PipelineWorker(config, state, artifacts, adapters=adapters, worker_id="validator").run_job(job_id)

    assert result.status == "failed"
    assert result.failed_step == "validation"
    assert calls == [0, 1, 2]
    attempts = state.list_attempts(job_id, "validation")
    assert len(attempts) == 3
    assert attempts[-1]["error_type"] == "validation_failed"
    assert attempts[-1]["retryable"] is False


def test_cancelled_remote_result_is_not_promoted(tmp_path: Path) -> None:
    config, state, artifacts, job_id = _setup(tmp_path)
    adapters = FakeAdapters()

    async def cancel_in_ocr(pdf_path: Path, model_key: str) -> str:
        state.request_cancel(job_id)
        await asyncio.sleep(0)
        return "must not be promoted"

    adapters.ocr = cancel_in_ocr
    result = PipelineWorker(config, state, artifacts, adapters=adapters, worker_id="cancel").run_job(job_id)

    assert result.status == "cancelled"
    try:
        assert artifacts.resolve(job_id, "ocr") is None
    except FileNotFoundError:
        pass
    assert state.step_artifact(job_id, "ocr") is None
    assert state.list_attempts(job_id, "ocr")[-1]["status"] == "cancelled"


def test_heartbeat_is_written_during_remote_call(tmp_path: Path) -> None:
    config, state, artifacts, job_id = _setup(tmp_path, heartbeat_seconds=1)
    adapters = FakeAdapters()

    async def slow_ocr(pdf_path: Path, model_key: str) -> str:
        await asyncio.sleep(1.1)
        return "# slow"

    adapters.ocr = slow_ocr
    result = PipelineWorker(config, state, artifacts, adapters=adapters, worker_id="heartbeat").run_job(job_id)

    assert result.status == "review_ready"
    assert state.heartbeat_metadata(job_id) is not None
    assert state.heartbeat_metadata(job_id)["owner"] == "heartbeat"  # type: ignore[index]


def test_ambiguous_bibtex_requires_attention_but_absent_is_review_ready(tmp_path: Path) -> None:
    config, state, artifacts, job_id = _setup(tmp_path)
    batch_id = state.get_job(job_id)["batch_id"]
    state.persist_bibtex_entries(batch_id, [{"key": "a", "title": "Tiny paper"}, {"key": "b", "title": "Tiny paper"}])
    result = PipelineWorker(config, state, artifacts, adapters=FakeAdapters(), worker_id="bib").run_job(job_id)
    assert result.status == "needs_attention"

    config2, state2, artifacts2, job2 = _setup(tmp_path / "absent")
    result2 = PipelineWorker(config2, state2, artifacts2, adapters=FakeAdapters(), worker_id="bib2").run_job(job2)
    assert result2.status == "review_ready"


def test_unique_bibtex_match_is_review_ready_and_bound(tmp_path: Path) -> None:
    config, state, artifacts, job_id = _setup(tmp_path)
    batch_id = state.get_job(job_id)["batch_id"]
    state.persist_bibtex_entries(batch_id, [{"key": "tiny-ref", "title": "Tiny paper"}])

    result = PipelineWorker(config, state, artifacts, adapters=FakeAdapters(), worker_id="unique").run_job(job_id)

    assert result.status == "review_ready"
    assert result.preview.bibtex_status == "matched"
    assert state.get_job_bibtex_key(job_id) == "tiny-ref"


def test_worker_restart_after_completion_reuses_all_checkpoints(tmp_path: Path) -> None:
    config, state, artifacts, job_id = _setup(tmp_path)
    first_adapters = FakeAdapters()
    first = PipelineWorker(config, state, artifacts, adapters=first_adapters, worker_id="first").run_job(job_id)
    assert first.status == "review_ready"

    second_adapters = FakeAdapters()
    second = PipelineWorker(config, state, artifacts, adapters=second_adapters, worker_id="second").run_job(job_id)

    assert second.status == "review_ready"
    assert second.preview_digest == first.preview_digest
    assert second_adapters.calls == []


def test_changed_configuration_fails_before_remote_model_call(tmp_path: Path) -> None:
    config, state, artifacts, job_id = _setup(tmp_path)
    changed = config.__class__(
        **{
            **config.__dict__,
            "ocr": ModelAllowlist(("ocr-new",), "ocr-new"),
        }
    )
    adapters = FakeAdapters()

    result = PipelineWorker(changed, state, artifacts, adapters=adapters, worker_id="invalidated").run_job(job_id)

    assert result.status == "failed"
    assert result.error_type == "model_invalidated"
    assert adapters.calls == []


def test_lease_loss_does_not_promote_inflight_result(tmp_path: Path) -> None:
    config, state, artifacts, job_id = _setup(tmp_path)
    adapters = FakeAdapters()

    async def lose_lease(pdf_path: Path, model_key: str) -> str:
        lease = state.get_job(job_id)["lease_token"]
        assert isinstance(lease, str)
        state.recover_expired()  # no-op while unexpired
        # Administrative lease takeover simulates Supervisor recovery.
        state.request_cancel(job_id)
        return "stale result"

    adapters.ocr = lose_lease
    result = PipelineWorker(config, state, artifacts, adapters=adapters, worker_id="lost").run_job(job_id)

    assert result.status == "cancelled"
    assert state.step_artifact(job_id, "ocr") is None


def _write_paper_config(path: Path) -> Path:
    path.write_text(
        """
        [[providers]]
        name = "openai"
        type = "openai_compatible"

        [[providers.base]]
        url = "https://example.invalid/v1"
        weight = 1

        [[providers.base.key]]
        value = "runtime-key"
        weight = 1

        [[providers.models]]
        model_name = "extract-model"
        is_support_json_schema = true

        [[providers.models]]
        model_name = "translate-model"

        [[main_model]]
        model = "openai/extract-model"
        weight = 1
        """,
        encoding="utf-8",
    )
    return path


def test_production_builder_loads_paper_config_and_constructs_provider_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_config_path = _write_paper_config(tmp_path / "paper.toml")

    class FakeOcrBackend:
        def ocr(self, path: Path) -> str:
            assert path.read_bytes().startswith(b"%PDF-")
            return "# OCR"

    extract_call: dict[str, Any] = {}

    async def fake_extract_documents(**kwargs: Any) -> None:
        extract_call.update(kwargs)
        kwargs["output_path"].write_text(
            json.dumps({"template_tag": "simple", "papers": [{"paper_title": "T", "paper_authors": []}]}),
            encoding="utf-8",
        )
        kwargs["errors_path"].write_text("[]", encoding="utf-8")

    monkeypatch.setattr(
        "deepresearch_flow.paper.extract.extract_documents", fake_extract_documents
    )

    class FakeTranslator:
        def __init__(self, cfg: Any) -> None:
            self.cfg = cfg

        async def translate(self, **kwargs: Any) -> Any:
            extract_call["translate_provider"] = kwargs["provider"]
            extract_call["translate_model"] = kwargs["model"]
            return SimpleNamespace(translated_text="translated")

    monkeypatch.setattr("deepresearch_flow.translator.engine.MarkdownTranslator", FakeTranslator)
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.7 tiny")
    adapters = build_production_adapters(
        paper_config_path=paper_config_path,
        ocr_backend=FakeOcrBackend(),
        staging_root=tmp_path / "staging",
    )

    assert asyncio.run(adapters.ocr(input_pdf, "ocr-model")) == "# OCR"
    summary = asyncio.run(adapters.extract("markdown", "openai/extract-model"))
    translated = asyncio.run(adapters.translate("markdown", "openai/translate-model"))

    assert summary["paper_title"] == "T"
    assert translated == "translated"
    assert extract_call["model"] == "extract-model"
    assert extract_call["provider"].name == "openai"
    assert extract_call["model_selector"].fixed_model == "openai/extract-model"
    assert extract_call["translate_provider"].name == "openai"
    assert extract_call["translate_model"] == "translate-model"
