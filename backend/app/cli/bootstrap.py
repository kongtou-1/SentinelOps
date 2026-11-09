import argparse
import getpass
import os

import app.repositories.cli_bootstrap as _repository
from app.db.session import SessionLocal
from app.schemas.auth import UserCreate


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first administrator")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    with SessionLocal() as db:
        role = _repository.main_query(db)
        if role is None or role.deleted_at is not None:
            raise SystemExit("Run migrations before creating an administrator")
        if _repository.main_query_2(db, role):
            raise SystemExit("An administrator already exists; manage users through the API")
        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
        body = UserCreate(username=args.username, password=password, role="admin")
        if _repository.main_query_3(body, db):
            raise SystemExit("Username is already reserved")
        _repository.main_query_4(body, db, role)
        db.commit()
    print(f"Administrator {args.username} created")


if __name__ == "__main__":
    main()
