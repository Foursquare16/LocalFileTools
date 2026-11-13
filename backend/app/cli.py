"""`python -m app.cli create-owner`: create the single owner, or reset the password (signs out all sessions)."""

import getpass
import sys

from . import auth, db

MIN_PASSWORD = 12


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
    print("Owner saved. All existing sessions were signed out.")


if __name__ == "__main__":
    if sys.argv[1:] != ["create-owner"]:
        sys.exit("usage: python -m app.cli create-owner")
    create_owner()
