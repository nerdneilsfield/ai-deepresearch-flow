from pathlib import Path

import click
import pytest

from deepresearch_flow.paper.extract import load_existing


def test_corrupt_existing_output_is_an_error_not_an_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "simple.json"
    path.write_text('{"papers": [{"source_path": "a.md"', encoding="utf-8")
    with pytest.raises(click.ClickException):
        load_existing(path)


def test_valid_existing_output_is_loaded(tmp_path: Path) -> None:
    path = tmp_path / "simple.json"
    path.write_text('{"papers": [{"source_path": "a.md"}]}', encoding="utf-8")
    assert load_existing(path) == [{"source_path": "a.md"}]
