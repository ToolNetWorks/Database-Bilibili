import os
import sys
from pathlib import Path

import psycopg

ROOT = Path("/root/Database-Bilibili")
ENV_FILE = ROOT / "secrets/neon.env"
MIGRATION_FILE = ROOT / "migrations/001_initial.sql"


def load_database_url() -> str:
    if not ENV_FILE.is_file():
        raise RuntimeError("Neon config not found")

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.partition("=")[2].strip()

    raise RuntimeError("DATABASE_URL not found")


def main() -> int:
    if not MIGRATION_FILE.is_file():
        print("ERROR: Migration file not found")
        return 1

    try:
        database_url = load_database_url()
        sql = MIGRATION_FILE.read_text(encoding="utf-8")

        if not sql.strip():
            print("ERROR: Empty migration")
            return 1

        with psycopg.connect(
            database_url,
            connect_timeout=15,
            autocommit=True,
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = 'public'
                      AND tablename IN (
                        'bilibili_channels',
                        'bilibili_videos'
                      )
                """)

                existing = [row[0] for row in cursor.fetchall()]

                if existing:
                    print("SKIP: Bilibili tables already exist")
                    print("Existing:", ", ".join(existing))
                    return 0

                print("===== APPLY MIGRATION =====")
                cursor.execute(sql)

                cursor.execute("""
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = 'public'
                      AND tablename LIKE 'bilibili_%'
                    ORDER BY tablename
                """)

                tables = [row[0] for row in cursor.fetchall()]

                print("===== TABLES CREATED =====")
                for table in tables:
                    print("OK:", table)

                required = {
                    "bilibili_channels",
                    "bilibili_videos",
                }

                if not required.issubset(set(tables)):
                    print("ERROR: Missing required tables")
                    return 1

        print("NEON MIGRATION COMPLETED")
        return 0

    except (psycopg.Error, OSError, RuntimeError) as exc:
        print("ERROR:", type(exc).__name__)
        print("Migration failed; check configuration and logs")
        return 1


if __name__ == "__main__":
    sys.exit(main())
