"""Command-line interface for Bugster."""

import asyncio
from typing import Optional

import click
import typer
from rich.console import Console
from rich.text import Text

from bugster.utils.onboarding import print_welcome

app = typer.Typer(
    name="bugster",
    add_completion=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()

# Main command that shows onboarding flow
@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """🦑 Bugster CLI"""
    # Show quickstart guide if no command or help requested
    if not ctx.invoked_subcommand:
        print_welcome(show_full_help=False)
    elif ctx.get_help():
        # Let typer show its help first
        ctx.get_help()
        # Then show our quickstart guide
        print_welcome(show_full_help=True)

@app.command()
def auth():
    """[bold blue]Authenticate[/bold blue] to Bugster by setting up your API key."""
    from bugster.commands.auth import auth_command
    auth_command()


@app.command()
def init():
    """[bold green]Initialize[/bold green] Bugster CLI configuration in your project."""
    from bugster.commands.init import init_command

    init_command()


@app.command()
def test(
    path: Optional[str] = typer.Argument(None, help="Path to test file or directory"),
    headless: Optional[bool] = typer.Option(
        False, "--headless", help="Run tests in headless mode"
    ),
    silent: Optional[bool] = typer.Option(
        False, "--silent", "-s", help="Run in silent mode (less verbose output)"
    ),
    stream_results: Optional[bool] = typer.Option(
        False, "--stream-results", help="Stream test results as they complete"
    ),
    output: Optional[str] = typer.Option(
        None, "--output", help="Save test results to JSON file"
    ),
    run_id: Optional[str] = typer.Option(
        None, "--run-id", help="Run ID to associate with the test run"
    ),
):
    """[bold yellow]Run[/bold yellow] Bugster tests.

    If no path is provided, runs all tests in .bugster/tests.
    """
    from bugster.commands.test import test_command

    asyncio.run(test_command(path, headless, silent, stream_results, output, run_id))


@app.command()
def analyze(
    show_logs: bool = typer.Option(
        False,
        "--show-logs",
        help="Show detailed logs during analysis",
    ),
    force: bool = typer.Option(
        False,
        "-f",
        "--force",
        help="Force analysis even if the codebase has already been analyzed",
    ),
):
    """[bold magenta]Analyze[/bold magenta] your codebase to generate test specs."""
    from bugster.commands.analyze import analyze_command

    analyze_command(options={"show_logs": show_logs, "force": force})


@app.command()
def update(
    update_only: bool = typer.Option(
        False, help="Only update existing specs, no suggestions or deletes"
    ),
    suggest_only: bool = typer.Option(
        False, help="Only suggest new specs, no updates or deletes"
    ),
    delete_only: bool = typer.Option(
        False, help="Only delete specs, no updates or suggestions"
    ),
):
    """[bold magenta]Update[/bold magenta] your codebase with the latest changes."""
    from bugster.commands.update import update_command

    update_command(
        update_only=update_only, suggest_only=suggest_only, delete_only=delete_only
    )


@app.command()
def sync(
    branch: Optional[str] = typer.Option(
        None, help="Branch to sync with (defaults to current git branch or 'main')"
    ),
    pull: bool = typer.Option(False, help="Only pull specs from remote"),
    push: bool = typer.Option(False, help="Only push specs to remote"),
    clean_remote: bool = typer.Option(
        False, help="Delete remote specs that don't exist locally"
    ),
    dry_run: bool = typer.Option(
        False, help="Show what would happen without making changes"
    ),
    prefer: str = typer.Option(
        None,
        "--prefer",
        help="Prefer 'local' or 'remote' when resolving conflicts",
        click_type=click.Choice(["local", "remote"]),
    ),
):
    """[bold magenta]Sync[/bold magenta] your codebase with Bugster."""
    from bugster.commands.sync import sync_command

    sync_command(branch, pull, push, clean_remote, dry_run, prefer)


def main():
    app()


if __name__ == "__main__":
    main()
