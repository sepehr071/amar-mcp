"""Data tools: read the portal's Excel releases (inflation, labour force, census population, any table)."""

from __future__ import annotations

import asyncio
import io
import re
import time
from typing import Annotated, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import http, site
from .registry import tool
from .site import MONTHS, clean

# Series releases that the portal updates in place (the id stays, the file link changes). Checked 2026-10-31.
CPI = {"national": 28579, "urban": 28595, "rural": 28569}
UNEMPLOYMENT = {"total": 22572, "urban": 22573, "rural": 22574}  # 15 years and older, quarterly
PARTICIPATION = {"total": 22569, "urban": 22570, "rural": 22571}
YOUTH_UNEMPLOYMENT = {"total": 21889, "urban": 21887, "rural": 21886}  # 15-24 years
CENSUS_COUNTIES = 52312  # census 1395: households and population of provinces and counties
SEASONS = ["بهار", "تابستان", "پاییز", "زمستان"]
MAX_TABLE_BYTES = 25_000_000

_books: dict[str, tuple[float, dict[str, list[list]]]] = {}


def key(s) -> str:
    """Matching key: unified letters, ZWNJ as space, lower case."""
    return clean(str(s).replace("\u200c", " ")).lower()


def num(x) -> float | None:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) else None


def read_book(data: bytes) -> dict[str, list[list]]:
    """All sheets of an .xlsx or .xls file as lists of rows."""
    if data[:2] == b"PK":
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        out = {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb}
        wb.close()
        return out
    if data[:4] == b"\xd0\xcf\x11\xe0":
        import xlrd

        wb = xlrd.open_workbook(file_contents=data)
        return {s.name: [[(c or None) for c in s.row_values(i)] for i in range(s.nrows)] for s in wb.sheets()}
    raise ToolError("That file is not an Excel workbook (.xlsx or .xls).")


async def book(url: str) -> dict[str, list[list]]:
    if (hit := _books.get(url)) and time.monotonic() - hit[0] < http.CACHE_TTL:
        return hit[1]
    data = await http.fetch(url)
    parsed = await asyncio.to_thread(read_book, data)
    if len(_books) >= 4:
        _books.pop(next(iter(_books)))
    _books[url] = (time.monotonic(), parsed)
    return parsed


async def excel_of(statid: int, n: int = 1) -> tuple[dict, dict]:
    """The release and its n-th Excel file (1-based)."""
    rel = await site.release(statid)
    xl = [f for f in rel["files"] if f["type"] in ("xlsx", "xls")]
    if not xl:
        raise ToolError(f"Release {statid} has no Excel file (files: {[f['type'] for f in rel['files']] or 'none'}).")
    if not 1 <= n <= len(xl):
        raise ToolError(f"Release {statid} has {len(xl)} Excel file(s); file must be 1-{len(xl)}.")
    return rel, xl[n - 1]


def r2(x, nd=2):
    return None if x is None else round(x, nd)


# ---- inflation ----------------------------------------------------------------------------------------------


def _month_no(label) -> int | None:
    s = clean(str(label or "")).replace("\u200c", "")
    return MONTHS.index(s) + 1 if s in MONTHS else None


def _tail(rows: list[list], c0: int, row: int = 3) -> tuple[int, int, int]:
    """(last data column, year, month) of a monthly sheet whose years sit on row 1 and months on row 2."""
    nums = [j for j, x in enumerate(rows[row]) if j >= c0 and num(x) is not None]
    if not nums:
        raise ToolError("The Excel table has no numbers; amar.org.ir may have changed its layout.")
    last = nums[-1]
    month = _month_no(rows[2][last])
    year = next(
        (int(m.group(1)) for x in reversed(rows[1][: last + 1]) if x and (m := re.search(r"(1[34]\d\d)", str(x)))), None
    )
    if month is None or year is None:
        raise ToolError("Could not read the month header of the Excel table; amar.org.ir may have changed its layout.")
    return last, year, month


def _back(year: int, month: int, k: int) -> tuple[int, int]:
    t = year * 12 + month - 1 - k
    return t // 12, t % 12 + 1


def _tail_values(rows: list[list], i: int, c0: int, n: int) -> dict[str, float | None]:
    last, y, m = _tail(rows, c0)
    out = {}
    for k in range(n):
        j = last - k
        if j < c0:
            break
        py, pm = _back(y, m, k)
        out[f"{py}-{pm:02d}"] = r2(num(rows[i][j]))
    return out


def _pick(rows: list[list], group: str | None, first: int = 3) -> int:
    """Row index of the CPI group (None = all-items index) in a CPI sheet."""
    labels = [(i, clean(str(r[0]))) for i, r in enumerate(rows) if i >= first and r and r[0]]
    if group is None:
        return labels[0][0]
    g = key(group)
    for test in (
        lambda lab: g.isdigit() and re.match(rf"0*{int(g)}\s*-", lab) is not None,
        lambda lab: key(lab) == g,
        lambda lab: g in key(lab),
    ):
        for i, lab in labels:
            if test(lab):
                return i
    raise ToolError(f"No CPI group matches '{group}'. Call am_inflation_groups for the list of groups.")


def _is_group(raw: str) -> bool:
    """Top-level CPI rows: not indented and not a 3+ digit sub-class code."""
    return raw == raw.lstrip() and not re.match(r"\d{3,}", raw)


_CPI_ROW = re.compile(r"(\d+\.\d\d)(\d+\.\d)(-?\d+\.\d)(-?\d+\.\d)(-?\d+\.\d)\s*$")


def parse_cpi_pdf(text: str) -> dict | None:
    """Headline and main-group figures from the monthly CPI report's table 5 (None if the layout is unknown)."""
    total, groups = None, []
    for line in text.splitlines():
        m = _CPI_ROW.search(line)
        if not m:
            continue
        label = clean(line[: m.start()])
        row = dict(
            zip(
                ("weight", "index", "monthly_pct", "point_to_point_pct", "twelve_month_avg_pct"),
                map(float, m.groups()),
                strict=True,
            )
        )
        if label == "شاخص کل" and total is None:
            total = row
        elif g := re.match(r"(\d+)\s*-\s*(.+)", label):
            groups.append({"group": f"{int(g.group(1)):02d} - {g.group(2).strip()}", **row})
    return {**total, "groups": groups} if total else None


async def _pdf_figures(statid: int) -> dict | None:
    """Numbers of a PDF-only monthly CPI report; any failure just means no numbers."""
    try:
        from io import BytesIO

        from pypdf import PdfReader

        rel = await site.release(statid)
        pdf = next(f for f in rel["files"] if f["type"] == "pdf")
        raw = await http.fetch(http.SITE + pdf["url"] if pdf["url"].startswith("/") else pdf["url"])

        def pages() -> str:
            texts = [p.extract_text() or "" for p in PdfReader(BytesIO(raw)).pages]
            return "\n".join(t for t in texts if "جدول5" in t)

        return parse_cpi_pdf(await asyncio.to_thread(pages))
    except Exception:  # noqa: BLE001  (numbers are a bonus; the report link is still returned)
        return None


async def _newest_report(end: tuple[int, int]) -> dict | None:
    """The newest monthly CPI report on the portal, if it is newer than the Excel series."""
    rows, _ = site.parse_list(
        await http.fetch_text(site.list_path("شاخص قیمت مصرف کننده -", None, "monthly", None, None, 1))
    )
    for r in rows:
        m = re.match(r"شاخص قیمت مصرف کننده - (\S+) (\d{4})", r["title"])
        if m and (mo := _month_no(m.group(1))) and (int(m.group(2)), mo) > end:
            figs = await _pdf_figures(r["id"])
            return {
                "id": r["id"],
                "title": r["title"],
                "updated": r["updated"],
                "period": f"{m.group(2)}-{mo:02d}",
                "national": figs,
                "note": "Newer than the Excel time series; national all-households figures read from the PDF report"
                if figs
                else "Newer than the Excel time series; only a PDF report so far, see am_release.",
            }
    return None


async def _cpi_file(region: str) -> tuple[dict[str, list[list]], dict]:
    rel, f = await excel_of(CPI[region])
    return await book(http.SITE + f["url"]), {
        "release": rel["id"],
        "title": rel["title"],
        "period": rel["period"],
        "published": f["published"],
        "file": http.SITE + f["url"],
    }


@tool("Inflation (CPI)")
async def am_inflation(
    region: Annotated[
        Literal["national", "urban", "rural"], Field(description="Households: all of Iran, urban or rural")
    ] = "national",
    months: Annotated[int, Field(description="How many latest months to return", ge=1, le=60)] = 12,
    group: Annotated[
        str | None,
        Field(
            description="A CPI group: Persian name part (e.g. 'مسکن', 'خوراک', 'حمل') or its 2-digit code ('04'). "
            "Omit for the all-items index. See am_inflation_groups for the list."
        ),
    ] = None,
    province: Annotated[
        str | None,
        Field(
            description="Province name (e.g. 'تهران', 'خوزستان'): returns that province's all-items inflation instead"
        ),
    ] = None,
) -> dict:
    """Get Iran's official consumer price index (CPI) and inflation from the Statistical Centre of Iran, latest first.

    Per month: the index, the change on the previous month (monthly_pct), the change on the same month a year
    earlier (point_to_point_pct, the headline "inflation" figure) and the change of the 12-month average on the
    previous 12 months (twelve_month_avg_pct, "annual inflation"). Use region and group for a breakdown (food,
    housing, ...) or province for one province (no index there, no groups). `newer_report` appears when the portal
    has published a later month as PDF before its Excel series is updated.
    """
    sheets, src = await _cpi_file(region)
    if province:
        return await _province_inflation(sheets, src, region, province, months)
    t1, t2, t3, t4 = (sheets[f"جدول {n}"] for n in (1, 2, 3, 4))
    i1 = _pick(t1, group)
    label = clean(str(t1[i1][0]))
    cols = {}
    for name, t in (("index", t1), ("monthly_pct", t2), ("point_to_point_pct", t3), ("twelve_month_avg_pct", t4)):
        i = next((i for i, r in enumerate(t) if i >= 3 and r and r[0] and clean(str(r[0])) == label), None)
        cols[name] = {} if i is None else _tail_values(t, i, 1, months + 1)
    periods = sorted(cols["index"], reverse=True)[:months]
    out = {
        "region": region,
        "group": label,
        "as_of": periods[0] if periods else None,
        "base": (re.search(r"100\s*=\s*(\d{4})", str(t1[0][0])) or [None, None])[1],
        "unit": "percent; index base year 100",
        "months": [{"period": p, **{k: v.get(p) for k, v in cols.items()}} for p in periods],
        "source": src,
    }
    if periods and (n := await _newest_report(tuple(map(int, periods[0].split("-"))))):
        out["newer_report"] = n
    return out


async def _province_inflation(sheets, src, region, province, months) -> dict:
    g = key(province)
    cols = {}
    name = None
    for sheet, field in (
        ("جدول 8", "monthly_pct"),
        ("جدول 9", "point_to_point_pct"),
        ("جدول 10", "twelve_month_avg_pct"),
    ):
        t = sheets[sheet]
        rows = [(i, key(r[1])) for i, r in enumerate(t) if i >= 3 and r[1]]
        i = next((i for i, k in rows if k == g), None) or next((i for i, k in rows if g in k), None)
        if i is None:
            raise ToolError(f"No province matches '{province}'. Use a name like 'تهران', 'خراسان رضوی' or 'کل کشور'.")
        name = clean(str(t[i][1]))
        cols[field] = _tail_values(t, i, 2, months)
    periods = sorted(cols["monthly_pct"], reverse=True)[:months]
    return {
        "region": region,
        "province": name,
        "as_of": periods[0],
        "unit": "percent",
        "months": [{"period": p, **{k: v.get(p) for k, v in cols.items()}} for p in periods],
        "source": src,
    }


@tool("Inflation by CPI group")
async def am_inflation_groups(
    region: Annotated[
        Literal["national", "urban", "rural"], Field(description="Households: all of Iran, urban or rural")
    ] = "national",
    period: Annotated[
        str | None, Field(description="Month as YYYY-MM (e.g. '1405-06'); omit for the latest month")
    ] = None,
) -> dict:
    """Compare official inflation across CPI groups (food, housing, transport, health...) for one month.

    For each main group: the index, the monthly change, the change on the same month a year earlier
    (point_to_point_pct) and the 12-month average change. Use it to see what drives inflation. For one group's
    history over many months use am_inflation with `group`.
    """
    sheets, src = await _cpi_file(region)
    t = [sheets[f"جدول {n}"] for n in (1, 2, 3, 4)]
    last, y, m = _tail(t[0], 1)
    want = (y, m)
    if period:
        mm = re.fullmatch(r"(1[34]\d\d)-(\d{1,2})", period.strip())
        if not mm or not 1 <= int(mm.group(2)) <= 12:
            raise ToolError("period must look like '1405-06' (Jalali year-month).")
        want = (int(mm.group(1)), int(mm.group(2)))
    k = (y * 12 + m) - (want[0] * 12 + want[1])
    if k < 0 or last - k < 1:
        raise ToolError(f"period {period} is outside the series (latest {y}-{m:02d}).")
    groups = []
    for i, row in enumerate(t[0]):
        if i < 3 or not row or not row[0] or not _is_group(str(row[0])):
            continue
        label = clean(str(row[0]))
        g = {"group": label, "index": r2(num(row[last - k]))}
        for name, tb in zip(("monthly_pct", "point_to_point_pct", "twelve_month_avg_pct"), t[1:], strict=True):
            j = next((j for j, r in enumerate(tb) if j >= 3 and r and r[0] and clean(str(r[0])) == label), None)
            g[name] = None if j is None else _value_at(tb, j, want)
        groups.append(g)
    return {
        "region": region,
        "period": f"{want[0]}-{want[1]:02d}",
        "unit": "percent; index base year 100",
        "groups": groups,
        "source": src,
    }


def _value_at(rows: list[list], i: int, want: tuple[int, int]) -> float | None:
    last, y, m = _tail(rows, 1)
    j = last - ((y * 12 + m) - (want[0] * 12 + want[1]))
    return r2(num(rows[i][j])) if 1 <= j <= last else None


# ---- labour force -------------------------------------------------------------------------------------------


def _quarters(rows: list[list], n: int) -> list[tuple[str, float | None]]:
    """Latest n quarters of a one-row labour-force sheet (years on row 1, seasons on row 2, values on row 3)."""
    nums = [j for j, x in enumerate(rows[3]) if j >= 1 and num(x) is not None]
    if not nums:
        raise ToolError("The labour-force table has no numbers; amar.org.ir may have changed its layout.")
    last = nums[-1]
    season = clean(str(rows[2][last]))
    year = next((int(x) for x in reversed(rows[1][: last + 1]) if num(x) is not None), None)
    if season not in SEASONS or year is None:
        raise ToolError("Could not read the quarter header of the labour-force table.")
    t = year * 4 + SEASONS.index(season)
    out = []
    for k in range(n):
        if last - k < 1:
            break
        q = t - k
        out.append((f"{SEASONS[q % 4]} {q // 4}", r2(num(rows[3][last - k]), 1)))
    return out


@tool("Labour force")
async def am_labour_force(
    measure: Annotated[
        Literal["all", "unemployment", "participation", "youth_unemployment"],
        Field(
            description="unemployment = rate for people 15 and older; participation = economic participation rate; "
            "youth_unemployment = ages 15-24; all = unemployment and participation"
        ),
    ] = "all",
    quarters: Annotated[int, Field(description="How many latest quarters", ge=1, le=40)] = 8,
) -> dict:
    """Get Iran's official unemployment and labour-force participation rates by quarter, latest first.

    From the Statistical Centre's labour force survey: percent of the active population (unemployment) or of the
    population 15+ (participation), for the whole country, urban and rural areas. The newest quarter lags about
    two to three months. For other labour tables (employment by sector, hours, wages) use am_search.
    """
    wanted = {
        "unemployment": UNEMPLOYMENT,
        "participation": PARTICIPATION,
        "youth_unemployment": YOUTH_UNEMPLOYMENT,
    }
    if measure == "all":
        wanted = {k: wanted[k] for k in ("unemployment", "participation")}
    else:
        wanted = {measure: wanted[measure]}
    jobs = {(m, reg): excel_of(sid) for m, ids in wanted.items() for reg, sid in ids.items()}
    found = dict(zip(jobs, await asyncio.gather(*jobs.values()), strict=True))
    out: dict = {"unit": "percent", "note": "unemployment: share of active population; participation: share of 15+"}
    src = {}
    for m, ids in wanted.items():
        merged: dict[str, dict] = {}
        for reg in ids:
            rel, f = found[(m, reg)]
            url = http.SITE + f["url"]
            sheet = next(iter((await book(url)).values()))
            for p, v in _quarters(sheet, quarters):
                merged.setdefault(p, {"period": p})[reg] = v
            src[f"{m}_{reg}"] = rel["id"]
        out[m] = list(merged.values())
    out["as_of"] = out[next(iter(wanted))][0]["period"]
    out["source_release_ids"] = src
    return out


# ---- population (census 1395) -------------------------------------------------------------------------------


@tool("Population of provinces and counties")
async def am_population(
    province: Annotated[
        str | None,
        Field(description="Province name (e.g. 'فارس', 'گیلان'): lists its counties instead of all provinces"),
    ] = None,
) -> dict:
    """Get population and households by province, or by county within one province, from Iran's 1395 census.

    The latest published full census (Aban 1395 = 2016) with total, urban, rural, male and female population and
    households. The 1405 census is under way and not published yet. For projections to later years or age groups
    use am_search ('پیش بینی جمعیت') and am_table.
    """
    rel, f = await excel_of(CENSUS_COUNTIES)
    rows = next(iter((await book(http.SITE + f["url"])).values()))
    recs: dict[tuple, dict] = {}
    for r in rows[2:]:
        if len(r) < 9 or r[3] is None or str(r[3]).strip() not in ("0", "1", "2"):
            continue
        rec = str(r[3]).strip()
        k = (rec, str(r[0]).strip(), str(r[1]).strip(), clean(str(r[2])))
        d = recs.setdefault(k, {"name": k[3]})
        part = {"0": "", "1": "urban_", "2": "rural_", "3": "nonresident_"}.get(str(r[4]).strip())
        if part is None:
            continue
        d[part + "population"] = int(r[6])
        if part == "":
            d.update(households=int(r[5]), male=int(r[7]), female=int(r[8]))
    out = {
        "census": "1395 (2016)",
        "source": {"release": rel["id"], "title": rel["title"], "file": http.SITE + f["url"]},
    }
    if not province:
        out["country"] = next(d for k, d in recs.items() if k[0] == "0")
        out["provinces"] = [d for k, d in recs.items() if k[0] == "1"]
        return out
    g = key(province)
    provs = [(k, d) for k, d in recs.items() if k[0] == "1"]
    prov = next((x for x in provs if key(x[1]["name"]) == g), None) or next(
        (x for x in provs if g in key(x[1]["name"])), None
    )
    if prov is None:
        raise ToolError(f"No province matches '{province}'. Call am_population with no argument to see the names.")
    out["province"] = prov[1]
    out["counties"] = [d for k, d in recs.items() if k[0] == "2" and k[1] == prov[0][1]]
    return out


# ---- any table ----------------------------------------------------------------------------------------------


def _cell(x):
    if isinstance(x, float):
        return round(x, 4)
    if isinstance(x, str):
        x = clean(x)
        return x[:80] + "…" if len(x) > 80 else x
    return x


@tool("Read an Excel table")
async def am_table(
    id: Annotated[int, Field(description="Release id from am_search or am_release", ge=1)],
    file: Annotated[int, Field(description="Which Excel file of the release (1-based, see am_release)", ge=1)] = 1,
    sheet: Annotated[
        str | None, Field(description="Sheet name or 1-based number. Omit to list the sheets first.")
    ] = None,
    find: Annotated[str | None, Field(description="Only rows that contain this text in any cell")] = None,
    offset: Annotated[int, Field(description="Skip this many matching rows (for paging)", ge=0)] = 0,
    max_rows: Annotated[int, Field(description="Rows to return", ge=1, le=200)] = 40,
    col_offset: Annotated[int, Field(description="Skip this many columns on the left (wide time series)", ge=0)] = 0,
    max_cols: Annotated[int, Field(description="Columns to return", ge=1, le=60)] = 14,
) -> dict:
    """Read an Excel table of a statistical release as compact rows (works for any release with an xlsx/xls file).

    First call with just the id: you get the sheet list with sizes and titles. Then pass `sheet`; rows come as
    [excel_row_number, cell, cell...] with empty rows removed. Use `find` to pick rows (e.g. a province or a group
    name), `offset` to page and `col_offset` for wide monthly series (the newest values are usually at the right
    end: see `cols` in the answer). Files over 25 MB are refused.
    """
    rel, f = await excel_of(id, file)
    sheets = await book(http.SITE + f["url"])
    head = {"release": rel["title"], "file": http.SITE + f["url"]}
    if sheet is None:
        listing = []
        for n, (name, rows) in enumerate(sheets.items(), 1):
            title = next((_cell(c) for r in rows[:6] for c in r if isinstance(c, str) and c.strip()), None)
            listing.append(
                {
                    "n": n,
                    "sheet": name,
                    "rows": len(rows),
                    "cols": max((len(r) for r in rows), default=0),
                    "title": title,
                }
            )
        return {**head, "sheets": listing, "next": "Call again with sheet=<name or n>."}
    names = list(sheets)
    if sheet.isdigit() and 1 <= int(sheet) <= len(names):
        name = names[int(sheet) - 1]
    else:
        name = next((n for n in names if key(n) == key(sheet)), None) or next(
            (n for n in names if key(sheet) in key(n)), None
        )
    if name is None:
        raise ToolError(f"No sheet '{sheet}'. Sheets: {names}")
    rows = sheets[name]
    ncols = max((len(r) for r in rows), default=0)
    sel = [(i + 1, r) for i, r in enumerate(rows) if any(c not in (None, "") for c in r)]
    if find:
        q = key(find)
        sel = [(i, r) for i, r in sel if any(c is not None and q in key(c) for c in r)]
    page = sel[offset : offset + max_rows]
    window = lambda r: [_cell(c) for c in r[col_offset : col_offset + max_cols]]  # noqa: E731
    top = [(i, r) for i, r in enumerate(rows, 1) if any(c not in (None, "") for c in r)][:3]
    return {
        **head,
        "sheet": name,
        "cols": ncols,
        "col_offset": col_offset,
        "total_rows": len(sel),
        # the sheet's first rows (title and column headers), so a filtered or shifted window can be read
        "header": [[i, *window(r)] for i, r in top] if find or offset or col_offset else None,
        "rows": [[i, *window(r)] for i, r in page],
        "next_offset": offset + max_rows if offset + max_rows < len(sel) else None,
    }
