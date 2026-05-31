from src.analytics import SaleRecord, build_dashboard, format_dashboard
from src.exporter import ExportRecord, to_csv
from src.monitoring import RateLimiter, ScraperMonitor


def test_dashboard_aggregates():
    records = [
        SaleRecord("rakuten:1", "watches", "rakuten", "2026-05",
                   sold_price_jpy=18000, cost_jpy=9800, actual_shipping_jpy=2000,
                   actual_fee_jpy=2700, estimated_profit_jpy=3000),
        SaleRecord("rakuten:2", "toys", "rakuten", "2026-05",
                   sold_price_jpy=4000, cost_jpy=1500, actual_shipping_jpy=900,
                   actual_fee_jpy=600, estimated_profit_jpy=900),
    ]
    dash = build_dashboard(records)
    assert dash.total.count == 2
    assert dash.total.profit_jpy == records[0].actual_profit_jpy + records[1].actual_profit_jpy
    assert "watches" in dash.by_category
    # format produces a string without raising.
    assert "P&L Dashboard" in format_dashboard(dash)


def test_export_csv_has_header_and_rows():
    rec = ExportRecord(
        sale_date="2026-05-30", order_id="EB-1", sku="rakuten:1",
        description="Casio watch", dest_country="US",
        sold_price_jpy=18000, purchase_price_jpy=9800,
        carrier="ems", tracking_no="EE123", export_date="2026-05-31",
    )
    csv = to_csv([rec])
    assert "sale_date" in csv.splitlines()[0]
    assert "rakuten:1" in csv
    assert "9800" in csv


def test_rate_limiter_enforces_interval():
    import time

    rl = RateLimiter(calls_per_sec=50)  # 20ms interval
    rl.wait()
    t0 = time.monotonic()
    rl.wait()
    assert time.monotonic() - t0 >= 0.015


def test_scraper_monitor_zero_results_alert():
    mon = ScraperMonitor("rakuten")
    mon.record(0)
    assert any(a.level == "warning" for a in mon.alerts)
    mon.record_error("boom")
    assert not mon.healthy()
