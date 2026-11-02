import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path("/root/Database-Bilibili")
ENV_FILE = ROOT / ".env"
MIGRATION = ROOT / "migrations/001_initial.sql"


def main() -> int:
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE, override=True)

    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        print("ERROR: DATABASE_URL chưa được cấu hình")
        return 1

    if not MIGRATION.is_file():
        print("ERROR: Không tìm thấy migration SQL")
        return 1

    print("===== CONNECT NEON SINGAPORE =====", flush=True)

    try:
        with psycopg.connect(
            database_url,
            connect_timeout=15,
            autocommit=True,
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT current_database(), current_user"
                )
                database, username = cursor.fetchone()

                print("DATABASE:", database)
                print("USER:", username)
                print("CONNECTION: OK")

                print(
                    "\n===== APPLY MIGRATION =====",
                    flush=True,
                )

                sql = MIGRATION.read_text(encoding="utf-8")
                cursor.execute(sql)

                cursor.execute("""
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = 'public'
                      AND tablename IN (
                        'bilibili_channels',
                        'bilibili_videos'
                      )
                    ORDER BY tablename
                """)

                tables = [
                    row[0] for row in cursor.fetchall()
                ]

                for table in tables:
                    print("OK:", table)

                if len(tables) != 2:
                    print("ERROR: Database schema incomplete")
                    return 1

                cursor.execute(
                    "SELECT COUNT(*) FROM bilibili_videos"
                )

                print(
                    "\nCURRENT VIDEOS:",
                    cursor.fetchone()[0],
                )

        print("\nNEON SINGAPORE READY")
        return 0

    except psycopg.Error as exc:
        print("NEON ERROR:", type(exc).__name__)
        print("SQLSTATE:", exc.sqlstate or "N/A")
        return 1


if __name__ == "__main__":
    sys.exit(main())
