"""Repeatable Compose bootstrap; runtime containers use non-superuser roles."""

import os

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from upraise_ai.storage.audit import AuditRepository
from upraise_ai.storage.jobs import JobRepository


def main():
    admin = make_conninfo(
        host=os.environ.get("PGHOST", "postgres"),
        dbname="postgres",
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
    )
    with psycopg.connect(admin, autocommit=True, connect_timeout=10) as conn:
        conn.execute("SELECT pg_advisory_lock(7824031)")
        try:
            for prefix, repository in (("JOBS", JobRepository), ("AUDIT", AuditRepository)):
                database = os.environ[f"{prefix}_DB"]
                role = os.environ[f"{prefix}_USER"]
                password = os.environ[f"{prefix}_PASSWORD"]
                if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                    conn.execute(
                        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                            sql.Identifier(role), sql.Literal(password)
                        )
                    )
                else:
                    conn.execute(
                        sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                            sql.Identifier(role), sql.Literal(password)
                        )
                    )
                if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone():
                    conn.execute(
                        sql.SQL("CREATE DATABASE {} OWNER {}").format(
                            sql.Identifier(database), sql.Identifier(role)
                        )
                    )
                # Existing scaffold audit DB may be owned by the bootstrap role.
                conn.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(database), sql.Identifier(role)
                    )
                )
                with psycopg.connect(make_conninfo(admin, dbname=database), autocommit=True) as target:
                    target.execute(
                        sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(sql.Identifier(role))
                    )
                repository(
                    make_conninfo(
                        host=os.environ.get("PGHOST", "postgres"),
                        dbname=database,
                        user=role,
                        password=password,
                    )
                ).migrate()
        finally:
            conn.execute("SELECT pg_advisory_unlock(7824031)")
    print("Ingestion job and audit schemas ready.")


if __name__ == "__main__":
    main()
