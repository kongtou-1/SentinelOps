"""Import legacy AI environment settings once, without printing secrets."""

from app.db.session import SessionLocal
from app.services.agent_config import import_environment


def main():
    with SessionLocal() as db:
        modules = import_environment(db)
    print("Imported modules: " + (", ".join(modules) or "none (already configured)"))


if __name__ == "__main__":
    main()
