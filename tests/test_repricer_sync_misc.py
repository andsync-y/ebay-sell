from src.config import Config
from src.fx import FxProvider
from src.generation import ListingGenerator, make_seo_title
from src.repricer import compute_floor_usd, reprice
from src.sync import ActiveListing, InventorySync


def test_reprice_respects_floor(config, new_item):
    floor = compute_floor_usd(new_item, "US", config, usd_jpy=155.0)
    # Competitor far below floor -> clamp to floor, never below.
    res = reprice(new_item, current_price_usd=floor + 20, lowest_competitor_usd=1.0,
                  dest_country="US", config=config, usd_jpy=155.0)
    assert res.new_price_usd >= floor
    assert res.changed


def test_reprice_holds_when_competitor_higher(config, new_item):
    floor = compute_floor_usd(new_item, "US", config, usd_jpy=155.0)
    res = reprice(new_item, current_price_usd=floor + 5, lowest_competitor_usd=floor + 50,
                  dest_country="US", config=config, usd_jpy=155.0)
    assert not res.changed


def test_fx_fallback_to_config():
    cfg = Config.load()
    rate = FxProvider(cfg).usd_jpy(allow_live=False)
    assert rate.source == "config"
    assert rate.rate == cfg.get("fx.usd_jpy")
    # buffer makes proceeds conservative (buffered <= raw).
    assert rate.buffered_rate <= rate.rate


def test_sync_ends_missing_item():
    sync = InventorySync("tester", allow_live_fx=False)
    # rk-9999 does not exist in sample data -> end.
    d = sync.evaluate(ActiveListing(sku="rakuten:rk-9999", listed_price_usd=50.0))
    assert d.action == "end"


def test_sync_keeps_in_stock():
    sync = InventorySync("tester", allow_live_fx=False)
    d = sync.evaluate(ActiveListing(sku="rakuten:rk-1001", listed_price_usd=500.0))
    # Listed well above required -> keep.
    assert d.action == "keep"


def test_seo_title_within_limit(new_item):
    title = make_seo_title(new_item, max_chars=80)
    assert len(title) <= 80
    assert "Casio" in title


def test_generator_template_no_source_prose(config, used_item):
    gen = ListingGenerator(config)
    text = gen.generate(used_item)
    assert text.source == "template"
    assert "Returns" in text.description  # policy boilerplate present
