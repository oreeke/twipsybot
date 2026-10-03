from importlib.metadata import version as package_version

import click
from dotenv import load_dotenv

from ..shared.config import Config
from ..shared.exceptions import ConfigurationError
from . import main as app_main


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.pass_context
def app(ctx: click.Context) -> None:
    load_dotenv()
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@app.command("run")
def run() -> int:
    return app_main.main()


@app.command("cfg")
def cfg() -> None:
    from ..tui import run as run_tui

    run_tui(Config())


@app.command("config-check")
def config_check() -> None:
    Config().load()
    click.echo("configuration valid")


@app.command("version")
def version() -> None:
    click.echo(package_version("twipsybot"))


def main() -> int:
    try:
        result = app.main(prog_name="twipsybot", standalone_mode=False)
        return result if isinstance(result, int) else 0
    except click.exceptions.Exit as e:
        return int(e.exit_code)
    except ConfigurationError as e:
        click.echo(f"Configuration error: {e}", err=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
