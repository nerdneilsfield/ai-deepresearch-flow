"""CLI entrypoint for deepresearch-flow."""

import click

from deepresearch_flow.paper.cli import paper
from deepresearch_flow.recognize.cli import recognize


@click.group()
def cli() -> None:
    """DeepResearch Flow command line interface."""


cli.add_command(paper)
cli.add_command(recognize)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
