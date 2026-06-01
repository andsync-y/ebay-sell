import os
import tempfile

import pytest

from src.config import Config
from src.models import SourceItem


@pytest.fixture(scope="session", autouse=True)
def _isolated_home():
    """Point all credential/user storage at a throwaway dir + fixed key."""
    tmp = tempfile.mkdtemp(prefix="yafu2ebay-test-")
    os.environ["YAFU2EBAY_HOME"] = tmp
    # Deterministic Fernet key so tests don't touch the real keystore.
    from cryptography.fernet import Fernet

    os.environ["YAFU2EBAY_KEY"] = Fernet.generate_key().decode()
    yield tmp


@pytest.fixture
def config() -> Config:
    return Config.load()


@pytest.fixture
def new_item() -> SourceItem:
    return SourceItem(
        source="rakuten", source_id="t-1", url="https://x/y", title="Casio Watch GA-2100",
        price_jpy=9800, condition="new", brand="Casio", category="watches",
        upc="889232180861", mpn="GA-2100-1A1", weight_g=120, dims_cm=(12, 10, 6),
        market_price_usd=119.0,
    )


@pytest.fixture
def used_item() -> SourceItem:
    return SourceItem(
        source="yahoo", source_id="t-2", url="https://x/z", title="Used Switch",
        price_jpy=18000, condition="used", brand="Nintendo", category="electronics",
        weight_g=900, dims_cm=(25, 18, 8), market_price_usd=210.0,
    )
