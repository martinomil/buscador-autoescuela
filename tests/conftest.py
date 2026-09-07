from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import init_db


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    Session = sessionmaker(bind=engine, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
