"""Catalog tools: search the portal's statistical releases, read one release, read the publishing calendar."""

from __future__ import annotations

import functools
import html
import re
from typing import Annotated, Literal
from urllib.parse import quote

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import http
from .registry import tool

CATEGORIES = {"population": 3503, "economic": 3505, "cross-sector": 3506, "census": 3005, "methodology": 3507}
PERIODS = {
    "monthly": 26,
    "quarterly": 24,
    "half-yearly": 22,
    "yearly": 20,
    "biennial": 21,
    "periodic": 19,
    "irregular": 27,
    "unscheduled": 28,
}
TYPES = {
    "time-series": 30,
    "table": 31,
    "report": 32,
    "abstract": 33,
    "publication": 34,
    "infographic": 39,
    "chart": 40,
    "map": 41,
    "metadata": 42,
    "microdata": 43,
    "estimate": 48,
}
SOURCES = {"portal": 13, "info-services": 14, "digital-library": 15, "other": 17}
DATA_TYPES = {"tables": 2, "publications": 4, "metadata": 7, "visual": 10}  # the other portal data types hold no items
MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
_FA = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه"})


def clean(s: str) -> str:
    """Unescape HTML, unify Arabic letter forms and collapse whitespace."""
    s = re.sub(r"ئ(?=\s|$)", "ی", html.unescape(s).translate(_FA))  # Excel names spell final ی as ئ (خراسان رضوئ)
    return re.sub(r"\s+", " ", s).strip()


def strip_tags(s: str) -> str:
    return clean(re.sub(r"<[^>]+>", " ", s))


def _label_values(block: str) -> dict[str, str]:
    return {clean(k): clean(v) for k, v in re.findall(r"<p>([^:<]{2,25}):\s*([^<]*)</p>", block)}


def parse_list(page: str) -> tuple[list[dict], int]:
    """Releases on one catalog page, and the highest page number its pager links to."""
    out = []
    for part in page.split('class="main-statistics-data-info')[1:]:
        m = re.search(r'statid/(\d+)">([^<]+)</a>', part)
        if not m:
            continue
        f = _label_values(part[:2000])
        out.append(
            {
                "id": int(m.group(1)),
                "title": clean(m.group(2)),
                "period": f.get("زمان آماری"),
                "updated": f.get("آخرین بروزرسانی"),
                "frequency": f.get("دوره انتشار"),
                "kind": f.get("نوع داده آماری"),
            }
        )
    pages = [int(x) for x in re.findall(r"currentpage/(\d+)", page)]
    return out, max(pages, default=1)


def parse_release(page: str, statid: int) -> dict:
    i = page.find('class="main-statistics-detail')
    if i < 0:
        raise ToolError(f"Release {statid} was not found on amar.org.ir.")
    block = page[i : page.find("End_Module", i) if "End_Module" in page[i:] else len(page)]
    title = re.search(r"<h5[^>]*><a[^>]*>([^<]+)</a>", block)
    subject = re.search(r"موضوع:\s*(.*?)</p>", block, re.S)
    topics = [clean(re.sub(r"<[^>]+>", "", x)) for x in re.split(r"<br\s*/?>", subject.group(1))] if subject else []
    f = _label_values(block)
    files = []
    for ext, body in re.findall(r'<p class="\.(\w+) border-box">(.*?)</p>', block, re.S):
        href = re.search(r'href="([^"]+)"', body)
        size = re.search(r"حجم:\s*([\d.]+)\s*MB", body)
        kind = re.search(r"نوع انتشار:\s*([^<]*)", body)
        when = re.search(r"زمان انتشار:\s*([^<]*)", body)
        if href:
            files.append(
                {
                    "url": html.unescape(href.group(1)).split("?")[0],
                    "type": ext.lower(),
                    "size_mb": float(size.group(1)) if size else None,
                    "kind": clean(kind.group(1)) if kind else None,
                    "published": clean(when.group(1)) if when else None,
                }
            )
    return {
        "id": statid,
        "title": clean(title.group(1)) if title else None,
        "topic": [t for t in topics if t],
        "period": f.get("زمان آماری"),
        "frequency": f.get("دوره ی انتشار"),
        "files": files,
        "page": f"{http.SITE}/statistical-information/statid/{statid}",
    }


async def release(statid: int) -> dict:
    return parse_release(await http.fetch_text(f"/statistical-information/statid/{statid}"), statid)


def list_path(query, year, period, kind, category, page, source=None, data_type=None) -> str:
    seg = []
    if source:
        seg += ["source", str(SOURCES[source])]
    if data_type:
        seg += ["types", str(DATA_TYPES[data_type])]
    if category:
        seg += ["catid", str(CATEGORIES[category])]
    if year:
        seg += ["year", str(year)]
    if period:
        seg += ["publishperiod", str(PERIODS[period])]
    if kind:
        seg += ["publishtype", str(TYPES[kind])]
    if query:
        seg += ["title", quote(query.strip().replace("/", " "), safe="")]
    if page > 1:
        seg += ["currentpage", str(page)]
    return "/statistical-information" + ("/" + "/".join(seg) if seg else "")


@tool("Search statistical releases")
async def am_search(
    query: Annotated[
        str | None, Field(description="Persian words in the title, e.g. 'نرخ بیکاری', 'تولید ناخالص', 'جمعیت استان'")
    ] = None,
    year: Annotated[
        int | None, Field(description="Jalali year of the statistical time, e.g. 1404", ge=1335, le=1410)
    ] = None,
    period: Annotated[
        Literal["monthly", "quarterly", "half-yearly", "yearly", "biennial", "periodic", "irregular", "unscheduled"]
        | None,
        Field(description="How often the release is published"),
    ] = None,
    kind: Annotated[
        Literal[
            "time-series",
            "table",
            "report",
            "abstract",
            "publication",
            "infographic",
            "chart",
            "map",
            "metadata",
            "microdata",
            "estimate",
        ]
        | None,
        Field(description="Type of publication"),
    ] = None,
    category: Annotated[
        Literal["population", "economic", "cross-sector", "census", "methodology"] | None,
        Field(
            description="Top-level topic: population = demographic and social, economic, cross-sector = cross-cutting and environment"
        ),
    ] = None,
    page: Annotated[int, Field(description="Page of 25 results, 1-based", ge=1, le=200)] = 1,
    source: Annotated[
        Literal["portal", "info-services", "digital-library", "other"] | None,
        Field(
            description="Which Centre system holds it: the national portal, statistical info services, digital library"
        ),
    ] = None,
    data_type: Annotated[
        Literal["tables", "publications", "metadata", "visual"] | None,
        Field(
            description="The portal's data type: statistical tables, publications and reports, metadata and microdata, images"
        ),
    ] = None,
) -> dict:
    """Search the Statistical Centre of Iran (amar.org.ir) catalog of releases: tables, time series, reports.

    Use it to find a release by Persian title words, year, how often it is published or its type. With no filters
    it lists the newest releases. Each result has an id: pass it to am_release for the files, or to am_table to read
    an Excel table. Ready tools need no search: am_inflation, am_labour_force, am_population, am_gdp, am_ppi and
    am_indicators (trade, housing, money, budget, household income and spending). `source` and `data_type` narrow
    by the portal's own system and data type (tables, publications, metadata, visual).
    """
    path = list_path(query, year, period, kind, category, page, source, data_type)
    rows, last = parse_list(await http.fetch_text(path))
    return {"page": page, "next_page": page + 1 if last > page else None, "count": len(rows), "results": rows}


@tool("Get one release")
async def am_release(
    id: Annotated[int, Field(description="Release id from am_search, e.g. 58298", ge=1)],
) -> dict:
    """Get one statistical release from amar.org.ir: title, topic path, period and its downloadable files.

    Use it after am_search. Files are Excel (xlsx/xls), PDF or Word links; read an Excel file with am_table
    (passing this id), open the PDF link yourself. A release the portal removed gives an error.
    """
    r = await release(id)
    for f in r["files"]:
        f["url"] = http.SITE + f["url"] if f["url"].startswith("/") else f["url"]
    return r


def _month_no(s: str | int | None) -> int | None:
    if s is None:
        return None
    s = str(s).strip()
    if s.isdigit():
        return int(s) if 1 <= int(s) <= 12 else None
    s = clean(s)
    return MONTHS.index(s) + 1 if s in MONTHS else None


def parse_calendar(page: str) -> list[dict]:
    items = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) != 15 or not strip_tags(tds[0]).isdigit():
            continue
        for m, cell in enumerate(tds[3:], 1):
            text = strip_tags(cell.replace("&nbsp;", " "))
            day = re.match(r"(\d+)", text)
            if not day:
                continue
            link = re.search(r"statid/(\d+)", cell)
            ref = re.search(r"\(([^)]*)\)", text)
            items.append(
                {
                    "product": strip_tags(tds[1]),
                    "office": strip_tags(tds[2]),
                    "month": m,
                    "day": int(day.group(1)),
                    "covers": ref.group(1).strip() if ref else None,
                    "id": int(link.group(1)) if link else None,
                }
            )
    return items


@tool("Release calendar")
async def am_calendar(
    year: Annotated[int, Field(description="Jalali year of the calendar", ge=1400, le=1410)] = 1405,
    month: Annotated[
        str | None, Field(description="Month number 1-12 or Persian name (e.g. 'آبان'); omit for the whole year")
    ] = None,
    query: Annotated[
        str | None, Field(description="Persian words in the product name, all must match, e.g. 'نیروی کار' or 'مصرفی'")
    ] = None,
) -> dict:
    """Get the Statistical Centre of Iran's publishing calendar: which statistics come out on which day of a month.

    Use it to answer "when is the next inflation (or labour force, GDP...) release". Each item gives the product,
    the responsible office, the month and day it is scheduled for and the period it covers. `id` is set once the
    release is published: pass it to am_release. Dates are Jalali (month 1 = فروردین). Planned dates can shift.
    """
    try:
        page = await http.fetch_text(f"/publishing-calendar{year}")
    except http.ApiError as e:
        if e.status == 404:
            raise ToolError(f"There is no publishing calendar page for {year}; try 1404 or 1405.") from e
        raise
    items = parse_calendar(page)
    if not items:
        raise ToolError("The calendar page had no table; amar.org.ir may have changed its layout.")
    if month is not None:
        mo = _month_no(month)
        if mo is None:
            raise ToolError("month must be 1-12 or a Persian month name such as 'آبان'.")
        items = [i for i in items if i["month"] == mo]
    if query:
        words = clean(query).split()
        items = [i for i in items if all(w in i["product"] for w in words)]
    items.sort(key=lambda i: (i["month"], i["day"]))
    return {"year": year, "count": len(items), "items": items}


RSS = "/DesktopModules/DnnForge%20-%20NewsArticles/RSS.aspx?TabID=182&ModuleID=1576&MaxCount="


def _tag(part: str, t: str) -> str:
    return html.unescape((re.search(rf"<{t}>(.*?)</{t}>", part, re.S) or [None, ""])[1]).strip()


def parse_rss(xml: str, query: str | None = None) -> list[dict]:
    items = []
    for part in re.findall(r"<item>(.*?)</item>", xml, re.S):
        tag = functools.partial(_tag, part)
        link = tag("link")
        m = re.search(r"/news/id/(\d+)", link)
        if not m:
            continue
        pub = re.search(r"\w+, (\d+ \w+ \d{4} [\d:]+)", tag("pubDate"))
        items.append(
            {
                "id": int(m.group(1)),
                "title": clean(tag("title")),
                "summary": strip_tags(tag("description"))[:300],
                "published_gmt": pub.group(1) if pub else None,
                "url": link.replace("http://", "https://", 1).split("#")[0],
            }
        )
    if query:
        words = clean(query).split()
        items = [i for i in items if all(w in i["title"] + " " + i["summary"] for w in words)]
    return items


def parse_article(page: str, news_id: int) -> dict:
    i = page.find('class="ArticleView"')
    if i < 0:
        raise ToolError(f"News item {news_id} was not found on amar.org.ir.")
    block = page[i : page.find("End_Module", i) if "End_Module" in page[i:] else len(page)]
    when = re.search(r"تاریخ انتشار:\s*([^<]+)", block)
    cats = [clean(x) for x in re.findall(r'<a class="cat\d[^>]*>([^<]+)</a>', block)]
    body = re.search(r'class="ArticleView_bg">(.*)', block, re.S)
    text = re.sub(r"-->|امتیاز:", " ", strip_tags(body.group(1))) if body else ""  # comment remnants, rating label
    text = re.sub(r"\s+", " ", text)
    text = re.split(r"\s(?:تصاویر|اشتراک گذاری)\s", text + " ", maxsplit=1)[0].strip()
    return {
        "id": news_id,
        "published": clean(when.group(1)) if when else None,
        "categories": cats,
        "text": text[:4000],
        "url": f"{http.SITE}/news/id/{news_id}",
    }


@tool("News and press releases")
async def am_news(
    id: Annotated[int | None, Field(description="News item id from the list, to read its text", ge=1)] = None,
    query: Annotated[
        str | None, Field(description="Persian words that must all appear in the title or summary (list mode)")
    ] = None,
    count: Annotated[
        int, Field(description="How many latest items to fetch for the list (newest first)", ge=1, le=50)
    ] = 15,
) -> dict:
    """List the latest news and press releases of the Statistical Centre of Iran, or read one item.

    Without `id` it returns the newest items (id, title, summary, GMT time, url) from the portal's news feed;
    `query` keeps only items whose title or summary has all those words. With `id` it returns the item's date,
    categories and text (long items are cut at 4000 characters). Use it for announcements such as census news,
    interviews and release notes; the numbers themselves are in the data tools.
    """
    if id is not None:
        return parse_article(await http.fetch_text(f"/news/id/{id}"), id)
    items = parse_rss(await http.fetch_text(RSS + str(count)), query)
    return {"count": len(items), "items": items}
