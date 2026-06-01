from src.compliance import (
    AccountHealth,
    AccountHealthMonitor,
    ComplianceChecker,
)
from src.models import SourceItem


def test_prohibited_keyword_blocked():
    checker = ComplianceChecker()
    item = SourceItem(source="s", source_id="1", url="", title="Survival knife folding",
                      price_jpy=4500, condition="new", category="home")
    res = checker.check(item)
    assert not res.allowed
    assert any("knife" in r for r in res.reasons)


def test_vero_brand_warns_but_allowed():
    checker = ComplianceChecker()
    item = SourceItem(source="s", source_id="1", url="", title="amiibo", price_jpy=1500,
                      condition="new", brand="Nintendo", category="toys", upc="045496380054")
    res = checker.check(item)
    assert res.allowed
    assert res.is_vero
    assert res.warnings


def test_account_health_breach(config):
    mon = AccountHealthMonitor(config)
    verdict = mon.evaluate(AccountHealth(defect_rate=0.10))
    assert not verdict.safe_to_list
    assert verdict.breaches


def test_account_health_ok(config):
    mon = AccountHealthMonitor(config)
    verdict = mon.evaluate(AccountHealth(defect_rate=0.0, late_shipment_rate=0.0,
                                         open_vero_cases=0))
    assert verdict.safe_to_list
