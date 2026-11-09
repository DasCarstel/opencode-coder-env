#!/usr/bin/env python3
"""Regressionstests für hestia/backend/wardrobe_costs.py — komplett ohne Netz.

Abgedeckt: parse_costs (Fixtur-Payload im models.dev-Layout: Provider-Key,
cost-Block je Modell, fehlende/ungültige Einträge), merge_payload (Nur
ACTIVE_MODELS, Preise je Modell, Limits inkl. "unlimited" und fehlende
→ null, meta + wardrobe-Info-Block) und fetch-Kanten (mocked urllib —
kein echter Abruf; Fehler → None, Cache-TTL).

Läuft mit pytest UND direkt:
    pytest -q hestia/ci/test_wardrobe_costs.py
    python3 hestia/ci/test_wardrobe_costs.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import wardrobe_costs  # noqa: E402

# --- Fixtures ---------------------------------------------------------------


def _model(cost=None):
    m = {"id": "x", "name": "X"}
    if cost is not None:
        m["cost"] = cost
    return m


FIXTURE_PAYLOAD = {
    "opencode-go": {
        "models": {
            "glm-5.3-flash": _model({"input": 0.25, "output": 1.2, "cache_read": 0.03}),
            "gpt-6-luna": _model({"input": 1.5, "output": 7.5, "cache_read": 0.15}),
            "kimi-k2.7-code": _model({"input": 0, "output": 0, "cache_read": 0}),
            # Modell ohne cost-Block → überspringen
            "no-cost-model": _model(),
            # cost kein dict → überspringen
            "broken-cost-model": {"cost": "free"},
        },
    },
    "anthropic": {"models": {"claude-x": _model({"input": 9, "output": 9})}},
    "openrouter": {},
}


# --- parse_costs ------------------------------------------------------------


def test_parse_costs_extracts_opencode_go_models():
    out = wardrobe_costs.parse_costs(FIXTURE_PAYLOAD)
    assert set(out) == {"glm-5.3-flash", "gpt-6-luna", "kimi-k2.7-code"}
    assert out["glm-5.3-flash"] == {"input": 0.25, "output": 1.2, "cache_read": 0.03}


def test_parse_costs_ignores_other_providers():
    out = wardrobe_costs.parse_costs(FIXTURE_PAYLOAD)
    assert "claude-x" not in out


def test_parse_costs_empty_and_garbage():
    assert wardrobe_costs.parse_costs(None) == {}
    assert wardrobe_costs.parse_costs({}) == {}
    assert wardrobe_costs.parse_costs({"opencode-go": {}}) == {}
    assert wardrobe_costs.parse_costs({"opencode-go": {"models": "nope"}}) == {}


def test_parse_costs_allows_partial_cost_blocks():
    out = wardrobe_costs.parse_costs(
        {"opencode-go": {"models": {"half": _model({"input": 0.4, "output": "x"})}}}
    )
    assert out["half"] == {"input": 0.4}


# --- merge_payload ----------------------------------------------------------


def test_merge_active_models_in_order():
    ids = [m["id"] for m in wardrobe_costs.merge_payload({}, {})["models"]]
    assert ids == [s["id"] for s in wardrobe_costs.ACTIVE_MODELS]
    assert len(ids) == 10


def test_merge_prices_by_model():
    prices = wardrobe_costs.parse_costs(FIXTURE_PAYLOAD)
    out = wardrobe_costs.merge_payload(prices, {})
    by_id = {m["id"]: m for m in out["models"]}
    assert by_id["glm-5.3-flash"]["prices"]["output"] == 1.2
    # Modell ohne Preis in der Fixture → null
    assert by_id["gemini-4-argon"]["prices"] is None


def test_merge_limits_and_missing_limit_is_none():
    limits = {"glm-5.3-flash": 10.0, "gpt-6-luna": "unlimited"}
    out = wardrobe_costs.merge_payload({}, limits)
    by_id = {m["id"]: m for m in out["models"]}
    assert by_id["glm-5.3-flash"]["limit"] == 10.0
    assert by_id["gpt-6-luna"]["limit"] == "unlimited"
    # fehlt in den Budgets → null
    assert by_id["qwen3.8-max"]["limit"] is None


def test_merge_active_badge_on_glm():
    out = wardrobe_costs.merge_payload({}, {})
    glm = [m for m in out["models"] if m["id"] == "glm-5.3-flash"][0]
    assert glm["badge"] == "aktiv"
    others = [m for m in out["models"] if m["id"] != "glm-5.3-flash"]
    assert all(m["badge"] is None for m in others)


def test_merge_meta_and_wardrobe_block():
    out = wardrobe_costs.merge_payload(None, None, fetched="2026-11-09T10:00:00")
    assert out["meta"] == {
        "prices_source": "models.dev",
        "prices_fetched": "2026-11-09T10:00:00",
        "limits_source": "hestia-benchmarks.jsonc budgets",
        "limits_checked": "2026-11-09",
    }
    info = out["wardrobe"]
    assert "glm-5.3-flash" in info["active"]
    assert "gemini-flash-latest" in info["fallback"]
    assert "Nano Banana" in info["image_render"]
    assert "2026-11-09" in info["measured"]


# --- fetch-Kanten (gemockt, kein Netz) --------------------------------------


def _mock_urlopen(payload, status=200):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def opener(req, timeout=0):
        assert timeout == wardrobe_costs.PRICE_TIMEOUT
        return Resp()

    return opener


def test_fetch_parses_live_payload(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen(FIXTURE_PAYLOAD))
    monkeypatch.setattr(wardrobe_costs, "PRICE_CACHE", {"data": None, "ts": 0.0})
    out = wardrobe_costs.fetch_model_costs()
    assert out["glm-5.3-flash"]["input"] == 0.25


def test_fetch_url_error_returns_none(monkeypatch):
    def boom(req, timeout=0):
        raise urllib.error.URLError("dns down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(wardrobe_costs, "PRICE_CACHE", {"data": None, "ts": 0.0})
    assert wardrobe_costs.fetch_model_costs() is None


def test_fetch_timeout_returns_none(monkeypatch):
    def boom(req, timeout=0):
        raise TimeoutError("timeout")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(wardrobe_costs, "PRICE_CACHE", {"data": None, "ts": 0.0})
    assert wardrobe_costs.fetch_model_costs() is None


def test_fetch_uses_custom_user_agent(monkeypatch):
    seen = {}

    def opener(req, timeout=0):
        seen["ua"] = req.headers.get("User-agent") or req.headers.get("User-Agent")
        raise urllib.error.URLError("stop")  # Auswertung danach; kein Netz nötig

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    monkeypatch.setattr(wardrobe_costs, "PRICE_CACHE", {"data": None, "ts": 0.0})
    wardrobe_costs.fetch_model_costs()
    assert seen["ua"] == wardrobe_costs.PRICE_UA == "my-coding-agent/1.0"


def test_fetch_cache_ttl(monkeypatch):
    calls = []

    wr = wardrobe_costs
    wr.PRICE_CACHE.update(ts=0.0, data=None)  # geklont via local var dafür

    def opener(req, timeout=0):
        calls.append(1)
        return _mock_urlopen(FIXTURE_PAYLOAD)(req, timeout)

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    import time
    now = time.time()
    # simuliere frischen Cache (<600s): kein neuer Abruf
    wr.PRICE_CACHE.update(ts=now, data={"glm-5.3-flash": {"input": 0.0, "output": 0.0, "cache_read": 0.0}})
    out = wr.fetch_model_costs()
    assert not calls
    assert out["glm-5.3-flash"]["input"] == 0.0
    # abgelaufener Cache: genau ein neuer Abruf
    wr.PRICE_CACHE.update(ts=now - wr.PRICE_TTL - 1)
    wr.fetch_model_costs()
    assert len(calls) == 1


def test_monthly_limits_swallows_load_errors(monkeypatch):
    # gobudget.settings darf nie reißen — und wenn doch, monthly_limits fängt:
    import gobudget

    def boom(force=False):
        raise OSError("github down")

    monkeypatch.setattr(gobudget, "settings", boom)
    assert wardrobe_costs.monthly_limits() == {}


def test_monthly_limits_from_settings(monkeypatch):
    import gobudget

    monkeypatch.setattr(gobudget, "settings", lambda force=False: {
        "provider": "opencode-go", "models": {"glm-5.3-flash": 10.0, "gpt-6-luna": "unlimited"},
    })
    out = wardrobe_costs.monthly_limits()
    assert out["glm-5.3-flash"] == 10.0
    assert out["gpt-6-luna"] == "unlimited"


if __name__ == "__main__":
    try:
        import pytest

        sys.exit(pytest.main(["-q", __file__]))
    except ImportError:
        # pytest nicht installiert (z. B. lokal ohne CI-Dependencies):
        # Tests direkt laufen lassen — der (fixtur-)monkeypatch-Parameter
        # wird als No-Op-Fake bedient, alle Tests ohne monkeypatch bleiben
        # lauffähig.
        import inspect

        failed = 0
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and inspect.isfunction(fn):
                sig = inspect.signature(fn)
                try:
                    if any(p.name == "monkeypatch" for p in sig.parameters.values()):
                        print(f"{name} SKIPPED (pytest fehlt: monkeypatch)")
                        continue
                    fn()
                    print(f"{name} ok")
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    print(f"{name} FAILED: {e}")
        sys.exit(1 if failed else 0)
