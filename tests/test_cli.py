import json

from src.cli import main


def test_cli_sources(capsys):
    rc = main(["sources"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "rakuten" in out and "yahoo" in out


def test_cli_run_json_offline(capsys):
    rc = main(["run", "--user", "clitester", "--source", "rakuten",
               "--dest", "US", "--offline", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert any(p["sku"] == "rakuten:rk-1001" for p in data)
    auto = [p for p in data if p["action"] == "auto_publish"]
    assert auto


def test_cli_run_publish_stub(capsys):
    rc = main(["run", "--user", "clitester2", "--source", "rakuten",
               "--dest", "US", "--offline", "--publish"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "eBay publish STUB" in out


def test_cli_orders(capsys):
    rc = main(["orders", "--user", "clitester3"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "SOLD" in out
