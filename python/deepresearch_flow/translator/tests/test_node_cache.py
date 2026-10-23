import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx

from deepresearch_flow.translator.node_cache import CACHE_DIR_NAME, NodeCache
from deepresearch_flow.translator.scheduler import DocStage, Scheduler
from deepresearch_flow.translator.tests.test_scheduler import _DummyTranslator, _make_queue_config

NODE = "<NODE_START_0000>\n你好\n</NODE_END_0000>\n"


class _Translator(_DummyTranslator):
    def __init__(self, reply: str, *, fail_finalize: bool = False) -> None:
        super().__init__({})
        self.cfg = SimpleNamespace(
            retry_group_max_chars=None, max_chunk_chars=4000, source_lang=None, target_lang="zh"
        )
        self.reply = reply
        self.fail_finalize = fail_finalize
        self.calls = 0

    def _group_nodes(self, nodes, only_ids=None, max_chunk_chars=None, include_translated=False):
        # Same contract as MarkdownTranslator: translated nodes are left out unless asked for.
        ids = sorted(only_ids if only_ids is not None else nodes)
        return [
            f"<NODE_START_{nid:04d}>\n{nodes[nid].origin_text}\n</NODE_END_{nid:04d}>\n"
            for nid in ids
            if include_translated or not nodes[nid].translated_text
        ]

    async def _translate_group(self, *args, **kwargs) -> str:
        self.calls += 1
        return self.reply

    async def finalize_document(self, *args, **kwargs) -> str:
        if self.fail_finalize:
            raise RuntimeError("interrupted before the output was written")
        return await super().finalize_document(*args, **kwargs)


def _run(tmp_path: Path, translator: _Translator, *, reuse: bool = True) -> list[Path]:
    source = tmp_path / "doc.md"
    source.write_text("hello", encoding="utf-8")
    output = tmp_path / "doc.zh.md"

    async def go() -> list[Path]:
        async with httpx.AsyncClient() as client:
            scheduler = Scheduler(
                translator=translator,  # type: ignore[arg-type]
                document_window=1,
                global_semaphore=asyncio.Semaphore(1),
                queue_configs=[_make_queue_config(DocStage.TRANSLATING)],
                progress=None,
                client=client,
                throttle=None,
                timeout=5.0,
                node_cache=True,
                reuse_node_cache=reuse,
            )
            return await asyncio.wait_for(
                scheduler.run(
                    paths=[source],
                    output_map={source: output},
                    fix_level="off",
                    format_enabled=False,
                ),
                timeout=5.0,
            )

    return asyncio.run(go())


def test_nodes_translated_before_an_interruption_are_not_translated_again(tmp_path: Path) -> None:
    first = _Translator(NODE, fail_finalize=True)
    assert _run(tmp_path, first) == [tmp_path / "doc.md"]
    assert first.calls == 1
    assert not (tmp_path / "doc.zh.md").exists()

    second = _Translator("")
    assert _run(tmp_path, second) == []
    assert second.calls == 0
    assert (tmp_path / "doc.zh.md").read_text(encoding="utf-8") == "你好"


def test_cache_is_removed_once_the_output_is_written(tmp_path: Path) -> None:
    assert _run(tmp_path, _Translator(NODE)) == []
    assert not (tmp_path / CACHE_DIR_NAME).exists()


def test_force_ignores_cached_nodes(tmp_path: Path) -> None:
    _run(tmp_path, _Translator(NODE, fail_finalize=True))

    forced = _Translator(NODE)
    assert _run(tmp_path, forced, reuse=False) == []
    assert forced.calls == 1


def test_cache_does_not_cross_target_languages(tmp_path: Path) -> None:
    out = tmp_path / "doc.zh.md"
    zh = NodeCache.for_output(out, None, "zh", load=False)
    zh.put("hello", "你好")
    zh.save()
    assert NodeCache.for_output(out, None, "zh", load=True).get("hello") == "你好"
    assert NodeCache.for_output(out, None, "ja", load=True).get("hello") is None
