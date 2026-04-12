from __future__ import annotations

from types import SimpleNamespace

from importlib import metadata

from deepresearch_flow.paper.web import templates


def test_get_jinja_env_falls_back_to_filesystem_loader(monkeypatch) -> None:
    def boom_package_loader(*args, **kwargs):  # noqa: ANN001, ARG001
        raise RuntimeError("missing package metadata")

    monkeypatch.setattr("deepresearch_flow.paper.web.templates.PackageLoader", boom_package_loader)
    env = templates.get_jinja_env()
    assert env.loader.__class__.__name__ == "FileSystemLoader"


def test_get_jinja_env_uses_package_loader_when_available(monkeypatch) -> None:
    class _FakeLoader:
        pass

    class _FakeEnv:
        def __init__(self, *, loader, autoescape):  # noqa: ANN001
            self.loader = loader
            self.autoescape = autoescape

    monkeypatch.setattr("deepresearch_flow.paper.web.templates.PackageLoader", lambda *args, **kwargs: _FakeLoader())
    monkeypatch.setattr("deepresearch_flow.paper.web.templates.Environment", _FakeEnv)

    env = templates.get_jinja_env()
    assert isinstance(env.loader, _FakeLoader)
    assert env.autoescape is True


def test_get_template_env_caches_singleton(monkeypatch) -> None:
    templates._jinja_env = None
    calls: list[str] = []
    fake_env = object()

    def fake_get_jinja_env():
        calls.append("called")
        return fake_env

    monkeypatch.setattr("deepresearch_flow.paper.web.templates.get_jinja_env", fake_get_jinja_env)

    assert templates.get_template_env() is fake_env
    assert templates.get_template_env() is fake_env
    assert calls == ["called"]

    templates._jinja_env = None


def test_render_template_sets_defaults_and_version_fallbacks(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class _Template:
        def render(self, **context):
            seen.update(context)
            return "rendered"

    class _Env:
        def get_template(self, name: str):
            seen["template_name"] = name
            return _Template()

    monkeypatch.setattr("deepresearch_flow.paper.web.templates.get_template_env", lambda: _Env())
    monkeypatch.setattr("deepresearch_flow.paper.web.templates.metadata.version", lambda _: "9.9.9")

    assert templates.render_template("detail.html", title="Paper") == "rendered"
    assert seen["template_name"] == "detail.html"
    assert seen["title"] == "Paper"
    assert seen["app_version"] == "9.9.9"
    assert seen["repo_url"] == templates.REPO_URL

    def missing_version(_: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr("deepresearch_flow.paper.web.templates.metadata.version", missing_version)
    seen.clear()
    assert templates.render_template("detail.html", title="Paper") == "rendered"
    assert seen["app_version"] == templates.__version__


def test_build_pdfjs_viewer_url_handles_optional_cdn() -> None:
    assert templates.build_pdfjs_viewer_url("/pdf/file.pdf") == "/pdfjs/web/viewer.html?file=%2Fpdf%2Ffile.pdf&allow_origin=1"
    assert (
        templates.build_pdfjs_viewer_url("/pdf/file.pdf", cdn_base_url="https://cdn.example.com/")
        == "/pdfjs/web/viewer.html?file=%2Fpdf%2Ffile.pdf&allow_origin=1&cdn=https%3A%2F%2Fcdn.example.com"
    )
