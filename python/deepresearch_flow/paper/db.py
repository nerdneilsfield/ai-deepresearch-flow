"""Database management commands for paper extraction outputs."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Iterable
import difflib

import click
import httpx

from deepresearch_flow.paper.config import load_config, resolve_api_keys
from deepresearch_flow.paper.extract import parse_model_ref
from deepresearch_flow.paper.llm import backoff_delay, call_provider
from deepresearch_flow.paper.providers.base import ProviderError
from deepresearch_flow.paper.template_registry import list_template_names
from deepresearch_flow.paper.render import resolve_render_template, render_papers

try:
    from pybtex.database import parse_file
    PYBTEX_AVAILABLE = True
except ImportError:
    PYBTEX_AVAILABLE = False


def load_json(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_authors(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(value)]


def parse_publication_year(paper: dict[str, Any]) -> int | None:
    if "bibtex" in paper and isinstance(paper["bibtex"], dict):
        year_str = paper["bibtex"].get("fields", {}).get("year")
        if year_str and str(year_str).isdigit():
            return int(year_str)
    date_str = paper.get("publication_date") or paper.get("paper_publication_date")
    if not date_str:
        return None
    match = re.search(r"(19|20)\d{2}", str(date_str))
    return int(match.group(0)) if match else None


def similar_title(a: str, b: str, threshold: float = 0.9) -> bool:
    if not a or not b:
        return False
    ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return ratio >= threshold


async def generate_tags_for_paper(
    client: httpx.AsyncClient,
    provider,
    model: str,
    api_key: str | None,
    paper: dict[str, Any],
    max_retries: int,
    backoff_base: float,
    backoff_max: float,
) -> list[str]:
    system_prompt = (
        "You are a scientific paper tagging assistant. "
        "Return ONLY a JSON array of up to 5 tags. "
        "Each tag should be 1-3 words, lowercase, and use underscores."
    )
    payload = {
        "title": paper.get("paper_title"),
        "authors": normalize_authors(paper.get("paper_authors")),
        "abstract": paper.get("abstract") or paper.get("summary") or "",
        "keywords": paper.get("keywords") or [],
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    attempt = 0
    while attempt < max_retries:
        attempt += 1
        try:
            response_text = await call_provider(
                provider,
                model,
                messages,
                schema={},
                api_key=api_key,
                timeout=60.0,
                structured_mode="none",
                client=client,
            )
            tags = parse_tag_list(response_text)
            if isinstance(tags, list):
                return [str(tag) for tag in tags][:5]
            raise ProviderError("Tag response is not a list", error_type="validation_error")
        except ProviderError as exc:
            if attempt < max_retries:
                await asyncio.sleep(backoff_delay(backoff_base, attempt, backoff_max))
                continue
            raise
        except Exception as exc:
            if attempt < max_retries:
                await asyncio.sleep(backoff_delay(backoff_base, attempt, backoff_max))
                continue
            raise ProviderError(str(exc), error_type="parse_error") from exc

    raise ProviderError("Max retries exceeded")


def parse_tag_list(text: str) -> list[str]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\\[[\\s\\S]*\\]", text)
        if not match:
            raise ProviderError("No JSON array found", error_type="parse_error")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, list):
        raise ProviderError("Tag response is not a list", error_type="validation_error")
    return [str(item) for item in parsed]


    def register_db_commands(db_group: click.Group) -> None:
    @db_group.command("append-bibtex")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-b", "--bibtex", "bibtex_path", required=True, help="Input BibTeX file path")
    @click.option("-o", "--output", "output_path", required=True, help="Output JSON file path")
    def append_bibtex(input_path: str, bibtex_path: str, output_path: str) -> None:
        if not PYBTEX_AVAILABLE:
            raise click.ClickException("pybtex is required for append-bibtex")

        papers = load_json(Path(input_path))
        bib_data = parse_file(bibtex_path)
        bib_entries = []
        for key, entry in bib_data.entries.items():
            bib_entries.append(
                {
                    "key": key,
                    "type": entry.type,
                    "fields": dict(entry.fields),
                    "persons": {role: [str(p) for p in persons] for role, persons in entry.persons.items()},
                }
            )

        appended = []
        for paper in papers:
            title = paper.get("paper_title") or ""
            matched = False
            for bib in bib_entries:
                bib_title = bib.get("fields", {}).get("title", "")
                if similar_title(title, bib_title):
                    paper["bibtex"] = bib
                    matched = True
                    break
            if matched:
                appended.append(paper)
        write_json(Path(output_path), appended)
        click.echo(f"Appended bibtex for {len(appended)} papers")

    @db_group.command("sort-papers")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-o", "--output", "output_path", required=True, help="Output JSON file path")
    @click.option("--order", type=click.Choice(["asc", "desc"]), default="desc")
    def sort_papers(input_path: str, output_path: str, order: str) -> None:
        papers = load_json(Path(input_path))
        reverse = order == "desc"
        papers.sort(key=lambda p: parse_publication_year(p) or 0, reverse=reverse)
        write_json(Path(output_path), papers)
        click.echo(f"Sorted {len(papers)} papers")

    @db_group.command("split-by-tag")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-d", "--output-dir", "output_dir", required=True, help="Output directory")
    def split_by_tag(input_path: str, output_dir: str) -> None:
        papers = load_json(Path(input_path))
        tag_map: dict[str, list[dict[str, Any]]] = {}
        for paper in papers:
            tags = paper.get("ai_generated_tags") or []
            for tag in tags:
                tag_map.setdefault(tag, []).append(paper)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for tag, items in tag_map.items():
            write_json(out_dir / f"{tag}.json", items)
        write_json(out_dir / "index.json", {"tags": sorted(tag_map.keys())})
        click.echo(f"Split into {len(tag_map)} tag files")

    @db_group.command("split-database")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-d", "--output-dir", "output_dir", required=True, help="Output directory")
    @click.option(
        "-c",
        "--criteria",
        type=click.Choice(["year", "alphabetical", "count"]),
        default="count",
    )
    @click.option("-n", "--count", "chunk_count", default=100, help="Chunk size for count criteria")
    def split_database(input_path: str, output_dir: str, criteria: str, chunk_count: int) -> None:
        papers = load_json(Path(input_path))
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if criteria == "year":
            by_year: dict[str, list[dict[str, Any]]] = {}
            for paper in papers:
                year = parse_publication_year(paper)
                key = str(year) if year else "unknown"
                by_year.setdefault(key, []).append(paper)
            for year, items in by_year.items():
                write_json(out_dir / f"year_{year}.json", items)
            click.echo(f"Split into {len(by_year)} year files")
            return

        if criteria == "alphabetical":
            by_letter: dict[str, list[dict[str, Any]]] = {}
            for paper in papers:
                title = (paper.get("paper_title") or "").strip()
                letter = title[:1].upper() if title else "#"
                by_letter.setdefault(letter, []).append(paper)
            for letter, items in by_letter.items():
                write_json(out_dir / f"{letter}.json", items)
            click.echo(f"Split into {len(by_letter)} letter files")
            return

        chunks = [papers[i : i + chunk_count] for i in range(0, len(papers), chunk_count)]
        for idx, chunk in enumerate(chunks, start=1):
            write_json(out_dir / f"chunk_{idx}.json", chunk)
        click.echo(f"Split into {len(chunks)} chunks")

    @db_group.command("statistics")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    def statistics(input_path: str) -> None:
        papers = load_json(Path(input_path))
        year_counts: dict[int | None, int] = {}
        author_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        for paper in papers:
            year = parse_publication_year(paper)
            year_counts[year] = year_counts.get(year, 0) + 1
            for author in normalize_authors(paper.get("paper_authors")):
                author_counts[author] = author_counts.get(author, 0) + 1
            for tag in paper.get("ai_generated_tags") or []:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        click.echo(f"Total papers: {len(papers)}")
        click.echo(f"Years: {len([y for y in year_counts if y])}")
        if author_counts:
            top_authors = sorted(author_counts.items(), key=lambda item: item[1], reverse=True)[:10]
            click.echo("Top authors:")
            for author, count in top_authors:
                click.echo(f"  {author}: {count}")
        if tag_counts:
            top_tags = sorted(tag_counts.items(), key=lambda item: item[1], reverse=True)[:10]
            click.echo("Top tags:")
            for tag, count in top_tags:
                click.echo(f"  {tag}: {count}")

    @db_group.command("generate-tags")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-o", "--output", "output_path", required=True, help="Output JSON file path")
    @click.option("-c", "--config", "config_path", default="config.toml", help="Path to config.toml")
    @click.option("-m", "--model", "model_ref", required=True, help="provider/model")
    @click.option("-w", "--workers", "workers", default=4, type=int, help="Concurrent workers")
    def generate_tags(input_path: str, output_path: str, config_path: str, model_ref: str, workers: int) -> None:
        async def _run() -> None:
            config = load_config(config_path)
            provider, model_name = parse_model_ref(model_ref, config.providers)
            keys = resolve_api_keys(provider.api_keys)
            if provider.type in {
                "openai_compatible",
                "dashscope",
                "gemini_ai_studio",
                "azure_openai",
                "claude",
            } and not keys:
                raise click.ClickException(f"{provider.type} providers require api_keys")

            papers = load_json(Path(input_path))
            semaphore = asyncio.Semaphore(workers)
            key_idx = 0

            async with httpx.AsyncClient() as client:
                async def process_one(paper: dict[str, Any]) -> None:
                    nonlocal key_idx
                    async with semaphore:
                        api_key = None
                        if keys:
                            api_key = keys[key_idx % len(keys)]
                            key_idx += 1
                        tags = await generate_tags_for_paper(
                            client,
                            provider,
                            model_name,
                            api_key,
                            paper,
                            max_retries=config.extract.max_retries,
                            backoff_base=config.extract.backoff_base_seconds,
                            backoff_max=config.extract.backoff_max_seconds,
                        )
                        paper["ai_generated_tags"] = tags

                await asyncio.gather(*(process_one(paper) for paper in papers))

            write_json(Path(output_path), papers)
            click.echo(f"Generated tags for {len(papers)} papers")

        asyncio.run(_run())

    @db_group.command("filter")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-o", "--output", "output_path", required=True, help="Output JSON file path")
    @click.option("-t", "--tags", default=None, help="Comma-separated tags")
    @click.option("-y", "--years", default=None, help="Year range (e.g. 2018-2024, -2019, 2020-)")
    @click.option("-a", "--authors", default=None, help="Comma-separated author names")
    @click.option("-l", "--limit", default=None, type=int, help="Limit results")
    @click.option("-r", "--order", type=click.Choice(["asc", "desc"]), default="desc")
    def filter_papers(
        input_path: str,
        output_path: str,
        tags: str | None,
        years: str | None,
        authors: str | None,
        limit: int | None,
        order: str,
    ) -> None:
        papers = load_json(Path(input_path))
        tag_set = {tag.strip() for tag in tags.split(",")} if tags else set()
        author_set = {a.strip() for a in authors.split(",")} if authors else set()

        def year_match(paper: dict[str, Any]) -> bool:
            if not years:
                return True
            year = parse_publication_year(paper)
            if year is None:
                return False
            if years.startswith("-"):
                return year <= int(years[1:])
            if years.endswith("-"):
                return year >= int(years[:-1])
            if "-" in years:
                start, end = years.split("-", 1)
                return int(start) <= year <= int(end)
            return year == int(years)

        filtered = []
        for paper in papers:
            if tag_set:
                paper_tags = set(paper.get("ai_generated_tags") or [])
                if not paper_tags.intersection(tag_set):
                    continue
            if author_set:
                paper_authors = set(normalize_authors(paper.get("paper_authors")))
                if not paper_authors.intersection(author_set):
                    continue
            if not year_match(paper):
                continue
            filtered.append(paper)

        filtered.sort(key=lambda p: parse_publication_year(p) or 0, reverse=(order == "desc"))
        if limit:
            filtered = filtered[:limit]
        write_json(Path(output_path), filtered)
        click.echo(f"Filtered down to {len(filtered)} papers")

    @db_group.command("merge")
    @click.option("-i", "--inputs", "input_paths", multiple=True, required=True, help="Input JSON files")
    @click.option("-o", "--output", "output_path", required=True, help="Output JSON file path")
    def merge_papers(input_paths: Iterable[str], output_path: str) -> None:
        merged: list[dict[str, Any]] = []
        for path in input_paths:
            merged.extend(load_json(Path(path)))
        write_json(Path(output_path), merged)
        click.echo(f"Merged {len(input_paths)} files into {output_path}")

    @db_group.command("render-md")
    @click.option("-i", "--input", "input_path", required=True, help="Input JSON file path")
    @click.option("-d", "--output-dir", "output_dir", default="rendered_md", help="Output directory")
    @click.option(
        "-t",
        "--markdown-template",
        "--template",
        "template_path",
        default=None,
        help="Jinja2 template path",
    )
    @click.option(
        "-n",
        "--template-name",
        "template_name",
        default=None,
        type=click.Choice(list_template_names()),
        help="Built-in template name",
    )
    @click.option(
        "-T",
        "--template-dir",
        "template_dir",
        default=None,
        help="Directory containing render.j2",
    )
    @click.option(
        "-l",
        "--language",
        "output_language",
        default="en",
        show_default=True,
        help="Fallback output language for rendering",
    )
    def render_md(
        input_path: str,
        output_dir: str,
        template_path: str | None,
        template_name: str | None,
        template_dir: str | None,
        output_language: str,
    ) -> None:
        papers = load_json(Path(input_path))
        out_dir = Path(output_dir)
        try:
            template = resolve_render_template(template_path, template_name, template_dir)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        rendered = render_papers(papers, out_dir, template, output_language)
        click.echo(f"Rendered {rendered} markdown files")
