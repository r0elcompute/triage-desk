from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from triage_sentinel.data.database import DatabaseSettings


@pytest.fixture(scope="session")
def migrated_test_engine() -> Iterator[Engine]:
    env_file = Path(__file__).resolve().parents[1] / "backend" / ".env"
    configured_url = DatabaseSettings(_env_file=env_file).test_database_url
    if not configured_url:
        pytest.fail(
            "TEST_DATABASE_URL must point to a dedicated PostgreSQL test database"
        )

    url = make_url(configured_url)
    if url.get_backend_name() != "postgresql" or url.get_driver_name() != "psycopg":
        pytest.fail("TEST_DATABASE_URL must use PostgreSQL with the psycopg driver")
    if "test" not in (url.database or "").lower():
        pytest.fail("TEST_DATABASE_URL database name must include 'test'")

    admin_engine = create_engine(url, pool_pre_ping=True)
    schema_name = f"triage_test_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    test_engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema_name}"},
    )
    try:
        alembic_config = Config(
            str(Path(__file__).resolve().parents[1] / "backend" / "alembic.ini")
        )
        with test_engine.connect() as connection:
            alembic_config.attributes["connection"] = connection
            command.upgrade(alembic_config, "head")
        yield test_engine
    finally:
        test_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def db_session(migrated_test_engine: Engine) -> Iterator[Session]:
    with migrated_test_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
