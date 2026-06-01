from src.pipeline import Pipeline


def _pipeline():
    # offline FX so tests are deterministic & network-free.
    return Pipeline(user_id="tester", allow_live_fx=False)


def test_pipeline_runs_offline():
    plans = _pipeline().run("rakuten", "US", limit=20)
    assert plans
    by_sku = {p.item.sku: p for p in plans}

    # New, UPC, profitable, good sell-through -> auto_publish.
    assert by_sku["rakuten:rk-1001"].action == "auto_publish"
    assert by_sku["rakuten:rk-1001"].photo_mode == "catalog"

    # Used item -> manual draft.
    yahoo_plans = _pipeline().run("yahoo", "US", limit=20)
    used = {p.item.sku: p for p in yahoo_plans}["yahoo:yf-2001"]
    assert used.action == "draft_pending_photo"
    assert used.photo_mode == "manual"


def test_prohibited_item_skipped():
    plans = _pipeline().run("rakuten", "US", limit=20)
    knife = {p.item.sku: p for p in plans}["rakuten:rk-1006"]
    assert knife.action == "skip"
    assert any("BLOCKED" in w for w in knife.warnings)


def test_low_sell_through_skipped():
    plans = _pipeline().run("rakuten", "US", limit=20)
    fan = {p.item.sku: p for p in plans}["rakuten:rk-1007"]
    assert fan.action == "skip"


def test_listing_text_is_generated_not_copied():
    plans = _pipeline().run("rakuten", "US", limit=20)
    plan = {p.item.sku: p for p in plans}["rakuten:rk-1001"]
    # Original English copy; source CJK title must not be copied verbatim.
    assert plan.title
    assert "腕時計" not in plan.description
    assert "Casio" in plan.title


def test_marketplace_currency_resolved():
    plans = _pipeline().run("rakuten", "US", limit=1)
    assert plans[0].currency in {"USD", "GBP", "EUR", "AUD"}
