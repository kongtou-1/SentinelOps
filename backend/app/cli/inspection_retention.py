"""Preview or soft-delete expired trial runs."""

import argparse

from app.db.session import SessionLocal
from app.services.inspections.retention import clean_trials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        for identifier in clean_trials(db, args.days, args.apply):
            print(identifier)


if __name__ == "__main__":
    main()
