from __future__ import annotations

from app.db.session import init_database_schema


def main() -> int:
    init_database_schema()
    print("Database ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
