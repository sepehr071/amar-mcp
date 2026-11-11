import asyncio
import io

import httpx
import openpyxl
import pytest
from conftest import fixture_bytes, fixture_text

from amar_mcp import data, http, site

pytestmark = pytest.mark.anyio

LIST = "/statistical-information/*"


def release_page(statid: int, file: str, ext: str = "xlsx", title: str = "t") -> str:
    return (
        f'<div class="main-statistics-detail detail"><h5 class="title-style"><a href="x/statid/{statid}">{title}</a></h5>'
        "<p>موضوع: الف >> ب <br> </p><p>زمان آماری: مهر 1405</p><p>دوره ی انتشار: ماهانه</p>"
        f'<p class=".{ext} border-box"><a class="downloadable" href="{file}"> دانلود فایل</a>'
        "<span>حجم:0.5 MB</span><span>نوع انتشار: سری زمانی</span><span>زمان انتشار: 1405/07/13</span></p></div>End_Module_1"
    )


def serve_book(routes, statid: int, file: str, name: str):
    routes[f"/statistical-information/statid/{statid}"] = release_page(statid, file)
    routes[file] = fixture_bytes(name)


def test_list_path():
    assert site.list_path(None, None, None, None, None, 1) == "/statistical-information"
    p = site.list_path("بیکاری", 1404, "monthly", "table", "economic", 2)
    assert p.startswith("/statistical-information/catid/3505/year/1404/publishperiod/26/publishtype/31/title/%D8%A8")
    assert p.endswith("/currentpage/2")

    # the site drops year/kind unless a source, type, category or title comes with them
    assert (
        site.list_path(None, 1403, None, "table", None, 1)
        == "/statistical-information/source/13/year/1403/publishtype/31"
    )


async def test_search(client, fake):
    fake[LIST] = fixture_text("list.html")
    r = (await client.call_tool("am_search", {"query": "تورم"})).structured_content
    assert r["count"] == 3 and r["next_page"] == 2
    first = r["results"][0]
    assert first["id"] == 58298 and first["period"] == "مهر 1405" and first["frequency"] == "ماهانه"
    assert "/title/" in str(fake.calls[-1].url)


async def test_release(client, fake):
    fake["/statistical-information/statid/58298"] = fixture_text("release_58298.html")
    r = (await client.call_tool("am_release", {"id": 58298})).structured_content
    assert r["title"].startswith("شاخص قیمت مصرف کننده")
    assert r["topic"] == ["آمارهای اقتصادی >> شاخص های قیمت >> شاخص قیمت مصرف کننده"]
    assert r["files"][0]["url"] == "https://amar.org.ir/Portals/0/Articles/shg0507.pdf"
    assert r["files"][0]["size_mb"] == 0.12


async def test_release_missing(client, fake):
    fake["/statistical-information/statid/1"] = "<html>nothing</html>"
    assert (await client.call_tool("am_release", {"id": 1})).is_error


async def test_calendar(client, fake):
    fake["/publishing-calendar1405"] = fixture_text("calendar.html")
    r = (await client.call_tool("am_calendar", {"month": "فروردین"})).structured_content
    assert r["count"] >= 1
    cpi = next(i for i in r["items"] if "مصرف" in i["product"])
    assert cpi["day"] == 10 and cpi["covers"] == "اسفند 1404" and cpi["id"] == 57125
    assert (await client.call_tool("am_calendar", {"month": "x"})).is_error


async def test_inflation(client, fake):
    serve_book(fake, 28579, "/Portals/0/Statistics/cpi.xlsx", "cpi_national.xlsx")
    fake[LIST] = fixture_text("list.html")  # newest monthly report is Mehr 1405, a month after the series
    r = (await client.call_tool("am_inflation", {"months": 3})).structured_content
    assert r["as_of"] == "1405-06" and r["group"] == "شاخص کل" and r["base"] == "1400"
    assert r["months"][0] == {
        "period": "1405-06",
        "index": 729.84,
        "monthly_pct": 4.24,
        "point_to_point_pct": 89.79,
        "twelve_month_avg_pct": 73.58,
    }
    assert [m["period"] for m in r["months"]] == ["1405-06", "1405-05", "1405-04"]
    assert r["months"][2]["index"] == 676.9
    assert r["newer_report"]["id"] == 58298


async def test_inflation_group_and_province(client, fake):
    serve_book(fake, 28579, "/Portals/0/Statistics/cpi.xlsx", "cpi_national.xlsx")
    fake[LIST] = "<html></html>"
    g = (await client.call_tool("am_inflation", {"months": 1, "group": "04"})).structured_content
    assert g["group"].startswith("04 - مسکن") and g["months"][0]["index"] > 0 and "newer_report" not in g
    p = (await client.call_tool("am_inflation", {"months": 2, "province": "مرکزی"})).structured_content
    assert p["province"] == "مرکزی" and p["months"][0]["period"] == "1405-06" and "index" not in p["months"][0]
    assert (await client.call_tool("am_inflation", {"group": "zzz"})).is_error


async def test_inflation_groups(client, fake):
    serve_book(fake, 28579, "/Portals/0/Statistics/cpi.xlsx", "cpi_national.xlsx")
    r = (await client.call_tool("am_inflation_groups", {})).structured_content
    labels = [g["group"] for g in r["groups"]]
    assert labels[0] == "شاخص کل" and any(x.startswith("01 -") for x in labels)
    assert not any(x.startswith("011") for x in labels)  # sub-classes are left out
    assert r["groups"][0]["point_to_point_pct"] == 89.79
    old = (await client.call_tool("am_inflation_groups", {"period": "1405-04"})).structured_content
    assert old["groups"][0]["index"] == 676.9
    assert (await client.call_tool("am_inflation_groups", {"period": "1390-01"})).is_error


async def test_labour_force(client, fake):
    for ids in (data.UNEMPLOYMENT, data.PARTICIPATION):
        for sid in ids.values():
            serve_book(fake, sid, f"/Portals/0/Statistics/l{sid}.xlsx", "labour.xlsx")
    r = (await client.call_tool("am_labour_force", {"quarters": 2})).structured_content
    assert r["as_of"] == "زمستان 1404"
    assert [x["period"] for x in r["unemployment"]] == ["زمستان 1404", "پاییز 1404"]
    assert r["unemployment"][0]["total"] == r["participation"][0]["total"]  # same fixture file for every region


async def test_population(client, fake):
    serve_book(fake, data.CENSUS_COUNTIES, "/Portals/0/Statistics/c.xlsx", "census.xlsx")
    r = (await client.call_tool("am_population", {})).structured_content
    assert r["country"]["population"] == 79926270 and r["country"]["urban_population"] == 59146847
    assert r["provinces"][0]["name"] == "مرکزی" and r["provinces"][0]["households"] == 455866
    p = (await client.call_tool("am_population", {"province": "مرکزی"})).structured_content
    assert p["counties"][0]["name"] == "اراک" and p["counties"][0]["population"] == 591756
    assert (await client.call_tool("am_population", {"province": "ناکجا"})).is_error


async def test_table(client, fake):
    serve_book(fake, 5, "/Portals/0/Statistics/t.xlsx", "cpi_national.xlsx")
    s = (await client.call_tool("am_table", {"id": 5})).structured_content
    assert s["sheets"][0]["sheet"] == "جدول 1" and s["sheets"][0]["n"] == 1
    r = (
        await client.call_tool("am_table", {"id": 5, "sheet": "جدول 1", "find": "مسکن", "max_cols": 3})
    ).structured_content
    assert r["total_rows"] == 2 and r["rows"][0][0] > 3 and len(r["rows"][0]) == 4
    assert r["header"][0][0] == 1
    assert (await client.call_tool("am_table", {"id": 5, "sheet": "nope"})).is_error
    assert (await client.call_tool("am_table", {"id": 5, "file": 2})).is_error


async def test_table_rejects_pdf(client, fake):
    fake["/statistical-information/statid/9"] = release_page(9, "/Portals/0/a.pdf", ext="pdf")
    assert (await client.call_tool("am_table", {"id": 9})).is_error


async def test_not_an_excel_file(client, fake):
    fake["/statistical-information/statid/8"] = release_page(8, "/Portals/0/bad.xlsx")
    fake["/Portals/0/bad.xlsx"] = b"<html>captcha</html>"
    assert (await client.call_tool("am_table", {"id": 8, "sheet": "1"})).is_error


async def test_host_allowlist():
    with pytest.raises(http.ApiError):
        await http.fetch("https://evil.example/x")


def test_workbook_roundtrip():
    wb = openpyxl.Workbook()
    wb.active.append([1, "a"])
    buf = io.BytesIO()
    wb.save(buf)
    assert data.read_book(buf.getvalue()) == {"Sheet": [[1, "a"]]}


async def test_lists_all_tools(client):
    names = {t.name for t in (await client.list_tools()).tools}
    assert names == {
        "am_search",
        "am_release",
        "am_calendar",
        "am_inflation",
        "am_inflation_groups",
        "am_labour_force",
        "am_population",
        "am_table",
        "am_indicators",
        "am_gdp",
        "am_ppi",
        "am_news",
    }


async def test_headers_look_like_a_chrome_navigation(fake):
    fake["/statistical-information/statid/1"] = "ok"
    await http.fetch("/statistical-information/statid/1")
    h = fake.calls[0].headers
    assert h["accept"].startswith("text/html,application/xhtml+xml") and h["accept"].endswith(
        "signed-exchange;v=b3;q=0.7"
    )
    assert (h["sec-fetch-mode"], h["sec-fetch-dest"], h["sec-fetch-site"]) == ("navigate", "document", "same-origin")
    assert h["referer"] == "https://amar.org.ir/statistical-information" and "origin" not in h
    assert h["accept-language"].startswith("fa-IR")


async def test_inflation_drops_the_newer_report_rather_than_run_out_of_time(client, fake, monkeypatch):
    async def slow(end):
        await asyncio.sleep(5)

    serve_book(fake, 28579, "/Portals/0/Statistics/cpi.xlsx", "cpi_national.xlsx")
    monkeypatch.setattr(http, "CALL_BUDGET", 1.5)
    monkeypatch.setattr(data, "_newest_report", slow)
    r = (await client.call_tool("am_inflation", {"months": 1})).structured_content
    assert r["as_of"] == "1405-06" and "newer_report" not in r


async def test_a_call_that_outlives_the_budget_gets_a_clear_error(client, monkeypatch):
    async def stall(request):
        await asyncio.sleep(5)
        return httpx.Response(200, text="<html></html>")

    monkeypatch.setattr(http, "CALL_BUDGET", 0.2)
    http.set_transport(httpx.MockTransport(stall))
    result = await client.call_tool("am_calendar", {})
    assert result.is_error and "did not answer within 0.2 s" in result.content[0].text


async def test_a_retry_joins_the_download_still_running(monkeypatch):
    calls = []

    async def slow(request):
        calls.append(request)
        await asyncio.sleep(0.2)
        return httpx.Response(200, content=b"body")

    http.set_transport(httpx.MockTransport(slow))
    first = asyncio.ensure_future(http.fetch("/x"))
    await asyncio.sleep(0.05)
    first.cancel()  # the timed-out call
    assert await http.fetch("/x") == b"body" and len(calls) == 1
