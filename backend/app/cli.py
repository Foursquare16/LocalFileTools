"""Owner commands, run inside the web container: `python -m app.cli <command>`.

create-owner       create the single owner, or reset the password (signs out the owner's sessions)
invite NAME        print a guest link that logs NAME in without a password until it expires
invites            list the guest links that still work
revoke NAME        cancel NAME's guest links at once
"""

import argparse
import getpass
import sys
import time

from . import auth, db

MIN_PASSWORD = 12
MAX_INVITE_HOURS = 24 * 30


def create_owner() -> None:
    username = input("Owner username: ").strip()
    password = getpass.getpass(f"Password ({MIN_PASSWORD}+ characters): ")
    if not username or len(password) < MIN_PASSWORD:
        sys.exit(f"A username and a password of at least {MIN_PASSWORD} characters are required.")
    if getpass.getpass("Repeat password: ") != password:
        sys.exit("Passwords do not match.")
    with db.state() as c:
        c.execute(
            "INSERT OR REPLACE INTO owner (id, username, password_hash) VALUES (1, ?, ?)",
            (username, auth.hash_password(password)),
        )
        c.execute("DELETE FROM sessions")
    print("Owner saved. All password logins were signed out; guest links still work (see `invites`).")


def invite(name: str, hours: float, url: str) -> None:
    if not 0 < hours <= MAX_INVITE_HOURS:
        sys.exit(f"--hours must be more than 0 and at most {MAX_INVITE_HOURS}.")
    token = auth.create_invite(name, hours)
    print(f"Guest link for {name}, valid for {hours:g} hours. Anyone who has it can use the app, so send it privately:")
    print(f"{url.rstrip('/')}/#invite={token}")
    if not url:
        print("Put your site's address in front of it, for example https://files.example.com/#invite=...")


def invites() -> None:
    now = time.time()
    with db.state() as c:
        rows = c.execute("SELECT name, expires FROM invites WHERE expires > ? ORDER BY expires", (now,)).fetchall()
    for row in rows:
        print(f"{row['name']}: {(row['expires'] - now) / 3600:.1f} hours left")
    if not rows:
        print("No guest links are active.")


def revoke(name: str) -> None:
    with db.state() as c:
        count = c.execute("DELETE FROM invites WHERE name = ?", (name,)).rowcount
    print(f"Cancelled {count} guest link(s) for {name}." if count else f"No guest link is named {name}.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create-owner", help="create the owner, or reset the password")
    p = commands.add_parser("invite", help="print a guest link")
    p.add_argument("name", help="who the link is for, to list and revoke it later")
    p.add_argument("--hours", type=float, default=48, help="how long the link works (default 48)")
    p.add_argument("--url", default="", help="your site's address, e.g. https://files.example.com")
    commands.add_parser("invites", help="list active guest links")
    commands.add_parser("revoke", help="cancel a guest's links").add_argument("name")
    args = parser.parse_args(argv)
    if args.command == "create-owner":
        create_owner()
    elif args.command == "invite":
        invite(args.name, args.hours, args.url)
    elif args.command == "invites":
        invites()
    else:
        revoke(args.name)


if __name__ == "__main__":
    main()
