import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_inflation(client):
    r = (await client.call_tool("am_inflation", {"months": 14})).structured_content
    assert len(r["months"]) == 14 and r["as_of"] >= "1405-06"
    m = r["months"][0]
    assert 100 < m["index"] < 5000 and -5 < m["monthly_pct"] < 30 and 0 < m["point_to_point_pct"] < 500
    assert r["months"][12]["point_to_point_pct"] is not None


async def test_live_inflation_group_province(client):
    g = (await client.call_tool("am_inflation", {"region": "urban", "group": "حمل", "months": 2})).structured_content
    assert "حمل" in g["group"] and g["months"][0]["index"] > 0
    p = (await client.call_tool("am_inflation", {"province": "فارس", "months": 2})).structured_content
    assert p["province"] == "فارس" and p["months"][0]["point_to_point_pct"] > 0


async def test_live_inflation_groups(client):
    r = (await client.call_tool("am_inflation_groups", {"region": "rural"})).structured_content
    assert len(r["groups"]) > 12 and r["groups"][0]["group"] == "شاخص کل"


async def test_live_labour_force(client):
    r = (await client.call_tool("am_labour_force", {"quarters": 4})).structured_content
    u = r["unemployment"][0]
    assert 1 < u["total"] < 30 and u["urban"] and u["rural"] and len(r["participation"]) == 4


async def test_live_population(client):
    r = (await client.call_tool("am_population", {})).structured_content
    assert len(r["provinces"]) == 31 and r["country"]["population"] == 79926270
    p = (await client.call_tool("am_population", {"province": "تهران"})).structured_content
    assert p["province"]["population"] > 12_000_000 and p["counties"]


async def test_live_search_release_table(client):
    s = (await client.call_tool("am_search", {"query": "شاخص قیمت مصرف کننده", "period": "monthly"})).structured_content
    assert s["count"] > 0
    rel = (await client.call_tool("am_release", {"id": 28579})).structured_content
    assert any(f["type"] == "xlsx" for f in rel["files"])
    sheets = (await client.call_tool("am_table", {"id": 28579})).structured_content
    assert len(sheets["sheets"]) >= 10
    t = (
        await client.call_tool("am_table", {"id": 28579, "sheet": "3", "find": "خدمات", "max_cols": 3})
    ).structured_content
    assert t["total_rows"] >= 1


async def test_live_calendar(client):
    r = (await client.call_tool("am_calendar", {"query": "مصرفی"})).structured_content
    assert r["count"] >= 12


async def test_live_gdp(client):
    r = (await client.call_tool("am_gdp", {"periods": 4})).structured_content
    s = r["series"][0]
    assert len(r["series"]) == 4 and -50 < s["growth_pct"] < 50 and s["gdp_const_bn_rial"] > 1e6
    y = (await client.call_tool("am_gdp", {"frequency": "annual", "periods": 3})).structured_content
    assert len(y["series"]) == 3 and "-Q" not in y["series"][0]["period"]


async def test_live_ppi(client):
    r = (await client.call_tool("am_ppi", {"quarters": 3})).structured_content
    s = r["series"][0]
    assert s["index"] > 100 and s["point_to_point_pct"] is not None and len(r["series"]) == 3
    assert (
        (await client.call_tool("am_ppi", {"sector": "صنعت"})).structured_content["sector"].strip().startswith("صنعت")
    )


async def test_live_indicators(client):
    t = (await client.call_tool("am_indicators", {})).structured_content
    assert {"trade", "housing", "household", "prices"} <= set(t["topics"])
    r = (await client.call_tool("am_indicators", {"topic": "prices", "table": "دهک", "last": 2})).structured_content
    tab = r["tables"][0]
    assert len(tab["rows"]) == 2 and tab["columns"][0].startswith("دهک اول") and tab["rows"][0][1] > 0
    p = (await client.call_tool("am_indicators", {"table": "استان", "find": "تهران", "max_cols": 3})).structured_content
    assert p["tables"][0]["rows"][0][0] == "تهران"


async def test_live_news(client):
    r = (await client.call_tool("am_news", {"count": 5})).structured_content
    assert r["count"] == 5 and r["items"][0]["title"]
    a = (await client.call_tool("am_news", {"id": r["items"][0]["id"]})).structured_content
    assert a["published"] and a["text"]


async def test_live_inflation_pdf_report(client):
    r = (await client.call_tool("am_inflation", {"months": 1})).structured_content
    n = r.get("newer_report")
    if n:  # only while the PDF report is ahead of the Excel series
        assert n["national"]["index"] > 100 and n["national"]["groups"]


async def test_live_search_filters(client):
    r = (await client.call_tool("am_search", {"data_type": "tables", "query": "قیمت"})).structured_content
    assert r["count"] > 0 and all(x["kind"] == "جداول آماری" for x in r["results"])
