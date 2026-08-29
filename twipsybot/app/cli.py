import atexit
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import click
import psutil

from ..shared.exceptions import ConfigurationError
from ..shared.utils import format_duration_hms
from . import main as app_main
from .main import _fatal_file_path, _stop_file_path


def _pid_file_path() -> Path:
    return Path("run") / "twipsybot.pid"


def _remove_stop_file(stop_file: Path) -> None:
    try:
        stop_file.unlink(missing_ok=True)
    except OSError:
        return


def _write_stop_file(stop_file: Path) -> None:
    try:
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        return


def _read_pid_record(pid_file: Path) -> tuple[int, float | None] | None:
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    create_time = data.get("create_time")
    if (
        not isinstance(pid, int)
        or isinstance(create_time, bool)
        or not isinstance(create_time, (int, float))
    ):
        return None
    return pid, float(create_time)


def _read_pid(pid_file: Path) -> int | None:
    record = _read_pid_record(pid_file)
    return record[0] if record else None


def _write_pid_file(pid_file: Path, proc: psutil.Process) -> None:
    pid_file.write_text(
        json.dumps({"pid": proc.pid, "create_time": proc.create_time()}),
        encoding="utf-8",
    )


def _get_bot_process(pid_file: Path) -> psutil.Process | None:
    record = _read_pid_record(pid_file)
    if record is None:
        return None
    pid, expected_create_time = record
    try:
        proc = psutil.Process(pid)
        if (
            expected_create_time is not None
            and proc.create_time() != expected_create_time
        ) or not proc.is_running():
            return None
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    return proc


def _remove_pid_file(pid_file: Path, *, expected_pid: int | None = None) -> None:
    try:
        if expected_pid is not None:
            current = _read_pid(pid_file)
            if current != expected_pid:
                return
        pid_file.unlink(missing_ok=True)
    except OSError:
        return


def _should_daemonize() -> bool:
    if os.environ.get("TWIPSYBOT_UP_CHILD") == "1":
        return False
    return os.environ.get("TWIPSYBOT_UP_MODE") != "foreground"


def _spawn_detached(argv: list[str], *, env: dict[str, str]) -> subprocess.Popen:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": os.getcwd(),
        "env": env,
    }
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
        kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def _run_up_foreground(pid_file: Path) -> int:
    pid = os.getpid()
    _write_pid_file(pid_file, psutil.Process(pid))
    atexit.register(_remove_pid_file, pid_file, expected_pid=pid)
    try:
        return app_main.main()
    except ConfigurationError as e:
        print(f"Startup error: {e}", file=sys.stderr)
        return 2
    finally:
        _remove_pid_file(pid_file, expected_pid=pid)


def _run_up_daemon(pid_file: Path) -> int:
    fatal_file = _fatal_file_path()
    env = dict(os.environ)
    env["TWIPSYBOT_UP_CHILD"] = "1"
    proc = _spawn_detached([sys.executable, "-m", "twipsybot.app.cli", "up"], env=env)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if proc.poll() is not None:
            print(
                f"twipsybot exited immediately (code={proc.returncode}), check logs",
                file=sys.stderr,
            )
            return 1
        if fatal_file.exists():
            print(
                "twipsybot failed to start (fatal startup error), check logs",
                file=sys.stderr,
            )
            return 1
        running = _get_bot_process(pid_file)
        if running is not None and running.pid == proc.pid:
            return 0
        time.sleep(0.05)
    print("failed to start twipsybot", file=sys.stderr)
    return 1


def _cmd_up() -> int:
    pid_file = _pid_file_path()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    _remove_stop_file(_stop_file_path())
    _remove_stop_file(_fatal_file_path())
    if pid_file.exists():
        proc = _get_bot_process(pid_file)
        if proc is not None and proc.pid != os.getpid():
            print(f"twipsybot is already running (pid={proc.pid})", file=sys.stderr)
            return 2
        _remove_pid_file(pid_file)

    if _should_daemonize():
        code = _run_up_daemon(pid_file)
        if code == 0:
            pid = _read_pid(pid_file)
            pid_text = str(pid) if pid is not None else "unknown"
            print(
                f"twipsybot started (pid={pid_text})\n"
                f"pid_file={pid_file}\n"
                "next:\n"
                "  twipsybot status\n"
                "  twipsybot restart\n"
                "  twipsybot down",
                file=sys.stdout,
            )
        return code
    print(
        f"twipsybot running (pid={os.getpid()})\npid_file={pid_file}",
        file=sys.stdout,
    )
    return _run_up_foreground(pid_file)


def _stop_process(pid_file: Path, proc: psutil.Process) -> None:
    stop_file = _stop_file_path()
    _write_stop_file(stop_file)
    try:
        proc.wait(timeout=5)
        _remove_stop_file(stop_file)
        _remove_pid_file(pid_file)
        return
    except psutil.TimeoutExpired:
        pass

    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
    except psutil.NoSuchProcess:
        return
    finally:
        _remove_stop_file(stop_file)
        _remove_pid_file(pid_file)


def _cmd_down() -> int:
    pid_file = _pid_file_path()
    if not pid_file.exists():
        print("twipsybot is not running", file=sys.stderr)
        return 2

    proc = _get_bot_process(pid_file)
    if proc is None:
        _remove_pid_file(pid_file)
        print("twipsybot is not running", file=sys.stderr)
        return 2

    pid = proc.pid
    try:
        _stop_process(pid_file, proc)
        print(f"twipsybot stopped (pid={pid})", file=sys.stdout)
        return 0
    except psutil.NoSuchProcess:
        _remove_pid_file(pid_file)
        print("twipsybot stopped", file=sys.stdout)
        return 0
    except Exception as e:
        print(f"failed to stop twipsybot: {e}", file=sys.stderr)
        return 1


def _cmd_restart() -> int:
    pid_file = _pid_file_path()
    print("twipsybot restarting...", file=sys.stdout)
    if pid_file.exists():
        proc = _get_bot_process(pid_file)
        if proc is not None:
            pid = proc.pid
            print(f"stopping twipsybot (pid={pid})...", file=sys.stdout)
            try:
                _stop_process(pid_file, proc)
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                print(f"failed to stop twipsybot: {e}", file=sys.stderr)
                return 1
            print(f"twipsybot stopped (pid={pid})", file=sys.stdout)
    else:
        print("twipsybot is not running; starting...", file=sys.stdout)
    return _cmd_up()


def _cmd_status() -> int:
    pid_file = _pid_file_path()
    if not pid_file.exists():
        print("stopped", file=sys.stdout)
        return 2

    proc = _get_bot_process(pid_file)
    if proc is None:
        _remove_pid_file(pid_file)
        print("stopped", file=sys.stdout)
        return 2

    pid = proc.pid
    is_tty = sys.stdout.isatty()
    try:
        while True:
            if not proc.is_running():
                print("stopped", file=sys.stdout)
                return 2
            try:
                mem = proc.memory_info().rss / (1024 * 1024)
                cpu = proc.cpu_percent(interval=None)
                uptime = format_duration_hms(time.time() - proc.create_time())
                line = (
                    f"running pid={pid} uptime={uptime} cpu={cpu:.1f}% rss={mem:.1f}MB"
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                print("stopped", file=sys.stdout)
                return 2

            if is_tty:
                sys.stdout.write("\r" + line + " " * 10)
                sys.stdout.flush()
            else:
                print(line, file=sys.stdout)

            time.sleep(1.0)
    except KeyboardInterrupt:
        if is_tty:
            sys.stdout.write("\n")
            sys.stdout.flush()
        return 130


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.pass_context
def app(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        raise click.exceptions.Exit(0)


@app.command()
def up() -> None:
    raise click.exceptions.Exit(_cmd_up())


@app.command()
def down() -> None:
    raise click.exceptions.Exit(_cmd_down())


@app.command()
def restart() -> None:
    raise click.exceptions.Exit(_cmd_restart())


@app.command()
def status() -> None:
    raise click.exceptions.Exit(_cmd_status())


@app.command()
@click.pass_context
def help(ctx: click.Context) -> None:
    click.echo(ctx.parent.get_help() if ctx.parent else ctx.get_help())
    raise click.exceptions.Exit(0)


def main() -> int:
    try:
        app.main(prog_name="twipsybot", standalone_mode=False)
        return 0
    except click.exceptions.Exit as e:
        return int(e.exit_code)
    except ConfigurationError as e:
        click.echo(f"Startup error: {e}", err=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
