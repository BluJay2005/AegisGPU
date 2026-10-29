import pandas as pd
import pytest


def test_semantic_search_prefers_matching_pattern(store):
    top = store.search_notes("many deposits just under the $10,000 reporting threshold consolidated", k=3)
    assert top[0]["pattern"] == "structuring"


def test_query_connection_is_read_only(store):
    with pytest.raises(pd.errors.DatabaseError, match="readonly"):
        store.query("DELETE FROM transactions")
