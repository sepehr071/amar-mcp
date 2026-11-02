"""Macro tools: the monthly indicators workbook (trade, housing, money...), quarterly GDP and producer prices."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import http, site
from .data import SEASONS, book, excel_of, key, num, r2
from .registry import tool
from .site import clean

PPI_TOTAL = 52141  # producer price index, all sectors, quarterly (updated in place)
INDICATORS_TITLE = "شاخص های کلان اقتصادی و اجتماعی کشور"
GDP_TITLE = "حساب های ملی فصلی از سال"
TOPICS = {  # english alias -> part of the sheet name in the indicators workbook
    "population": "جمعیت",
    "household": "بودجه خانوار",
    "accounts": "حساب",
    "prices": "شاخص",
    "money": "پولی",
    "budget": "وضع مالی",
    "trade": "تجارت",
    "stock": "بورس",
    "energy": "انرژی",
    "industry": "صنعت",
    "housing": "مسکن",
    "agriculture": "کشاورزی",
}


async def latest(title_start: str) -> dict:
    """The newest catalog release whose title starts with the text and that has an Excel file (the newest one can be PDF-only)."""
    rows, _ = site.parse_list(await http.fetch_text(site.list_path(title_start, None, None, None, None, 1)))
    hits = [r for r in rows if clean(r["title"]).startswith(title_start)]
    if not hits:
        raise ToolError(f"No release titled '{title_start}…' was found on amar.org.ir.")
    for hit in hits:
        if any(f["type"] in ("xlsx", "xls") for f in (await site.release(hit["id"]))["files"]):
            return hit
    raise ToolError(f"No release titled '{title_start}…' has an Excel file.")


async def _xlsx(statid: int) -> tuple[dict, dict[str, list[list]], str]:
    rel, f = await excel_of(statid)
    return rel, await book(http.SITE + f["url"]), http.SITE + f["url"]


# ---- stacked tables of the indicators workbook --------------------------------------------------------------


def _val(x):
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, int | float):
        return round(x, 4)
    s = clean(str(x))
    if s in ("", "-", "000", "×"):  # placeholders for "no data"
        return None
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        f = float(s)
        return int(f) if f.is_integer() else round(f, 4)
    return s[:60]


def split_tables(rows: list[list]) -> list[dict]:
    """The stacked tables of one sheet: title, header rows, data rows, source line."""
    filled = lambda r: [c for c in r if c not in (None, "")]  # noqa: E731
    out, i = [], 0
    while i < len(rows):
        c = filled(rows[i])
        if len(c) == 1 and isinstance(c[0], str) and rows[i][0] == c[0] and not c[0].startswith("ماخذ"):
            j = i + 1
            while j < len(rows) and filled(rows[j]) and not str(rows[j][0] or "").startswith("ماخذ"):
                j += 1
            body = rows[i + 1 : j]
            if len(body) >= 2:
                k = next((n for n, r in enumerate(body) if r and re.search(r"1[34]\d\d", str(r[0] or ""))), None)
                head, data = (body[:k], body[k:]) if k else (body[:1], body[1:])
                src = clean(str(rows[j][0])) if j < len(rows) and rows[j][0] else None
                out.append({"title": clean(c[0]), "head": head, "data": data, "source": src, "timed": bool(k)})
            i = j
        else:
            i += 1
    return out


def _columns(head: list[list], width: int) -> list[str]:
    """One label per column from stacked header rows (merged cells are carried to the right)."""
    grid = []
    for n, r in enumerate(head):
        row, last = [], None
        for j in range(width):
            x = r[j] if j < len(r) and r[j] not in (None, "") else None
            if x is not None:
                last = clean(str(x))
            row.append(last if n < len(head) - 1 else (clean(str(x)) if x is not None else None))
        grid.append(row)
    cols = []
    for j in range(width):
        parts = []
        for g in grid:
            if g[j] and g[j] not in parts:
                parts.append(g[j])
        cols.append(" / ".join(parts)[:70])
    return cols


@tool("Macro indicators")
async def am_indicators(
    topic: Annotated[
        Literal[
            "population",
            "household",
            "accounts",
            "prices",
            "money",
            "budget",
            "trade",
            "stock",
            "energy",
            "industry",
            "housing",
            "agriculture",
        ]
        | None,
        Field(
            description="Subject sheet: population (and labour, education), household (income, expenditure, Gini, "
            "deciles), accounts (national accounts), prices (CPI by decile or province, PPI, food prices), money, "
            "budget (government finance), trade (exports, imports, balance of payments), stock, energy, industry, "
            "housing (permits, price and rent), agriculture. Omit to list all topics."
        ),
    ] = None,
    table: Annotated[
        str | None,
        Field(description="Table number from the topic's list, or Persian words of its title (e.g. 'اجاره', 'دهک')"),
    ] = None,
    find: Annotated[
        str | None, Field(description="Only rows whose first cell contains this text (a year, province, month...)")
    ] = None,
    last: Annotated[
        int, Field(description="Rows to return: the latest ones in a time table, else the first", ge=1, le=60)
    ] = 6,
    col_offset: Annotated[int, Field(description="Skip this many columns on the left (wide tables)", ge=0)] = 0,
    max_cols: Annotated[int, Field(description="Columns to return", ge=1, le=40)] = 12,
) -> dict:
    """Read the Centre's monthly macro-indicators workbook: trade, housing, money, budget, prices by decile and more.

    The workbook (شاخص های کلان اقتصادی و اجتماعی کشور) stacks about 100 small tables on 12 subjects, mostly
    annual and recent quarters or months. Call with no arguments for the topics, with `topic` for its table list
    (n, title), then with `topic` and `table` for the data: columns carry the combined header text, rows are
    [label, values...]. Dash and '000' placeholders come back as null. Data runs to different dates per table
    (housing prices stop at 1400, trade at 1404); the release it came from is in `source`. The newest file
    is found by title, so no id is needed.
    """
    rel = await latest(INDICATORS_TITLE)
    _, sheets, url = await _xlsx(rel["id"])
    head = {"source": {"release": rel["id"], "title": rel["title"], "file": url}}
    names = list(sheets)[4:]  # first four sheets are title, contents, symbols, definitions
    if topic is None and table is None:
        found = {k: next((n for n in names if sub in key(n)), None) for k, sub in TOPICS.items()}
        return {**head, "topics": {k: n for k, n in found.items() if n}, "next": "Pass topic."}
    pool = [(n, split_tables(sheets[n])) for n in names if topic is None or TOPICS[topic] in key(n)]
    if topic and not pool:
        raise ToolError(f"No sheet for topic '{topic}' in the current workbook.")
    if table is None:
        return {
            **head,
            "tables": [{"n": i, "sheet": n, "title": t["title"][:110]} for n, ts in pool for i, t in enumerate(ts, 1)],
            "next": "Pass table=<n or title words>.",
        }
    q = key(table)
    picked = [
        (n, t) for n, ts in pool for i, t in enumerate(ts, 1) if (q.isdigit() and int(q) == i) or q in key(t["title"])
    ]
    if not picked:
        raise ToolError("No table matches. Call am_indicators with just `topic` to list the tables.")
    out = []
    for n, t in picked[:3]:
        width = max(len(r) for r in t["head"] + t["data"])
        cols = _columns(t["head"], width)
        data = [r for r in t["data"] if not find or key(find) in key(r[0] if r else "")]
        sel = data[-last:] if t["timed"] else data[:last]
        win = slice(col_offset + 1, col_offset + 1 + max_cols)
        rows_ = [[_val(r[0]), *[_val(x) for x in r[win]]] for r in sel]
        labels = cols[win]
        while labels and not labels[-1] and all(len(r) < len(labels) + 1 or r[len(labels)] is None for r in rows_):
            labels.pop()  # empty trailing columns of a narrow table
        out.append(
            {
                "sheet": n,
                "title": t["title"],
                "source": t["source"],
                "columns": labels,
                "total_rows": len(data),
                "rows": [r[: len(labels) + 1] for r in rows_],
            }
        )
    return {**head, "tables": out, "more_tables": len(picked) - 3 if len(picked) > 3 else None}


# ---- quarterly GDP -------------------------------------------------------------------------------------------


def _find_col(header: list, *needles: str, avoid: str | None = None) -> int:
    for j, h in enumerate(header):
        k = key(h) if h else ""
        if all(n in k for n in needles) and not (avoid and avoid in k):
            return j
    raise ToolError("The national-accounts table has a new layout; a column was not found.")


@tool("GDP growth (quarterly)")
async def am_gdp(
    frequency: Annotated[
        Literal["quarterly", "annual"], Field(description="quarterly national accounts or yearly totals")
    ] = "quarterly",
    periods: Annotated[int, Field(description="How many latest quarters (or years)", ge=1, le=60)] = 8,
) -> dict:
    """Get Iran's official GDP growth by quarter or year, with the main sectors, latest first.

    From the Centre's quarterly national accounts (base year 1400 = 2021). growth_pct is the change at constant
    prices on the same quarter a year earlier; gdp_ex_oil_pct leaves out crude oil and gas. Sector growth is
    for agriculture, industry (includes oil, mining, manufacturing, construction) and services. Levels are in
    billion rials. For the full tables (value added by activity, expenditure side) use am_table with the
    `source.release` id.
    """
    rel = await latest(GDP_TITLE)
    _, sheets, url = await _xlsx(rel["id"])
    growth, const, cur = sheets["4"], sheets["3"], sheets["1"]
    h = growth[1]
    cols = {
        "growth_pct": _find_col(h, "محصول ناخالص", avoid="بدون نفت"),
        "gdp_ex_oil_pct": _find_col(h, "بدون نفت"),
        "agriculture_pct": _find_col(h, "گروه کشاورزی"),
        "industry_pct": _find_col(h, "گروه صنعت"),
        "oil_gas_pct": _find_col(h, "نفت و گاز"),
        "services_pct": _find_col(h, "گروه خدمات"),
    }
    gc = _find_col(const[1], "محصول ناخالص", avoid="بدون نفت")
    gk = _find_col(cur[1], "محصول ناخالص", avoid="بدون نفت")
    quarterly = frequency == "quarterly"
    idx = {str(r[1]).strip(): n for n, r in enumerate(growth) if n >= 2 and r and r[1] is not None}
    out = []
    for lab, n in reversed(list(idx.items())):
        if bool(re.fullmatch(r"\d{4}_[1-4]", lab)) != quarterly or not re.fullmatch(r"\d{4}(_[1-4])?", lab):
            continue
        g = num(growth[n][cols["growth_pct"]])
        if g is None:  # placeholder row of a year not finished
            continue
        row = {"period": lab.replace("_", "-Q")}
        row |= {k: r2(num(growth[n][j]), 1) for k, j in cols.items()}
        row["gdp_const_bn_rial"] = r2(num(const[n][gc]), 0)
        row["gdp_current_bn_rial"] = r2(num(cur[n][gk]), 0)
        out.append(row)
        if len(out) == periods:
            break
    return {
        "as_of": out[0]["period"] if out else None,
        "unit": "percent change on the same period a year earlier; levels in billion rials",
        "series": out,
        "source": {"release": rel["id"], "title": rel["title"], "file": url},
    }


# ---- producer price index ------------------------------------------------------------------------------------


def _season_map(rows: list[list], ycol: int, c0: int) -> dict[str, int]:
    """Column of each quarter ('1404-Q2') in a sheet whose year sits on row 4 and season name on row 5."""
    out, year = {}, None
    for j in range(c0, max(len(r) for r in rows[3:5])):
        y = rows[3][j] if j < len(rows[3]) else None
        year = int(str(y).strip()) if y not in (None, "") and str(y).strip().isdigit() else year
        s = clean(str(rows[4][j])) if j < len(rows[4]) and rows[4][j] else None
        if year and s in SEASONS:
            out[f"{year}-Q{SEASONS.index(s) + 1}"] = j
    return out


@tool("Producer price index")
async def am_ppi(
    sector: Annotated[
        str | None,
        Field(description="Persian name part: 'كل' (default, all sectors), 'كشاورزي', 'معدن', 'صنعت', 'برق', 'خدمات'"),
    ] = None,
    quarters: Annotated[int, Field(description="How many latest quarters", ge=1, le=60)] = 8,
) -> dict:
    """Get Iran's producer price index (PPI, factory-gate prices) by quarter and sector, latest first.

    Per quarter: the index (100 = 1400), the change on the previous quarter, on the same quarter a year earlier
    (point_to_point_pct) and of the 4-quarter average (annual_pct). Producer prices lead consumer prices, so use it
    next to am_inflation. Detailed PPI series (industry sub-sectors, poultry, dairy, mining, services) are separate
    releases: find them with am_search('شاخص قیمت تولید کننده') and read them with am_table.
    """
    rel, sheets, url = await _xlsx(PPI_TOTAL)
    tabs = {
        "index": sheets["جدول 1"],
        "quarterly_pct": sheets["جدول 2"],
        "point_to_point_pct": sheets["جدول 3"],
        "annual_pct": sheets["جدول 4"],
    }
    base = tabs["index"]
    labels = [(i, clean(str(r[1]))) for i, r in enumerate(base) if i >= 5 and r and len(r) > 1 and r[1]]
    g = key(sector or "کل")
    row = next((x for x in labels if key(x[1]) == g), None) or next((x for x in labels if g in key(x[1])), None)
    if row is None:
        raise ToolError(f"No sector matches '{sector}'. Sectors: {[x[1] for x in labels]}")
    maps = {k: _season_map(t, 1, 2) for k, t in tabs.items()}
    qs = sorted(maps["index"], reverse=True)[:quarters]
    series = []
    for q in qs:
        item = {"period": q}
        for k, t in tabs.items():
            j = maps[k].get(q)
            item[k] = r2(num(t[row[0]][j]), 2) if j is not None and row[0] < len(t) else None
        series.append(item)
    return {
        "sector": row[1],
        "as_of": qs[0] if qs else None,
        "unit": "percent; index base year 100 = 1400",
        "series": series,
        "source": {"release": rel["id"], "title": rel["title"], "file": url},
    }
