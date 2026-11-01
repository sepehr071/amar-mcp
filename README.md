<!-- mcp-name: io.github.sepehr071/amar-mcp -->

# amar-mcp: Statistical Centre of Iran (مرکز آمار ایران) inflation, unemployment and census MCP server

**amar-mcp is an unofficial, read-only MCP server that gives AI agents the official numbers of the Statistical Centre of Iran (amar.org.ir).**<br>
Its 12 tools return the consumer price index and inflation (monthly, year-on-year, 12-month average, by CPI group, household region and province), unemployment and participation rates, GDP growth, producer prices, the monthly macro-indicators workbook (trade, housing price and rent, money, budget, household income and spending, CPI by decile), census population by province and county, news, the publishing calendar, and any Excel table of any release.<br>
No API key or account is needed. It works in Claude, Cursor, Copilot and any other MCP client.

[![PyPI](https://img.shields.io/pypi/v/amar-mcp?color=2563eb)](https://pypi.org/project/amar-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/amar-mcp)](https://pypi.org/project/amar-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/amar-mcp/blob/main/LICENSE)

[For AI agents](#for-ai-agents) · [Quick start](#quick-start) · [Tools](#tools) · [FAQ](#faq) · [فارسی](#فارسی)

---

## For AI agents

- **What:** unofficial MCP server (model context protocol) for amar.org.ir, Iran's national statistics portal: official CPI and inflation, labour force survey rates, census 1395 population, the release catalog and calendar, and a reader for the portal's Excel tables.
- **Install:** `uvx amar-mcp` or `claude mcp add amar -- uvx amar-mcp`. Transport: stdio. Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.10+).
- **Auth:** none. Optional `AMAR_MCP_PROXY` (HTTP proxy URL, or a comma-separated list); system proxy settings are ignored.
- **Access:** read-only. It cannot log in or change anything, so no confirmation is needed before calling any tool.
- **Network:** amar.org.ir answers only from inside Iran (it drops foreign IPs), so run the server on an Iranian connection or set `AMAR_MCP_PROXY` to an Iranian proxy.
- **Tools:** 12, all prefixed `am_`. Typical sequences: `am_inflation` -> `am_inflation_groups`; `am_gdp`, `am_ppi`; `am_indicators` (topic -> table); `am_search` -> `am_release` -> `am_table`; `am_calendar` for "when is the next release".
- **Use when:** the user asks what Iran's official inflation, CPI, unemployment or population is, what drives inflation (food, housing...), how a province compares, when the Statistical Centre publishes something, or wants any table from amar.org.ir.
- **Do not use when:** the user wants central-bank data, exchange or gold prices (other servers cover those), or live market data. The monthly CPI Excel series trails the PDF report by about a week; `am_inflation` flags this in `newer_report` and reads the newer month's national figures from the PDF.
- **Output:** compact JSON. Periods are Jalali (`1405-06` = Shahrivar 1405). Percentages are percent. Names and titles are Persian as the Centre has them.
- **Machine-readable:** [`server.json`](https://github.com/sepehr071/amar-mcp/blob/main/server.json).

## Example

> **You:** What is Iran's official inflation, and what drives it?
>
> **Agent:** *calls* `am_inflation()` and `am_inflation_groups()`
>
> For Shahrivar 1405 the Statistical Centre reports 4.24% monthly, 89.8% year-on-year and 73.6% for the 12-month average. Food and tobacco rose 122% year-on-year, transport 115%, housing 38%.

<sub>Numbers from a live call on 2026-11-01.</sub>

Other prompts: "unemployment rate by quarter", "population of Yazd province and its counties", "inflation in Fars province", "when is the next CPI release", <span dir="rtl">تورم نقطه به نقطه مسکن شهری چقدر است؟</span>, <span dir="rtl">نرخ بیکاری آخرین فصل؟</span>

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). No API key or account.

**Claude Code:** `claude mcp add amar -- uvx amar-mcp`

**Claude Desktop, Cursor, Windsurf, Gemini CLI:** add

```json
{
  "mcpServers": {
    "amar": { "command": "uvx", "args": ["amar-mcp"] }
  }
}
```

**VS Code:** `.vscode/mcp.json` with `{"servers": {"amar": {"type": "stdio", "command": "uvx", "args": ["amar-mcp"]}}}`.

**Codex CLI:** `codex mcp add amar -- uvx amar-mcp`. Anything else: `uvx amar-mcp`, or `pip install amar-mcp` and run `amar-mcp`.

## Tools

| Tool | What it does |
|---|---|
| `am_inflation` | Official CPI index and inflation per month (monthly, point-to-point, 12-month average) by household region, CPI group or province |
| `am_inflation_groups` | Compare inflation across CPI groups (food, housing, transport...) for one month |
| `am_labour_force` | Unemployment, youth unemployment and participation rates by quarter, total, urban and rural |
| `am_population` | Population and households by province, or by county in one province, from the 1395 census |
| `am_search` | Search the portal's releases by Persian title words, year, frequency, type and topic |
| `am_release` | One release: topic, period and its Excel, PDF and Word files |
| `am_table` | Read any Excel table of a release as compact rows (sheet list, filter, paging) |
| `am_calendar` | The publishing calendar: which statistic comes out on which day of a month |
| `am_gdp` | GDP growth by quarter or year with agriculture, industry, oil and gas, services; levels in billion rials |
| `am_ppi` | Producer price index by quarter and sector (index, quarterly, year-on-year, 4-quarter average) |
| `am_indicators` | The monthly macro workbook: trade, housing price and rent, money, budget, household spending, Gini, CPI by decile and province, energy, stock market |
| `am_news` | Latest news and press releases of the Centre, or the text of one item |

All 12 tools are annotated `readOnlyHint: true`.

## How it works

```text
  AI agent  ──MCP/stdio──▶  amar-mcp  ──HTTPS──▶  amar.org.ir  (release pages, Excel files)
```

The portal has no JSON API, so the server reads its catalog pages and downloads the Excel files that the portal
itself offers, then parses them. It sends browser-like requests (Chrome TLS fingerprint), makes at most 3 concurrent
requests, caches answers for 5 minutes and refuses files over 25 MB. The site's certificate chain is incomplete (it
omits an intermediate); the server ships that public intermediate certificate to verify the connection normally.

## FAQ

**Why does it fail from outside Iran?** amar.org.ir does not answer foreign IPs. Run it on an Iranian connection or set `AMAR_MCP_PROXY`.

**Why is the newest month missing in `am_inflation`?** The Centre publishes a PDF report first and updates the Excel series about a week later. The answer's `newer_report` names that release (use `am_release` for the PDF link); it also carries that month's national index and inflation, read from the PDF.

**Are these the same as central-bank figures?** No. These are the Statistical Centre's figures; the Central Bank of Iran publishes its own.

**Is it affiliated with the Centre?** No. It reads public pages and files, no login.

## فارسی

**amar-mcp** یک سرور MCP غیررسمی و فقط‌خواندنی برای درگاه ملی آمار (مرکز آمار ایران، amar.org.ir) است. با آن می‌توانید تورم و شاخص قیمت مصرف‌کننده (ماهانه، نقطه‌به‌نقطه، متوسط ۱۲ ماه؛ به تفکیک گروه کالا، مناطق شهری و روستایی و استان)، نرخ بیکاری و مشارکت، جمعیت استان‌ها و شهرستان‌ها در سرشماری ۱۳۹۵، تقویم انتشار و هر جدول اکسل منتشرشده را بپرسید.

نصب: `claude mcp add amar -- uvx amar-mcp` (نیاز به [uv](https://docs.astral.sh/uv/getting-started/installation/)). درگاه مرکز آمار فقط از داخل ایران پاسخ می‌دهد؛ سرور را روی اینترنت ایران اجرا کنید یا `AMAR_MCP_PROXY` را روی یک پروکسی ایرانی بگذارید.

<sub>Author: Sepehr Radmard (سپهر رادمرد), [github.com/sepehr071](https://github.com/sepehr071), [linkedin.com/in/sepehr-radmard](https://linkedin.com/in/sepehr-radmard). MIT license.</sub>
