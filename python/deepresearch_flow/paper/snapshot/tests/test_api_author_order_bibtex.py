"""Black-box checks: papers added via admin keep author order and full BibTeX text."""

from __future__ import annotations

import tempfile
from pathlib import Path

from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.admin import create_admin_app
from deepresearch_flow.paper.snapshot.api import create_app
from deepresearch_flow.paper.snapshot.tests.test_admin import ADMIN_TOKEN, _make_db, _sample_paper

AUTHORS = ["Ji Wu", "Huai Yu", "Shu Han", "Gui-Song Xia"]
RAW = "@article{wu2026,\n  title = {QuadricsReg},\n  author = {Wu, Ji and Yu, Huai},\n  year = 2026\n}"


def test_detail_and_search_keep_author_order_and_bibtex_raw() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(Path(tmp))
        admin = TestClient(create_admin_app(snapshot_db=db, admin_token=ADMIN_TOKEN))
        paper = _sample_paper(
            paper_title="QuadricsReg",
            paper_authors=AUTHORS,
            bibtex={"type": "article", "key": "wu2026", "fields": {"title": "QuadricsReg"}, "raw": RAW},
        )
        resp = admin.post("/papers", json={"papers": [paper]}, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        paper_id = resp.json()["paper_ids"][0]

        api = TestClient(create_app(snapshot_db=db, static_base_url="", mcp_access_token="test-token"))
        detail = api.get(f"/api/v1/papers/{paper_id}").json()
        assert detail["authors"] == [a.lower() for a in AUTHORS]
        listed = api.get("/api/v1/search", params={"q": "QuadricsReg"}).json()["items"][0]
        assert listed["authors"] == [a.lower() for a in AUTHORS]
        assert api.get(f"/api/v1/papers/{paper_id}/bibtex").json()["bibtex_raw"] == RAW


def test_search_reports_match_scope() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(Path(tmp))
        admin = TestClient(create_admin_app(snapshot_db=db, admin_token=ADMIN_TOKEN))
        admin.post(
            "/papers",
            json={"papers": [_sample_paper(paper_title="Quadric registration", paper_authors=["A B"])]},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        api = TestClient(create_app(snapshot_db=db, static_base_url="", mcp_access_token="test-token"))
        assert api.get("/api/v1/search", params={"q": "quadric"}).json()["items"][0]["match_scope"] == "title"
        assert api.get("/api/v1/search", params={"q": "transformers"}).json()["items"][0]["match_scope"] == "summary"
