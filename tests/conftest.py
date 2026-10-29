import pytest

from amlnet.generator import generate, make_notes
from amlnet.store import SqliteStore


@pytest.fixture(scope="session")
def data():
    return generate(n_accounts=6_000, n_tx=50_000, seed=3)


@pytest.fixture(scope="session")
def store(data):
    s = SqliteStore(":memory:")
    s.load(data[0], data[1], make_notes())
    return s
