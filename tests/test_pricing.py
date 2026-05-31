import math

from src.pricing import (
    choose_shipping,
    compute_price,
    estimate_size,
    volumetric_weight_g,
)


def test_volumetric_weight():
    # 30x25x15 = 11250 cm^3 / 5000 * 1000 = 2250 g
    assert volumetric_weight_g((30, 25, 15), 5000) == 2250


def test_estimate_size_uses_defaults(config):
    from src.models import SourceItem

    item = SourceItem(source="s", source_id="1", url="", title="t", price_jpy=100,
                      category="electronics")
    weight, dims = estimate_size(item, config)
    assert weight == 800
    assert dims == (25, 20, 10)


def test_choose_shipping_picks_cheapest(config, new_item):
    q = choose_shipping(new_item, "US", config)
    assert q is not None
    # Small light watch -> a courier (fedex/dhl) or ems; cost must be positive.
    assert q.cost_jpy > 0
    assert q.zone == "zone2"


def test_compute_price_recovers_costs(config, new_item):
    shipping = choose_shipping(new_item, "US", config)
    pr = compute_price(new_item, shipping, config, usd_jpy=155.0)
    assert pr.ok
    # The realised proceeds after fee+fx must cover cost+shipping+profit.
    denom = 1 - pr.ebay_fee_rate - pr.payment_fx_rate
    realised = pr.list_price_jpy * denom
    assert realised + 1 >= pr.cost_jpy + (shipping.cost_jpy if shipping else 0) + pr.profit_jpy
    assert pr.list_price_usd == round(pr.list_price_jpy / 155.0, 2)


def test_compute_price_cost_basis_profit(config, new_item):
    pr = compute_price(new_item, None, config, usd_jpy=155.0)
    # cost-basis default: profit = cost * margin
    assert pr.profit_jpy == round(new_item.price_jpy * pr.target_margin)
