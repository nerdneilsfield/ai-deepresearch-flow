"""CLI entrypoint for deepresearch-flow."""

import click

from deepresearch_flow.paper.cli import paper


@click.group()
def cli() -> None:
    """DeepResearch Flow command line interface."""


cli.add_command(paper)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
