from collections.abc import Iterator
from contextlib import contextmanager

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


class DatabaseSettings(BaseSettings):
    database_url: str | None = None
    test_database_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or DatabaseSettings().database_url
    if not url:
        raise ValueError("DATABASE_URL must be set to use the database")
    return create_engine(url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory.begin() as session:
        yield session
