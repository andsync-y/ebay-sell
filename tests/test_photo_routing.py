from src.ebay import EbayClient
from src.models import SourceItem


def _client(config):
    return EbayClient(config, user_id="", store=None)


def test_new_with_upc_routes_catalog(config, new_item):
    res = _client(config).catalog_match(new_item, vero=False)
    assert res.matched and res.photo_mode == "catalog"
    assert res.epid


def test_used_routes_manual(config, used_item):
    res = _client(config).catalog_match(used_item, vero=False)
    assert not res.matched and res.photo_mode == "manual"


def test_no_gtin_routes_manual(config):
    item = SourceItem(source="s", source_id="1", url="", title="t", price_jpy=100,
                      condition="new", brand="Generic", category="home")
    res = _client(config).catalog_match(item, vero=False)
    assert res.photo_mode == "manual"


def test_excluded_brand_routes_manual(config):
    # 'nike' is in settings brand_image_exclusions
    item = SourceItem(source="s", source_id="1", url="", title="Nike Shoe", price_jpy=100,
                      condition="new", brand="Nike", category="fashion", upc="123456789012")
    res = _client(config).catalog_match(item, vero=False)
    assert res.photo_mode == "manual"


def test_vero_forces_manual(config, new_item):
    res = _client(config).catalog_match(new_item, vero=True)
    assert res.photo_mode == "manual"
