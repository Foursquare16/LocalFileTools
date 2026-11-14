"""Owner commands, run inside the web container: `python -m app.cli <command>`.

close    show everyone a "service closed" page; jobs already in the queue still run and are cleaned up
open     open the service again
"""

import argparse

from . import auth


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("close", help="close the service to everyone")
    commands.add_parser("open", help="open the service again")
    args = parser.parse_args(argv)
    if args.command == "close":
        auth.closed_flag().touch()
        print("The service is closed.")
    else:
        auth.closed_flag().unlink(missing_ok=True)
        print("The service is open.")


if __name__ == "__main__":
    main()
