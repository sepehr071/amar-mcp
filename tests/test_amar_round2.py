import pytest
from conftest import fixture_bytes, fixture_text
from test_amar import LIST, serve_book

from amar_mcp import data, macro, site

pytestmark = pytest.mark.anyio


def card(statid: int, title: str) -> str:
    return (
        f'<div class="main-statistics-data-info"><h5><a href="x/statid/{statid}">{title}</a></h5>'
        "<p>زمان آماری: ۱</p><p>آخرین بروزرسانی: 1405/06/22</p><p>دوره انتشار: ماهانه</p></div>"
    )


async def test_list_path_filters():
    p = site.list_path("قیمت", 1404, "monthly", None, "economic", 1, "info-services", "tables")
    assert p.startswith("/statistical-information/source/14/types/2/catid/3505/year/1404/publishperiod/26/title/")


async def test_indicators_topics_and_tables(client, fake):
    fake[LIST] = card(58270, macro.INDICATORS_TITLE + " - تاریخ به روزرسانی: 1405/06/18")
    serve_book(fake, 58270, "/Portals/0/Statistics/ind.xlsx", "indicators.xlsx")
    t = (await client.call_tool("am_indicators", {})).structured_content
    assert t["source"]["release"] == 58270 and set(t["topics"]) == {"trade", "housing", "household"}
    lst = (await client.call_tool("am_indicators", {"topic": "housing"})).structured_content
    assert lst["tables"][0]["title"].startswith("متوسط قیمت و اجاره")
    r = (await client.call_tool("am_indicators", {"topic": "housing", "table": "اجاره", "last": 2})).structured_content
    tab = r["tables"][0]
    assert tab["rows"][-1] == ["زمستان 1400", 116711, 127975, 368694]
    assert len(tab["columns"]) == 3 and "اجاره" in tab["columns"][2]  # empty trailing columns are dropped


async def test_indicators_headers_and_find(client, fake):
    fake[LIST] = card(58270, macro.INDICATORS_TITLE + " - x")
    serve_book(fake, 58270, "/Portals/0/Statistics/ind.xlsx", "indicators.xlsx")
    r = (await client.call_tool("am_indicators", {"topic": "trade", "table": "1", "last": 2})).structured_content
    tab = r["tables"][0]
    assert tab["columns"][0] == "واردات / وزن" and tab["columns"][3] == "صادرات / کل / ارزش"
    assert tab["rows"][-1][0] == "بهمن 1404" and tab["rows"][-1][1:5] == [3903, 4793, 12983, 4236]
    assert tab["rows"][-1][5] is None  # '000' placeholder
    g = (
        await client.call_tool("am_indicators", {"topic": "household", "table": "جینی", "find": "1403"})
    ).structured_content
    assert g["tables"][0]["rows"] == [[1403, 0.387, 0.3689, 0.3532]]
    assert (await client.call_tool("am_indicators", {"topic": "trade", "table": "zzz"})).is_error
    assert (await client.call_tool("am_indicators", {"topic": "energy"})).is_error


async def test_gdp(client, fake):
    fake[LIST] = card(54306, "حساب های ملی فصلی از سال 1390 تا بهار 1405")
    serve_book(fake, 54306, "/Portals/0/Statistics/q.xlsx", "gdp.xlsx")
    r = (await client.call_tool("am_gdp", {"periods": 3})).structured_content
    assert r["as_of"] == "1405-Q1"
    first = r["series"][0]
    assert first["growth_pct"] == -10.1 and first["gdp_ex_oil_pct"] == -4.6 and first["oil_gas_pct"] == -26.4
    assert first["gdp_const_bn_rial"] == 21795097 and [s["period"] for s in r["series"]][1] == "1404-Q4"
    y = (await client.call_tool("am_gdp", {"frequency": "annual", "periods": 1})).structured_content
    assert y["series"][0]["period"] == "1404" and y["series"][0]["growth_pct"] == 0.2


async def test_ppi(client, fake):
    serve_book(fake, macro.PPI_TOTAL, "/Portals/0/Statistics/ppi.xlsx", "ppi.xlsx")
    r = (await client.call_tool("am_ppi", {"quarters": 2})).structured_content
    assert r["sector"] == "شاخص کل" and r["as_of"] == "1404-Q4"
    assert r["series"][0] == {
        "period": "1404-Q4",
        "index": 478.47,
        "quarterly_pct": 24.82,
        "point_to_point_pct": 65.02,
        "annual_pct": 51.21,
    }
    s = (await client.call_tool("am_ppi", {"sector": "خدمات", "quarters": 1})).structured_content
    assert s["series"][0]["index"] == 464.99
    assert (await client.call_tool("am_ppi", {"sector": "zzz"})).is_error


async def test_news(client, fake):
    fake["/DesktopModules/*"] = fixture_bytes("news.xml")
    fake["/news/id/20353"] = fixture_text("news_20353.html")
    r = (await client.call_tool("am_news", {"count": 3})).structured_content
    assert (
        r["count"] == 3
        and r["items"][0]["id"] == 20355
        and r["items"][0]["url"].startswith("https://amar.org.ir/news/")
    )
    assert "count=3" not in str(fake.calls[-1].url) and "MaxCount=3" in str(fake.calls[-1].url)
    q = (await client.call_tool("am_news", {"query": "دام سبک"})).structured_content
    assert [i["id"] for i in q["items"]] == [20353]
    a = (await client.call_tool("am_news", {"id": 20353})).structured_content
    assert a["published"] == "06 آبان 1405" and a["categories"] == ["اخبار آماری"]
    assert a["text"].startswith("در تیرماه") and "-->" not in a["text"] and "امتیاز" not in a["text"]


def test_parse_cpi_pdf():
    from io import BytesIO

    from pypdf import PdfReader

    text = PdfReader(BytesIO(fixture_bytes("cpi_report.pdf"))).pages[0].extract_text()
    r = data.parse_cpi_pdf(text)
    assert (r["index"], r["monthly_pct"], r["point_to_point_pct"], r["twelve_month_avg_pct"]) == (
        761.8,
        4.4,
        88.7,
        76.8,
    )
    assert (
        len(r["groups"]) == 12
        and r["groups"][6]["group"].startswith("07 - حمل")
        and r["groups"][6]["monthly_pct"] == 10.7
    )
    assert data.parse_cpi_pdf("nothing") is None


async def test_inflation_reads_pdf_report(client, fake):
    serve_book(fake, 28579, "/Portals/0/Statistics/cpi.xlsx", "cpi_national.xlsx")
    fake[LIST] = fixture_text("list.html")
    fake["/statistical-information/statid/58298"] = fixture_text("release_58298.html")
    fake["/Portals/0/Articles/shg0507.pdf"] = fixture_bytes("cpi_report.pdf")
    n = (await client.call_tool("am_inflation", {"months": 1})).structured_content["newer_report"]
    assert n["period"] == "1405-07" and n["national"]["point_to_point_pct"] == 88.7
    assert n["national"]["groups"][0]["group"].startswith("01 -")
