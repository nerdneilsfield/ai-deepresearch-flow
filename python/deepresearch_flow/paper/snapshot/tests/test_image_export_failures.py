from __future__ import annotations

import base64
import errno
import hashlib
from pathlib import Path
from unittest.mock import patch

from deepresearch_flow.paper.snapshot import builder, image_utils
from deepresearch_flow.paper.web.static_assets import (
    _ImageStore,
    _rewrite_markdown_images as rewrite_web_markdown_images,
)


def _failing_image_write_factory() -> object:
    original = Path.write_bytes

    def _write_bytes(self: Path, data: bytes) -> int:
        if self.parent.name == "images":
            raise OSError(errno.EIO, "Input/output error")
        return original(self, data)

    return _write_bytes


def _fail_once_image_write_factory() -> object:
    original = Path.write_bytes
    failed = False

    def _write_bytes(self: Path, data: bytes) -> int:
        nonlocal failed
        if self.parent.name == "images" and not failed:
            failed = True
            raise OSError(errno.EIO, "Input/output error")
        return original(self, data)

    return _write_bytes


def _expected_image_entry() -> dict[str, str]:
    digest = hashlib.sha256(b"img-bytes").hexdigest()
    return {
        "path": f"images/{digest}.png",
        "sha256": digest,
        "ext": "png",
        "status": "write_failed",
    }


def test_builder_rewrite_markdown_images_keeps_content_when_image_export_write_fails(
    tmp_path: Path,
) -> None:
    payload = base64.b64encode(b"img-bytes").decode("ascii")
    markdown = f"![plot](data:image/png;base64,{payload})"

    with patch.object(Path, "write_bytes", _failing_image_write_factory()):
        rewritten, images = builder._rewrite_markdown_images(
            markdown,
            source_path=tmp_path / "source.md",
            images_output_dir=tmp_path / "static" / "images",
            written=set(),
        )

    assert rewritten == markdown
    assert images == [_expected_image_entry()]
    assert list((tmp_path / "static" / "images").glob("*")) == []


def test_snapshot_image_utils_keeps_content_when_image_export_write_fails(tmp_path: Path) -> None:
    payload = base64.b64encode(b"img-bytes").decode("ascii")
    markdown = f"![plot](data:image/png;base64,{payload})"

    with patch.object(Path, "write_bytes", _failing_image_write_factory()):
        rewritten, images = image_utils.rewrite_markdown_images(
            markdown,
            source_path=tmp_path / "source.md",
            images_output_dir=tmp_path / "static" / "images",
            written=set(),
        )

    assert rewritten == markdown
    assert images == [_expected_image_entry()]
    assert list((tmp_path / "static" / "images").glob("*")) == []


def test_web_static_assets_keeps_data_url_when_image_export_write_fails(tmp_path: Path) -> None:
    payload = base64.b64encode(b"img-bytes").decode("ascii")
    markdown = f"![plot](data:image/png;base64,{payload})"
    store = _ImageStore(tmp_path / "static" / "images")

    with patch.object(Path, "write_bytes", _failing_image_write_factory()):
        rewritten = rewrite_web_markdown_images(markdown, store)

    assert rewritten == markdown
    assert list((tmp_path / "static" / "images").glob("*")) == []


def test_builder_rewrite_markdown_images_retries_transient_image_export_eio(tmp_path: Path) -> None:
    payload = base64.b64encode(b"img-bytes").decode("ascii")
    markdown = f"![plot](data:image/png;base64,{payload})"
    expected = _expected_image_entry()

    with (
        patch.object(Path, "write_bytes", _fail_once_image_write_factory()),
        patch("deepresearch_flow.paper.snapshot.builder.time.sleep"),
    ):
        rewritten, images = builder._rewrite_markdown_images(
            markdown,
            source_path=tmp_path / "source.md",
            images_output_dir=tmp_path / "static" / "images",
            written=set(),
        )

    assert rewritten == f"![plot]({expected['path']})"
    assert images == [{**expected, "status": "available"}]
    assert (
        tmp_path / "static" / "images" / Path(expected["path"]).name
    ).read_bytes() == b"img-bytes"


def test_snapshot_image_utils_retries_transient_image_export_eio(tmp_path: Path) -> None:
    payload = base64.b64encode(b"img-bytes").decode("ascii")
    markdown = f"![plot](data:image/png;base64,{payload})"
    expected = _expected_image_entry()

    with (
        patch.object(Path, "write_bytes", _fail_once_image_write_factory()),
        patch("deepresearch_flow.paper.snapshot.image_utils.time.sleep"),
    ):
        rewritten, images = image_utils.rewrite_markdown_images(
            markdown,
            source_path=tmp_path / "source.md",
            images_output_dir=tmp_path / "static" / "images",
            written=set(),
        )

    assert rewritten == f"![plot]({expected['path']})"
    assert images == [{**expected, "status": "available"}]
    assert (
        tmp_path / "static" / "images" / Path(expected["path"]).name
    ).read_bytes() == b"img-bytes"


def test_web_static_assets_retries_transient_image_export_eio(tmp_path: Path) -> None:
    payload = base64.b64encode(b"img-bytes").decode("ascii")
    markdown = f"![plot](data:image/png;base64,{payload})"
    expected = _expected_image_entry()
    store = _ImageStore(tmp_path / "static" / "images")

    with (
        patch.object(Path, "write_bytes", _fail_once_image_write_factory()),
        patch("deepresearch_flow.paper.web.static_assets.time.sleep"),
    ):
        rewritten = rewrite_web_markdown_images(markdown, store)

    assert rewritten == f"![plot]({expected['path']})"
    assert (
        tmp_path / "static" / "images" / Path(expected["path"]).name
    ).read_bytes() == b"img-bytes"
