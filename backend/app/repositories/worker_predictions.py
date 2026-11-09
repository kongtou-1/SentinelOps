"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import text


def main_query(owner):
    return owner.scalar(text("SELECT pg_try_advisory_lock(60006)"))


def main_query_2(owner):
    owner.execute(text("SELECT 1"))
