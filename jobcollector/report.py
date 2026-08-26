"""Render a Notion-style, self-contained HTML dashboard of collected data.

Single file, zero dependencies: inline CSS/JS, data embedded as JSON, rendered
into the DOM via textContent (no innerHTML with scraped content). Views: Jobs
and Items databases (table + board layouts, sortable columns, filters, detail
drawer), plus an Analytics page with KPI cards, bar charts and run-history
trends. Polish: dark mode, keyboard shortcuts, search highlighting, toasts,
collapsible sidebar.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .storage import Store

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JobCollector</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%232383e2'/%3E%3Ctext x='16' y='22' font-family='Arial' font-size='15' font-weight='700' fill='white' text-anchor='middle'%3EJC%3C/text%3E%3C/svg%3E">
<script>
(function () {
  try {
    var t = localStorage.getItem("jc-theme");
    if (!t) t = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    document.documentElement.dataset.theme = t;
  } catch (e) {}
})();
</script>
<style>
  :root {
    --bg: #ffffff; --workspace: #f7f7f5; --sidebar: #f7f7f5;
    --text: #37352f; --muted: #9b9a97; --muted2: #787774;
    --border: #e9e9e7; --border2: #e3e2e0;
    --hover: #efefee; --row-hover: #f7f6f3; --chip: #f1f1ef;
    --accent: #2383e2; --accent-bg: #eff4fb; --accent-hover: #1a74cc;
    --green: #0e9f6e; --red: #e5484d; --yellow: #d97706;
    --scroll: #d3d1cb; --shadow: 0 2px 8px rgba(15, 15, 15, .06);
    --shadow-lg: 0 8px 32px rgba(15, 15, 15, .18);
    --scrim: rgba(15, 15, 15, .35);
    --mark: rgba(35, 131, 226, .18);
  }
  :root[data-theme="dark"] {
    --bg: #191919; --workspace: #1f1f1f; --sidebar: #1f1f1f;
    --text: #ececea; --muted: #8f8f8f; --muted2: #abaaa7;
    --border: #2b2b2b; --border2: #333333;
    --hover: #2a2a2a; --row-hover: #262626; --chip: #2b2b2b;
    --accent: #529cca; --accent-bg: #22364a; --accent-hover: #6db1d8;
    --green: #4cb782; --red: #f2686d; --yellow: #e8a33d;
    --scroll: #3f3f3f; --shadow: 0 2px 8px rgba(0, 0, 0, .4);
    --shadow-lg: 0 8px 32px rgba(0, 0, 0, .6);
    --scrim: rgba(0, 0, 0, .55);
    --mark: rgba(82, 156, 202, .28);
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, Helvetica, Arial, sans-serif;
         background: var(--workspace); color: var(--text); font-size: 14px; }
  button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  input, select { font: inherit; color: inherit; background: var(--bg); border: 1px solid var(--border2);
                  border-radius: 6px; padding: 5px 8px; outline: none; }
  input:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-bg); }
  ::selection { background: rgba(35, 131, 226, .25); }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-thumb { background: var(--scroll); border-radius: 5px;
                              border: 2px solid transparent; background-clip: content-box; }
  ::-webkit-scrollbar-track { background: transparent; }
  mark { background: var(--mark); color: inherit; border-radius: 2px; padding: 0 1px; }

  .app { display: flex; height: 100vh; }

  /* ------------------------------------------------------------ sidebar */
  .sidebar { width: 260px; min-width: 260px; background: var(--sidebar); border-right: 1px solid var(--border);
             display: flex; flex-direction: column; overflow-y: auto; padding: 10px 8px;
             transition: width .18s ease, min-width .18s ease; }
  .app.side-collapsed .sidebar { width: 52px; min-width: 52px; padding: 10px 6px; }
  .ws-name { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 6px;
             font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; }
  .ws-name:hover { background: var(--hover); }
  .ws-name .logo { width: 20px; height: 20px; min-width: 20px; border-radius: 5px; background: var(--accent);
                   display: inline-flex; align-items: center; justify-content: center;
                   color: #fff; font-size: 11px; font-weight: 700; }
  .side-search { margin: 6px 4px 10px; }
  .side-search input { width: 100%; background: var(--bg); }
  .nav-sec { padding: 4px 10px 2px; font-size: 11px; font-weight: 600; color: var(--muted);
             letter-spacing: .03em; text-transform: uppercase; margin-top: 6px; white-space: nowrap; }
  .nav-item { display: flex; align-items: center; gap: 8px; padding: 5px 10px; border-radius: 6px;
              color: var(--muted2); font-size: 14px; cursor: pointer; user-select: none;
              white-space: nowrap; overflow: hidden; }
  .nav-item:hover { background: var(--hover); }
  .nav-item.active { background: var(--chip); color: var(--text); font-weight: 500; }
  .nav-item .ico { width: 18px; min-width: 18px; text-align: center; }
  .nav-item .count { margin-left: auto; color: var(--muted); font-size: 12px; }
  .nav-group { margin-top: 2px; }
  .sidebar-foot { margin-top: auto; padding: 8px 6px 2px; font-size: 12px; color: var(--muted); }
  .side-actions { display: flex; gap: 2px; padding: 0 2px 6px; }
  .side-actions .icon-btn { padding: 6px 9px; border-radius: 6px; font-size: 14px; color: var(--muted2); }
  .side-actions .icon-btn:hover { background: var(--hover); color: var(--text); }
  .side-collapsed .hide-collapse { display: none; }
  .side-collapsed .ws-name, .side-collapsed .nav-item { justify-content: center; padding: 6px 0; }

  /* ---------------------------------------------------------- workspace */
  .main { flex: 1; overflow-y: auto; }
  .crumbs { display: flex; align-items: center; gap: 6px; padding: 14px 24px 0;
            font-size: 13px; color: var(--muted2); }
  .crumbs b { color: var(--text); font-weight: 500; }
  .crumbs .sep { color: var(--muted); }
  .page-title { padding: 6px 24px 0; font-size: 40px; font-weight: 700; letter-spacing: -.02em; }
  .toolbar { position: sticky; top: 0; z-index: 20; display: flex; align-items: center; gap: 8px;
             padding: 12px 24px; flex-wrap: wrap; background: var(--workspace);
             border-bottom: 1px solid transparent; }
  .toolbar.pinned { border-bottom-color: var(--border); }
  .toolbar .search { flex: 0 1 280px; }
  .toolbar .search input { width: 100%; }
  .pill { display: inline-flex; align-items: center; gap: 6px; background: var(--bg);
          border: 1px solid var(--border2); border-radius: 6px; padding: 5px 10px;
          font-size: 13px; color: var(--muted2); cursor: pointer; }
  .pill:hover { background: var(--chip); }
  .pill.active { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }
  .pill .x { font-weight: 700; opacity: .7; }
  .seg { display: inline-flex; background: var(--chip); border-radius: 6px; padding: 2px; }
  .seg button { padding: 4px 12px; border-radius: 5px; font-size: 13px; color: var(--muted2); }
  .seg button.active { background: var(--bg); color: var(--text); box-shadow: 0 1px 3px rgba(15,15,15,.12); }
  .toolbar .spacer { flex: 1; }
  .icon-btn { color: var(--muted2); padding: 5px 8px; border-radius: 6px; font-size: 13px; }
  .icon-btn:hover { background: var(--hover); color: var(--text); }
  .count-chip { color: var(--muted); font-size: 12.5px; font-variant-numeric: tabular-nums; }

  /* -------------------------------------------------------------- table */
  .db-card { margin: 2px 24px 24px; background: var(--bg); border: 1px solid var(--border);
             border-radius: 8px; overflow: hidden; }
  table.db { width: 100%; border-collapse: collapse; }
  table.db th { text-align: left; padding: 8px 12px; font-size: 12px; font-weight: 500;
                color: var(--muted); border-bottom: 1px solid var(--border2);
                cursor: pointer; user-select: none; white-space: nowrap; }
  table.db th:hover { color: var(--text); background: var(--row-hover); }
  table.db th .arr { font-size: 10px; color: var(--accent); font-weight: 700; }
  table.db td { padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
  table.db tbody tr:last-child td { border-bottom: none; }
  table.db tbody tr { cursor: pointer; transition: background .1s; }
  table.db tbody tr:hover { background: var(--row-hover); }
  table.db tbody tr:hover .title-cell::after { content: " →"; color: var(--muted); font-weight: 400; }
  .title-cell { font-weight: 500; color: var(--text); max-width: 420px; overflow: hidden;
                text-overflow: ellipsis; white-space: nowrap; }
  .sub { color: var(--muted2); font-size: 13px; }
  .src { font-size: 12px; color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .dot.active { background: var(--green); box-shadow: 0 0 0 3px rgba(14, 159, 110, .15); }
  .dot.expired { background: var(--red); box-shadow: 0 0 0 3px rgba(229, 72, 77, .12); }
  .tag { display: inline-block; background: var(--chip); color: var(--muted2); border-radius: 4px;
         padding: 1px 7px; font-size: 12px; margin-right: 4px; }
  .empty-state { padding: 56px 24px; text-align: center; color: var(--muted); }
  .empty-state .big { font-size: 34px; margin-bottom: 8px; }
  .empty-state .clear-btn { margin-top: 12px; color: var(--accent); font-size: 13px; }

  /* --------------------------------------------------------- companies */
  .wc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
             gap: 16px; padding: 8px 24px 24px; align-items: start; }
  .wc-panel { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 18px; }
  .wc-head { display: flex; align-items: center; gap: 8px; font-size: 16px; }
  .wc-head b { font-weight: 600; letter-spacing: -.01em; }
  .wc-head .count-chip { margin-left: auto; }
  .wc-hint { color: var(--muted2); font-size: 13px; margin: 6px 0 14px; line-height: 1.45; }
  .wc-add { display: flex; gap: 8px; margin-bottom: 14px; }
  .wc-add input { flex: 1; padding: 7px 10px; border-radius: 6px; }
  .wc-list { display: flex; flex-direction: column; gap: 6px; max-height: 46vh; overflow-y: auto; }
  .wc-row { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border: 1px solid var(--border);
            border-radius: 6px; }
  .wc-row:hover { background: var(--row-hover); }
  .wc-val { flex: 1; font-weight: 500; font-size: 13.5px; overflow: hidden; text-overflow: ellipsis;
            white-space: nowrap; }
  .wc-row .pill { cursor: default; }
  .wc-row .pill.muted { opacity: .55; }
  .wc-actions { display: flex; gap: 2px; }
  .wc-empty { color: var(--muted); font-size: 13px; padding: 12px 2px; }

  /* -------------------------------------------------------------- board */
  .board { display: flex; gap: 12px; overflow-x: auto; padding: 2px 24px 24px; align-items: flex-start; }
  .col { width: 280px; min-width: 280px; background: var(--chip); border-radius: 8px;
         padding: 8px; max-height: calc(100vh - 200px); overflow-y: auto; }
  .col-head { display: flex; align-items: center; gap: 6px; padding: 4px 6px 8px; font-size: 13px;
              font-weight: 600; color: var(--muted2); }
  .col-dot { width: 9px; height: 9px; min-width: 9px; border-radius: 3px; }
  .col-head .n { color: var(--muted); font-weight: 400; margin-left: auto;
                 background: var(--bg); border-radius: 999px; padding: 0 8px; font-size: 11.5px; }
  .card { background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
          padding: 10px 12px; margin-bottom: 8px; cursor: pointer; box-shadow: 0 1px 2px rgba(15,15,15,.04);
          transition: box-shadow .12s ease, transform .12s ease, border-color .12s ease; }
  .card:hover { box-shadow: var(--shadow); transform: translateY(-1px); border-color: var(--border2); }
  .card .t { font-size: 13.5px; font-weight: 500; margin-bottom: 4px; }
  .card .m { font-size: 12.5px; color: var(--muted2); }
  .card .card-src { font-size: 11px; color: var(--muted); font-family: ui-monospace, monospace;
                    margin-top: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* -------------------------------------------------------------- drawer */
  .scrim { position: fixed; inset: 0; background: var(--scrim); z-index: 40;
           opacity: 0; pointer-events: none; transition: opacity .2s ease; }
  .scrim.open { opacity: 1; pointer-events: auto; }
  .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: 480px; max-width: 92vw;
            background: var(--bg); z-index: 41; box-shadow: var(--shadow-lg);
            overflow-y: auto; padding: 24px 28px; transform: translateX(100%);
            transition: transform .22s ease; }
  .drawer.open { transform: none; }
  .drawer .d-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
  .pill-status { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px;
                 border-radius: 999px; font-size: 12.5px; font-weight: 500; }
  .pill-status.active { background: rgba(14, 159, 110, .14); color: var(--green); }
  .pill-status.expired { background: rgba(229, 72, 77, .12); color: var(--red); }
  .pill-status.neutral { background: var(--chip); color: var(--muted2); }
  .drawer .d-title { font-size: 24px; font-weight: 700; letter-spacing: -.01em; line-height: 1.3;
                     word-break: break-word; }
  .drawer .d-meta { margin: 16px 0; border-top: 1px solid var(--border2); }
  .drawer .d-row { display: flex; padding: 7px 0; border-bottom: 1px solid var(--border); font-size: 13.5px; }
  .drawer .d-row .k { width: 110px; color: var(--muted); flex-shrink: 0; }
  .drawer .d-body { margin-top: 14px; font-size: 14px; line-height: 1.65; color: var(--muted2);
                    white-space: pre-wrap; word-break: break-word; }
  .d-actions { display: flex; gap: 8px; margin-top: 18px; }
  .open-btn { display: inline-block; background: var(--accent); color: #fff;
              padding: 7px 14px; border-radius: 6px; font-weight: 500; }
  .open-btn:hover { text-decoration: none; background: var(--accent-hover); }
  .open-btn.ghost { background: var(--chip); color: var(--text); }
  .open-btn.ghost:hover { background: var(--hover); }
  .close-x { position: absolute; top: 14px; right: 14px; color: var(--muted); padding: 6px 9px;
             border-radius: 6px; font-size: 15px; }
  .close-x:hover { background: var(--hover); color: var(--text); }

  /* ---------------------------------------------------------- analytics */
  .an-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
             gap: 16px; padding: 4px 24px 24px; }
  .an-card { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px 20px; }
  .an-card h3 { margin: 0 0 4px; font-size: 14px; font-weight: 600; }
  .an-card .desc { color: var(--muted); font-size: 12.5px; margin-bottom: 12px; }
  .bar-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; font-size: 12.5px; }
  .bar-row .lbl { width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                  color: var(--muted2); text-align: right; }
  .bar-row .track { flex: 1; background: var(--chip); border-radius: 4px; height: 10px; overflow: hidden; }
  .bar-row .fill { height: 100%; border-radius: 4px; background: var(--accent); transition: width .4s ease; }
  .bar-row:hover .fill { background: var(--accent-hover); }
  .bar-row .val { width: 44px; color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }
  .kpi-row { display: flex; gap: 12px; flex-wrap: wrap; margin: 4px 24px 16px; }
  .kpi { background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
         padding: 12px 18px; min-width: 128px; transition: box-shadow .12s ease; }
  .kpi:hover { box-shadow: var(--shadow); }
  .kpi b { display: block; font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .kpi span { font-size: 11.5px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  .kpi small { display: block; margin-top: 3px; font-size: 12px; color: var(--muted2); }
  .trend { display: flex; align-items: flex-end; gap: 4px; height: 96px; padding-top: 6px; }
  .tbar { flex: 1; display: flex; flex-direction: column; justify-content: flex-end;
          align-items: center; gap: 3px; min-width: 0; }
  .tfill { width: 100%; background: var(--accent); border-radius: 3px 3px 0 0; min-height: 2px;
           transition: background .12s ease; }
  .tbar:hover .tfill { background: var(--accent-hover); }
  .tbar:last-child .tfill { background: var(--green); }
  .tbar span { font-size: 9.5px; color: var(--muted); white-space: nowrap; }

  /* -------------------------------------------------------------- reader */
  .reader { display: flex; height: calc(100vh - 128px); min-height: 420px;
            margin: 2px 24px 24px; border: 1px solid var(--border); border-radius: 10px;
            overflow: hidden; background: var(--bg); }
  .r-feeds { width: 230px; min-width: 230px; background: var(--workspace);
             border-right: 1px solid var(--border); display: flex; flex-direction: column; }
  .r-fsec, .r-feed { display: flex; align-items: center; gap: 8px; padding: 6px 12px;
                     margin: 1px 6px; border-radius: 6px; font-size: 13.5px;
                     color: var(--muted2); cursor: pointer; user-select: none; }
  .r-fsec:hover, .r-feed:hover { background: var(--hover); }
  .r-fsec.active, .r-feed.active { background: var(--chip); color: var(--text); font-weight: 500; }
  .r-fsec .badge, .r-feed .badge { margin-left: auto; font-size: 11px; color: var(--muted);
                                   background: var(--bg); border-radius: 999px; padding: 0 7px; }
  .r-feed .badge.hot { color: var(--accent); background: var(--accent-bg); }
  .r-nav-sec { padding: 10px 12px 4px; font-size: 11px; font-weight: 600; color: var(--muted);
               text-transform: uppercase; letter-spacing: .03em; }
  .r-feed-actions { margin-top: auto; display: flex; gap: 6px; padding: 8px;
                    border-top: 1px solid var(--border); }
  .r-feed-actions button { flex: 1; padding: 6px 4px; border-radius: 6px; font-size: 12.5px;
                           color: var(--muted2); background: var(--bg); border: 1px solid var(--border2); }
  .r-feed-actions button:hover { background: var(--hover); color: var(--text); }
  .r-list { width: 340px; min-width: 340px; border-right: 1px solid var(--border);
            display: flex; flex-direction: column; }
  .r-list-head { padding: 10px 12px; border-bottom: 1px solid var(--border); display: flex; gap: 6px; }
  .r-list-head input { flex: 1; min-width: 0; }
  .r-rows { flex: 1; overflow-y: auto; }
  .r-row { display: flex; gap: 9px; padding: 10px 12px; border-bottom: 1px solid var(--border);
           cursor: pointer; }
  .r-row:hover { background: var(--row-hover); }
  .r-row.active { background: var(--accent-bg); }
  .r-row .rdot { width: 8px; height: 8px; min-width: 8px; margin-top: 5px; border-radius: 50%;
                 background: var(--accent); box-shadow: 0 0 0 3px rgba(35, 131, 226, .15); }
  .r-row.read .rdot { background: transparent; box-shadow: none; }
  .r-row .rt { font-size: 13.5px; font-weight: 500; line-height: 1.35;
               display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .r-row.read .rt { font-weight: 400; color: var(--muted2); }
  .r-row .rm { font-size: 11.5px; color: var(--muted); margin-top: 2px; white-space: nowrap;
               overflow: hidden; text-overflow: ellipsis; }
  .r-row .rstar { margin-left: auto; opacity: 0; color: var(--yellow); font-size: 15px; align-self: center; }
  .r-row:hover .rstar, .r-row.starred .rstar { opacity: 1; }
  .r-row.starred .rstar { opacity: 1; }
  .r-article { flex: 1; overflow-y: auto; padding: 28px 40px 64px; min-width: 0; }
  .r-article .ra-title { font-size: 26px; font-weight: 700; line-height: 1.3; letter-spacing: -.01em;
                         margin-bottom: 10px; word-break: break-word; }
  .r-article .ra-meta { color: var(--muted); font-size: 13px; margin-bottom: 16px;
                        display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .r-article .ra-meta .dot-sep { opacity: .5; }
  .r-article .ra-actions { display: flex; gap: 8px; margin-bottom: 22px; flex-wrap: wrap; }
  .r-article .ra-actions button { padding: 6px 14px; border-radius: 6px; font-size: 13px;
                                  background: var(--chip); color: var(--muted2); }
  .r-article .ra-actions button:hover { background: var(--hover); color: var(--text); }
  .r-article .ra-actions button.primary { background: var(--accent); color: #fff; }
  .r-article .ra-actions button.primary:hover { background: var(--accent-hover); }
  .r-article .ra-body { font-size: 15px; line-height: 1.7; color: var(--muted2);
                        white-space: pre-wrap; word-break: break-word; }
  .r-placeholder { flex: 1; display: flex; align-items: center; justify-content: center;
                   color: var(--muted); font-size: 14px; flex-direction: column; gap: 8px; }

  /* ---------------------------------------------------------------- misc */
  .toast { position: fixed; bottom: 22px; left: 50%; transform: translate(-50%, 16px);
           background: var(--text); color: var(--bg); padding: 9px 18px; border-radius: 8px;
           font-size: 13px; opacity: 0; pointer-events: none; transition: all .2s ease;
           z-index: 70; box-shadow: var(--shadow-lg); max-width: 80vw; }
  .toast.show { opacity: 1; transform: translate(-50%, 0); }
  .help { position: fixed; inset: 0; background: var(--scrim); z-index: 60;
          display: flex; align-items: center; justify-content: center; }
  .help-card { background: var(--bg); border-radius: 12px; padding: 22px 26px; width: 360px;
               max-width: 92vw; box-shadow: var(--shadow-lg); }
  .help-card h3 { margin: 0 0 14px; font-size: 16px; font-weight: 600; }
  .help-row { display: flex; align-items: center; justify-content: space-between;
              padding: 6px 0; font-size: 13.5px; color: var(--muted2); }
  .help-row + .help-row { border-top: 1px solid var(--border); }
  kbd { background: var(--chip); border: 1px solid var(--border2); border-bottom-width: 2px;
        border-radius: 4px; padding: 1px 7px; font: 12px ui-monospace, SFMono-Regular, Menlo, monospace;
        color: var(--text); }
  .help-close { margin-top: 16px; width: 100%; background: var(--accent); color: #fff;
                padding: 8px; border-radius: 6px; font-weight: 500; }
  .help-close:hover { background: var(--accent-hover); }
  .hidden { display: none !important; }
  @media (prefers-reduced-motion: reduce) {
    * { transition: none !important; animation: none !important; }
  }
</style>
</head>
<body>
<div class="app" id="app">
  <aside class="sidebar">
    <div class="ws-name"><span class="logo">JC</span><span class="hide-collapse">JobCollector</span></div>
    <div class="side-search hide-collapse"><input id="sideQ" placeholder="Search data…"></div>
    <div id="nav"></div>
    <div class="sidebar-foot hide-collapse" id="generated"></div>
    <div class="side-actions">
      <button id="themeBtn" class="icon-btn" title="Toggle dark mode (D)">🌙</button>
      <button id="collapseBtn" class="icon-btn" title="Collapse sidebar">«</button>
      <button id="helpBtn" class="icon-btn" title="Keyboard shortcuts (?)">?</button>
    </div>
  </aside>
  <main class="main">
    <div class="crumbs" id="crumbs"></div>
    <h1 class="page-title" id="pageTitle"></h1>
    <div class="toolbar" id="toolbar"></div>
    <div id="view"></div>
  </main>
</div>
<div id="scrim" class="scrim"></div>
<div id="drawer" class="drawer"></div>
<div id="toast" class="toast"></div>
<div id="help" class="help hidden">
  <div class="help-card">
    <h3>Keyboard shortcuts</h3>
    <div class="help-row"><span>Focus filter</span><kbd>/</kbd></div>
    <div class="help-row"><span>Table view</span><kbd>T</kbd></div>
    <div class="help-row"><span>Board view</span><kbd>B</kbd></div>
    <div class="help-row"><span>Cycle board grouping</span><kbd>G</kbd></div>
    <div class="help-row"><span>Pages</span><kbd>1</kbd> <kbd>2</kbd> <kbd>3</kbd> <kbd>4</kbd></div>
    <div class="help-row"><span>Reader: next / prev</span><kbd>J</kbd> <kbd>K</kbd></div>
    <div class="help-row"><span>Reader: read / star / filter</span><kbd>M</kbd> <kbd>S</kbd> <kbd>U</kbd></div>
    <div class="help-row"><span>Dark mode</span><kbd>D</kbd></div>
    <div class="help-row"><span>Export CSV</span><kbd>E</kbd></div>
    <div class="help-row"><span>Close panel</span><kbd>Esc</kbd></div>
    <button class="help-close" id="helpClose">Got it</button>
  </div>
</div>

<script id="data" type="application/json">{DATA}</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById("data").textContent);
const JOBS = DATA.jobs, ITEMS = DATA.items, STATS = DATA.stats, ITEMSTATS = DATA.itemStats;
const RUNS = DATA.runs || [], LAST = DATA.lastRun;
const state = {
  page: "jobs", layout: "table", group: "source",
  q: "", sideQ: "", source: "", activeOnly: true, sortCol: "posted", sortDir: -1, sel: null,
};

/* ------------------------------------------------ companies & keywords */
const W = { api: false, items: DATA.watchlist || [] };
try {
  const saved = JSON.parse(localStorage.getItem("jc-watchlist") || "[]");
  if (Array.isArray(saved) && saved.length) W.items = saved;
} catch (e) {}
function watchMatchCount(kind, v) {
  const q = v.toLowerCase();
  return JOBS.filter((j) => {
    if (!j.is_active) return false;
    if (kind === "company") return (j.company || "").toLowerCase().includes(q);
    return (j.title || "").toLowerCase().includes(q) || (j.company || "").toLowerCase().includes(q)
      || (j.description || "").toLowerCase().includes(q) || (j.tags || []).join(" ").toLowerCase().includes(q);
  }).length;
}
function watchSave() { try { localStorage.setItem("jc-watchlist", JSON.stringify(W.items)); } catch (e) {} }
function watchList(kind) { return W.items.filter((w) => w.kind === kind); }
function renderCompanies() {
  const main = $("view");
  const panel = (kind, icon, title, hint, placeholder) => {
    const items = watchList(kind);
    return `<div class="wc-panel">
      <div class="wc-head"><span>${icon}</span><b>${title}</b><span class="count-chip">${items.length}</span></div>
      <p class="wc-hint">${hint}</p>
      <div class="wc-add">
        <input id="wcIn-${kind}" placeholder="${placeholder}" spellcheck="false">
        <button class="pill active" data-add="${kind}">＋ Add</button>
      </div>
      <div class="wc-list">${items.length === 0
        ? `<div class="wc-empty">Nothing tracked yet. Add a ${kind} above to start watching it.</div>`
        : items.map((w) => `<div class="wc-row" data-id="${w.id}">
            <span class="wc-val" title="${esc(w.value)}">${esc(w.value)}</span>
            <span class="pill ${w.count > 0 ? "" : "muted"}">${w.count} job${w.count === 1 ? "" : "s"}</span>
            <span class="wc-actions">
              <button class="icon-btn" data-filter="${esc(w.value)}" title="Filter jobs">🔍</button>
              <button class="icon-btn" data-del="${w.id}" title="Remove">✕</button>
            </span>
          </div>`).join("")}</div>
    </div>`;
  };
  main.innerHTML =
    `<div class="wc-grid">
      ${panel("company", "🏢", "Companies",
        "Track companies you care about. The count shows active jobs whose company name matches.",
        "e.g. Stripe, Canonical, Mozilla…")}
      ${panel("keyword", "🔎", "Keywords",
        "Monitor terms across job titles, descriptions and tags. Click 🔍 to filter the Jobs page.",
        "e.g. remote, rust, ai…")}
    </div>`;
  main.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => watchAdd(b.dataset.add)));
  main.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => watchFilter(b.dataset.filter)));
  main.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => watchDel(parseInt(b.dataset.del, 10))));
  main.querySelectorAll(".wc-add input").forEach((inp) =>
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") watchAdd(inp.id.replace("wcIn-", "")); })
  );
  const f = $("wcIn-company"); if (f) f.focus();
}
function watchAdd(kind) {
  const inp = $("wcIn-" + kind);
  const v = (inp && inp.value || "").trim();
  if (!v) return;
  const dup = watchList(kind).some((w) => w.value.toLowerCase() === v.toLowerCase());
  if (dup) { toast(kind + " already tracked"); return; }
  if (W.api) {
    fetch("/api/watchlist", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, value: v }) })
      .then((r) => r.json()).then((d) => {
        if (d && d.item) { W.items = W.items.concat([d.item]); renderCompanies(); toast("Added " + kind); }
        else toast("Add failed: " + ((d && d.error) || "unknown"));
      }).catch(() => toast("Add failed (server unreachable)"));
  } else {
    const item = { id: Date.now(), kind, value: v, count: watchMatchCount(kind, v) };
    W.items = W.items.concat([item]); watchSave(); renderCompanies(); toast("Added " + kind + " (saved locally)");
  }
}
function watchDel(id) {
  W.items = W.items.filter((w) => w.id !== id);
  if (W.api) fetch("/api/watchlist_delete", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }) }).catch(() => {});
  else watchSave();
  renderCompanies();
}
function watchFilter(v) { state.page = "jobs"; state.q = v; state.sel = null; render(); }
async function watchPing() {
  try {
    const res = await fetch("/api/watchlist");
    const d = await res.json();
    if (d && d.ok) { W.api = true; W.items = d.items; render(); }
  } catch (e) {}
}

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const rxEsc = (s) => s.split("").map((ch) => ".*+?^${}()|[]\\\\".includes(ch) ? "\\\\" + ch : ch).join("");
const ago = (iso) => {
  if (!iso) return "";
  const d = (Date.now() - new Date(iso).getTime()) / 86400000;
  if (d > 365) return Math.floor(d / 365) + "y";
  if (d > 30) return Math.floor(d / 30) + "mo";
  if (d >= 1) return Math.floor(d) + "d";
  const h = Math.floor(d * 24);
  return h >= 1 ? h + "h" : "now";
};
const shortDate = (iso) => (iso ? String(iso).slice(0, 10) : "");
const fullDate = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");

/* ---------------------------------------------------------------- filtering */
function jobFilter(j) {
  if (!j.title || !j.company) return false;   // drop malformed rows
  if (state.activeOnly && !j.is_active) return false;
  const q = (state.q + " " + state.sideQ).toLowerCase().trim();
  if (q && !(j.title + " " + j.company + " " + j.location + " " + j.source + " " + j.tags).toLowerCase().includes(q)) return false;
  if (state.source && !(j.source === state.source || j.source.startsWith(state.source + ":") ||
      (state.source === "board" && j.source_kind === "board"))) return false;
  return true;
}
function itemFilter(it) {
  const q = (state.q + " " + state.sideQ).toLowerCase().trim();
  if (q && !(it.title + " " + it.category + " " + it.source).toLowerCase().includes(q)) return false;
  if (state.source && it.source !== state.source && !it.source.startsWith(state.source + ":") && it.category !== state.source) return false;
  return true;
}
const jobs = () => JOBS.filter(jobFilter);
const items = () => ITEMS.filter(itemFilter);

/* ------------------------------------------------------------------ sorts */
const JCOLS = {
  title: { get: (j) => j.title.toLowerCase(), sort: 1 },
  company: { get: (j) => j.company.toLowerCase(), sort: 1 },
  location: { get: (j) => j.location.toLowerCase(), sort: 1 },
  source: { get: (j) => j.source, sort: 1 },
  posted: { get: (j) => j.posted_at || j.first_seen_at || "", sort: 1 },
};
function sorted(rows, cols, sortCol, dir) {
  const c = cols[sortCol];
  if (!c) return rows;
  return [...rows].sort((a, b) => {
    let x = c.get(a), y = c.get(b);
    if (x < y) return -1 * dir;
    if (x > y) return 1 * dir;
    return 0;
  });
}
function setSort(col) {
  if (state.sortCol === col) state.sortDir *= -1;
  else { state.sortCol = col; state.sortDir = 1; }
  render();
}
const sortArrow = (col) => state.sortCol === col ? ` <span class="arr">${state.sortDir === 1 ? "▲" : "▼"}</span>` : "";

/* ------------------------------------------------------------ sidebar nav */
function sourceGroups() {
  const groups = {};
  for (const [src, counts] of Object.entries(STATS.by_source)) {
    const kind = src.split(":")[0];
    groups[kind] = (groups[kind] || 0) + counts.active;
  }
  for (const [src, n] of Object.entries(ITEMSTATS.by_source || {})) {
    const kind = src.split(":")[0];
    groups[kind] = (groups[kind] || 0) + n;
  }
  return Object.entries(groups).sort((a, b) => b[1] - a[1]);
}
function renderNav() {
  const nav = $("nav");
  const mk = (icon, label, count, page, src) =>
    `<div class="nav-item ${state.page === page ? "active" : ""} ${!page && state.source === src ? "active" : ""}" data-page="${page || ""}" data-src="${src || ""}">
       <span class="ico">${icon}</span><span class="hide-collapse">${label}</span><span class="count hide-collapse">${count || ""}</span></div>`;
  let h = '<div class="nav-sec hide-collapse">Database</div>';
  h += mk("📰", "Reader", DATA.readerUnread || "", "reader", "");
  h += mk("🗂️", "Jobs", DATA.jobsTotal, "jobs", "");
  h += mk("📄", "Engine Items", DATA.itemTotal, "items", "");
  h += mk("📊", "Analytics", "", "analytics", "");
  h += mk("🏢", "Companies", "", "companies", "");
  h += '<div class="nav-sec hide-collapse">Sources</div>';
  for (const [kind, n] of sourceGroups()) {
    h += mk("●", kind, n, null, kind);
  }
  nav.innerHTML = h;
  R.navCount = nav.querySelector('.nav-item[data-page="reader"] .count');
  nav.querySelectorAll(".nav-item").forEach((el) =>
    el.addEventListener("click", () => {
      if (el.dataset.page) { state.page = el.dataset.page; state.sel = null; render(); }
      else { state.page = "jobs"; state.source = el.dataset.src; state.sel = null; render(); }
    })
  );
}

/* ------------------------------------------------------------------ toolbar */
function sourceOptions() {
  const set = new Set();
  JOBS.forEach((j) => set.add(j.source));
  ITEMS.forEach((i) => set.add(i.source));
  return [...set].sort();
}
function renderToolbar() {
  const tb = $("toolbar");
  const isJobs = state.page === "jobs";
  const rows = isJobs ? jobs() : items();
  const total = isJobs ? DATA.jobsTotal : DATA.itemTotal;
  const groupLabels = isJobs ? ["source", "company"] : ["category", "source"];
  let h = "";
  h += `<div class="seg">
          <button class="${state.layout === "table" ? "active" : ""}" data-layout="table">Table</button>
          <button class="${state.layout === "board" ? "active" : ""}" data-layout="board">Board</button>
        </div>`;
  if (state.layout === "board") {
    h += `<div class="seg">${groupLabels.map((g) =>
      `<button class="${state.group === g ? "active" : ""}" data-group="${g}">${g[0].toUpperCase() + g.slice(1)}</button>`).join("")}</div>`;
  }
  if (isJobs) {
    h += `<button class="pill ${state.activeOnly ? "active" : ""}" id="activeOnly" title="Show only currently active postings">✓ Active only</button>`;
  }
  if (state.source) {
    h += `<button class="pill active" id="clearSrc" title="Clear source filter">Source: ${esc(state.source)} <span class="x">✕</span></button>`;
  }
  h += `<div class="search"><input id="q" placeholder="Filter ${isJobs ? "jobs" : "items"}…  ( / )" value="${esc(state.q)}"></div>`;
  h += `<span class="pill"><select id="srcSel"><option value="">All sources</option>${sourceOptions().map((s) => `<option ${s === state.source ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></span>`;
  h += `<span class="spacer"></span>`;
  h += `<span class="count-chip" id="countLabel">${rows.length}${rows.length < total ? " of " + total : ""} shown</span>`;
  h += `<button class="icon-btn" id="exportBtn" title="Export filtered rows as CSV (E)">⬇ Export</button>`;
  tb.innerHTML = h;
  tb.querySelectorAll("[data-layout]").forEach((b) =>
    b.addEventListener("click", () => { state.layout = b.dataset.layout; render(); })
  );
  tb.querySelectorAll("[data-group]").forEach((b) =>
    b.addEventListener("click", () => { state.group = b.dataset.group; render(); })
  );
  tb.querySelectorAll("#activeOnly").forEach((b) =>
    b.addEventListener("click", () => { state.activeOnly = !state.activeOnly; render(); })
  );
  tb.querySelectorAll("#clearSrc").forEach((b) =>
    b.addEventListener("click", () => { state.source = ""; render(); })
  );
  $("q").addEventListener("input", (e) => { state.q = e.target.value; render(); });
  $("srcSel").addEventListener("change", (e) => { state.source = e.target.value; render(); });
  $("exportBtn").addEventListener("click", exportCsv);
}

/* ------------------------------------------------------------- view render */
function hl(s) {
  const out = esc(s);
  const q = (state.q + " " + state.sideQ).trim();
  if (!q) return out;
  const WS = new RegExp(String.fromCharCode(92) + "s+");
  const words = [...new Set(q.toLowerCase().split(WS).filter(Boolean))];
  let res = out;
  for (const w of words) {
    if (w.length < 2) continue;
    res = res.replace(new RegExp(rxEsc(esc(w)), "gi"), (m) => "<mark>" + m + "</mark>");
  }
  return res;
}
function statusDot(j) {
  return `<span class="dot ${j.is_active ? "active" : "expired"}" title="${j.is_active ? "Active" : "Expired"}"></span>`;
}
function rowHtml(j) {
  return `<tr data-idx="${JOBS.indexOf(j)}" title="${esc(j.title)}">
    <td><span class="title-cell">${statusDot(j)}${hl(j.title)}</span></td>
    <td class="sub">${hl(j.company)}</td>
    <td class="sub">${hl(j.location)}</td>
    <td><span class="src">${esc(j.source)}</span></td>
    <td class="sub" title="${fullDate(j.posted_at)}">${j.posted_at ? ago(j.posted_at) : ""}</td>
  </tr>`;
}
function itemRowHtml(it) {
  return `<tr data-iidx="${ITEMS.indexOf(it)}" title="${esc(it.title)}">
    <td><span class="title-cell">${hl(it.title)}</span></td>
    <td class="sub">${esc(it.category) || "—"}</td>
    <td><span class="src">${esc(it.source)}</span></td>
    <td class="sub" title="${fullDate(it.published_at)}">${it.published_at ? ago(it.published_at) : ""}</td>
  </tr>`;
}
function renderTable() {
  const isJobs = state.page === "jobs";
  const cols = isJobs ? JCOLS : { title: { get: (i) => i.title.toLowerCase() }, category: { get: (i) => i.category }, source: { get: (i) => i.source }, published: { get: (i) => i.published_at || "" } };
  const rows = sorted(isJobs ? jobs() : items(), cols, state.sortCol, state.sortDir);
  const head = isJobs
    ? `<th data-sort="title">Title${sortArrow("title")}</th><th data-sort="company">Company${sortArrow("company")}</th><th data-sort="location">Location${sortArrow("location")}</th><th data-sort="source">Source${sortArrow("source")}</th><th data-sort="posted">Posted${sortArrow("posted")}</th>`
    : `<th data-sort="title">Title${sortArrow("title")}</th><th data-sort="category">Category${sortArrow("category")}</th><th data-sort="source">Source${sortArrow("source")}</th><th data-sort="published">Published${sortArrow("published")}</th>`;
  const body = rows.length
    ? rows.map(isJobs ? rowHtml : itemRowHtml).join("")
    : `<tr><td colspan="5"><div class="empty-state"><div class="big">🔍</div>Nothing matches the current filters.<br><button class="clear-btn" id="resetFilters">Clear filters</button></div></td></tr>`;
  $("view").innerHTML = `<div class="db-card"><table class="db"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  $("view").querySelectorAll("th[data-sort]").forEach((th) =>
    th.addEventListener("click", () => setSort(th.dataset.sort))
  );
  $("view").querySelectorAll("tr[data-idx]").forEach((tr) =>
    tr.addEventListener("click", () => openDrawer(JOBS[+tr.dataset.idx]))
  );
  $("view").querySelectorAll("tr[data-iidx]").forEach((tr) =>
    tr.addEventListener("click", () => openDrawer(ITEMS[+tr.dataset.iidx]))
  );
  const rf = $("resetFilters");
  if (rf) rf.addEventListener("click", () => { state.q = ""; state.sideQ = ""; state.source = ""; state.activeOnly = false; render(); $("sideQ").value = ""; });
}
function boardGroups() {
  const rows = state.page === "jobs" ? jobs() : items();
  const key = (r) => {
    if (state.page === "jobs") return state.group === "company" ? (r.company || "(none)") : (r.source || "(none)");
    return state.group === "source" ? (r.source || "(none)") : (r.category || "(none)");
  };
  const map = new Map();
  rows.forEach((r) => { const k = key(r); if (!map.has(k)) map.set(k, []); map.get(k).push(r); });
  return [...map.entries()].sort((a, b) => b[1].length - a[1].length).slice(0, 14);
}
const PALS = [[35, 131, 226], [14, 159, 110], [217, 119, 6], [229, 72, 77], [139, 92, 246],
              [236, 72, 153], [8, 145, 178], [245, 158, 11], [20, 184, 166], [225, 29, 72]];
const palColor = (i, alpha) => { const c = PALS[i % PALS.length]; return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + alpha + ")"; };
function renderBoard() {
  const isJobs = state.page === "jobs";
  const groups = boardGroups();
  if (!groups.length) {
    $("view").innerHTML = `<div class="empty-state"><div class="big">🗂️</div>Nothing matches the current filters.<br><button class="clear-btn" id="resetFilters">Clear filters</button></div>`;
    const rf = $("resetFilters");
    if (rf) rf.addEventListener("click", () => { state.q = ""; state.sideQ = ""; state.source = ""; state.activeOnly = false; render(); $("sideQ").value = ""; });
    return;
  }
  let h = `<div class="board">`;
  groups.forEach(([name, rows], gi) => {
    h += `<div class="col"><div class="col-head"><span class="col-dot" style="background:${palColor(gi, .75)}"></span>${esc(name)} <span class="n">${rows.length}</span></div>`;
    for (const r of rows) {
      if (isJobs) {
        const j = r;
        h += `<div class="card" data-idx="${JOBS.indexOf(j)}"><div class="t">${statusDot(j)}${esc(j.title)}</div>
              <div class="m">${esc(j.company)}${j.location ? " · " + esc(j.location) : ""}</div>
              <div class="card-src">${esc(j.source)}${j.posted_at ? " · " + ago(j.posted_at) : ""}</div></div>`;
      } else {
        h += `<div class="card" data-iidx="${ITEMS.indexOf(r)}"><div class="t">${esc(r.title)}</div>
              <div class="m">${esc(r.category) || "—"}</div>
              <div class="card-src">${esc(r.source)}${r.published_at ? " · " + ago(r.published_at) : ""}</div></div>`;
      }
    }
    h += `</div>`;
  });
  h += `</div>`;
  $("view").innerHTML = h;
  $("view").querySelectorAll("[data-idx]").forEach((el) =>
    el.addEventListener("click", () => openDrawer(JOBS[+el.dataset.idx]))
  );
  $("view").querySelectorAll("[data-iidx]").forEach((el) =>
    el.addEventListener("click", () => openDrawer(ITEMS[+el.dataset.iidx]))
  );
}

/* --------------------------------------------------------------- analytics */
function bars(entries, max) {
  const top = Math.max(1, ...entries.map((e) => e[1]));
  return entries.slice(0, max || 99).map(([lbl, v]) =>
    `<div class="bar-row"><span class="lbl" title="${esc(lbl)}">${esc(lbl)}</span>
     <span class="track"><span class="fill" style="width:${Math.round((v / top) * 100)}%"></span></span>
     <span class="val">${v}</span></div>`).join("");
}
function trendCard() {
  if (!RUNS.length) return "";
  const maxSeen = Math.max(1, ...RUNS.map((r) => r.seen));
  const barsHtml = RUNS.map((r) => {
    const pct = Math.max(2, Math.round((r.seen / maxSeen) * 100));
    return `<div class="tbar" title="${shortDate(r.at)} — ${r.seen} seen · +${r.new} new · −${r.expired} expired">
              <div class="tfill" style="height:${pct}%"></div><span>${shortDate(r.at).slice(5)}</span></div>`;
  }).join("");
  return `<div class="an-card"><h3>Collection history</h3>
          <div class="desc">Jobs seen per run${RUNS.length > 1 ? " — " + RUNS.length + " runs" : ""}. Hover a bar for the new / expired breakdown.</div>
          <div class="trend">${barsHtml}</div></div>`;
}
function renderAnalytics() {
  const bySource = Object.entries(STATS.by_source).sort((a, b) => b[1].total - a[1].total);
  const itemsBySource = Object.entries(ITEMSTATS.by_source || {}).sort((a, b) => b[1] - a[1]);
  const itemsByCat = Object.entries(ITEMSTATS.by_category || {}).sort((a, b) => b[1] - a[1]);
  const lastRunLine = LAST ? `+${LAST.new} new · −${LAST.expired} expired · ${LAST.seen} seen` : "no runs yet";
  const h = `
  <div class="kpi-row">
    <div class="kpi"><b>${STATS.total}</b><span>Total jobs</span><small>${STATS.active} active · ${STATS.expired} expired</small></div>
    <div class="kpi"><b>${STATS.seen_last_run}</b><span>Seen last run</span><small>${STATS.sources} job sources</small></div>
    <div class="kpi"><b>${ITEMSTATS.total}</b><span>Engine items</span><small>${Object.keys(ITEMSTATS.by_category || {}).length} categories</small></div>
    <div class="kpi"><b style="color:var(--green)">${LAST ? "+" + LAST.new : "—"}</b><span>New last run</span><small>${lastRunLine}</small></div>
  </div>
  <div class="an-grid">
    ${trendCard()}
    <div class="an-card"><h3>Jobs per source</h3><div class="desc">Active vs total collected per source.</div>${bars(bySource.map(([s, c]) => [s, c.total]))}</div>
    <div class="an-card"><h3>Engine items per source</h3><div class="desc">RSS and scraped items by source.</div>${bars(itemsBySource)}</div>
    <div class="an-card"><h3>Engine items per category</h3><div class="desc">news / blog / changelog / …</div>${bars(itemsByCat)}</div>
    <div class="an-card"><h3>Top companies</h3><div class="desc">Most collected postings by company.</div>${bars(Object.entries(STATS.by_company || {}).sort((a, b) => b[1] - a[1]).slice(0, 12))}</div>
  </div>`;
  $("view").innerHTML = h;
}

/* ------------------------------------------------------------------ drawer */
function openDrawer(row) {
  if (!row) return;
  const d = $("drawer");
  const isJob = "is_active" in row;
  let status = isJob
    ? `<span class="pill-status ${row.is_active ? "active" : "expired"}">${row.is_active ? "● Active" : "● Expired"}</span>`
    : `<span class="pill-status neutral">● Item</span>`;
  let meta = "";
  if (isJob) {
    meta = `
      <div class="d-row"><span class="k">Company</span><span>${esc(row.company)}</span></div>
      <div class="d-row"><span class="k">Location</span><span>${esc(row.location) || "—"}</span></div>
      <div class="d-row"><span class="k">Source</span><span class="src">${esc(row.source)}</span></div>
      <div class="d-row"><span class="k">Posted</span><span title="${fullDate(row.posted_at)}">${shortDate(row.posted_at) || "—"} <span style="color:var(--muted)">(${ago(row.posted_at)})</span></span></div>
      ${row.salary ? `<div class="d-row"><span class="k">Salary</span><span>${esc(row.salary)}</span></div>` : ""}
      ${row.tags ? `<div class="d-row"><span class="k">Tags</span><span>${row.tags.split ? row.tags.split("|").map((t) => `<span class="tag">${esc(t)}</span>`).join("") : ""}</span></div>` : ""}
      <div class="d-body">${esc(row.description || "No description collected.")}</div>`;
  } else {
    meta = `
      <div class="d-row"><span class="k">Category</span><span>${esc(row.category) || "—"}</span></div>
      <div class="d-row"><span class="k">Source</span><span class="src">${esc(row.source)}</span></div>
      <div class="d-row"><span class="k">Published</span><span title="${fullDate(row.published_at)}">${shortDate(row.published_at) || "—"} <span style="color:var(--muted)">(${ago(row.published_at)})</span></span></div>
      ${row.author ? `<div class="d-row"><span class="k">Author</span><span>${esc(row.author)}</span></div>` : ""}
      <div class="d-body">${esc(row.summary || row.content || "No summary collected.")}</div>`;
  }
  d.innerHTML = `<button class="close-x" id="closeX" title="Close (Esc)">✕</button>
    <div class="d-head">${status}</div>
    <div class="d-title">${esc(row.title) || "(untitled)"}</div>
    <div class="d-meta">${meta}</div>
    <div class="d-actions">
      <a class="open-btn" href="${esc(row.url)}" target="_blank" rel="noopener">Open original ↗</a>
      <button class="open-btn ghost" id="copyLink">Copy link</button>
    </div>`;
  $("scrim").classList.add("open");
  d.classList.add("open");
  $("closeX").addEventListener("click", closeDrawer);
  $("copyLink").addEventListener("click", () => {
    const url = row.url || "";
    const done = () => toast("Link copied to clipboard");
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(done, done);
    else done();
  });
}
function closeDrawer() {
  $("scrim").classList.remove("open");
  $("drawer").classList.remove("open");
}

/* ---------------------------------------------------------------- reader */
const R = {
  feeds: (DATA.readerFeeds || []).map((f) => Object.assign({}, f)),
  items: DATA.items,
  filter: "all", source: "", sel: -1, q: "",
  api: false, navCount: null,
};

function readerVisible() {
  let rows = R.items;
  if (R.source) rows = rows.filter((i) => i.source === R.source);
  if (R.filter === "unread") rows = rows.filter((i) => !i.read);
  if (R.filter === "starred") rows = rows.filter((i) => i.starred);
  if (R.q) {
    const q = R.q.toLowerCase();
    rows = rows.filter((i) => (i.title + " " + (i.summary || "") + " " + (i.author || "")).toLowerCase().includes(q));
  }
  return rows;
}
function readerUnreadTotal() {
  return R.items.reduce((n, i) => n + (i.read ? 0 : 1), 0);
}
function readerPersist(kind, it, val) {
  const key = kind === "read" ? "read" : "starred";
  if (R.api) {
    const body = { source: it.source, url: it.url };
    body[key] = val;
    fetch("/api/" + kind, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
  } else {
    try {
      const k = "jc-" + kind + ":" + it.source + ":" + it.url;
      if (val) localStorage.setItem(k, "1"); else localStorage.removeItem(k);
    } catch (e) {}
  }
}
function renderReaderFeeds() {
  const unread = readerUnreadTotal();
  const starred = R.items.filter((i) => i.starred).length;
  const total = R.items.length;
  const sec = (label, n, filter) =>
    `<div class="r-fsec ${R.filter === filter && !R.source ? "active" : ""}" data-filter="${filter}" data-src="">
       <span>${label}</span><span class="badge">${n || ""}</span></div>`;
  $("rInbox").innerHTML = sec("All items", total, "all") + sec("Unread", unread, "unread") + sec("Starred", starred, "starred");
  $("rFeedsList").innerHTML = (R.feeds.length ? R.feeds.map((f) => {
    const active = R.source === f.source ? "active" : "";
    const hot = f.unread > 0 ? "hot" : "";
    return `<div class="r-feed ${active}" data-src="${esc(f.source)}">
              <span class="rdot" style="background:${f.unread ? "var(--accent)" : "transparent"}"></span>
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(f.name)}</span>
              <span class="badge ${hot}">${f.unread || ""}</span></div>`;
  }).join("") : `<div style="padding:8px 12px;color:var(--muted);font-size:12.5px">No feeds yet.<br>Run <b>jobcollect feed fetch</b>.</div>`);
  document.querySelectorAll("#rInbox .r-fsec, #rFeedsList .r-feed").forEach((el) =>
    el.addEventListener("click", () => {
      R.source = el.dataset.src || "";
      R.filter = el.dataset.filter || R.filter;
      R.sel = -1;
      renderReaderFeeds(); renderReaderList(); renderReaderArticle();
    })
  );
  if (R.navCount) R.navCount.textContent = unread || "";
}
function renderReaderList() {
  const v = readerVisible();
  if (R.sel >= v.length) R.sel = v.length - 1;
  $("rRows").innerHTML = v.length ? v.map((it, i) =>
    `<div class="r-row ${i === R.sel ? "active" : ""} ${it.read ? "read" : ""} ${it.starred ? "starred" : ""}" data-i="${i}">
       <span class="rdot"></span>
       <div style="min-width:0;flex:1">
         <div class="rt">${esc(it.title)}</div>
         <div class="rm">${esc(it.source.split(":").pop() || it.source)} · ${it.published_at ? ago(it.published_at) : ""}</div>
       </div>
       <span class="rstar" data-star="${i}">${it.starred ? "★" : "☆"}</span>
     </div>`).join("") : `<div class="r-placeholder" style="border:none;min-height:200px">Nothing here.</div>`;
  $("rRows").querySelectorAll(".r-row").forEach((el) =>
    el.addEventListener("click", () => {
      R.sel = +el.dataset.i;
      const it = readerVisible()[R.sel];
      if (it && !it.read) { it.read = true; readerPersist("read", it, true); }
      renderReaderFeeds(); renderReaderList(); renderReaderArticle();
    })
  );
  $("rRows").querySelectorAll(".rstar").forEach((el) =>
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const it = readerVisible()[+el.dataset.star];
      if (it) { it.starred = !it.starred; readerPersist("star", it, it.starred); renderReaderList(); renderReaderArticle(); }
    })
  );
  const active = $("rRows").querySelector(".r-row.active");
  if (active) active.scrollIntoView({ block: "nearest" });
}
function renderReaderArticle() {
  const el = $("rArticle");
  const it = readerVisible()[R.sel];
  if (!it) {
    el.innerHTML = `<div class="r-placeholder">📰<span>Select an article to read it here.</span></div>`;
    return;
  }
  const feedName = esc(it.source.split(":").pop() || it.source);
  const body = it.content || it.summary || "";
  let actions = `
    <button id="raStar" class="${it.starred ? "primary" : ""}">${it.starred ? "★ Starred" : "☆ Star"}</button>
    <button id="raRead">${it.read ? "Mark unread" : "Mark read"}</button>`;
  if (!it.full && R.api) actions += `<button id="raFull">Load full text</button>`;
  actions += `<a class="open-btn" href="${esc(it.url)}" target="_blank" rel="noopener" style="margin-left:auto">Open original ↗</a>`;
  el.innerHTML = `
    <div class="ra-title">${esc(it.title)}</div>
    <div class="ra-meta">
      <span class="src">${feedName}</span>
      ${it.category ? `<span class="tag">${esc(it.category)}</span>` : ""}
      ${it.author ? `<span>by ${esc(it.author)}</span>` : ""}
      <span class="dot-sep">·</span>
      <span title="${fullDate(it.published_at)}">${shortDate(it.published_at) || "—"} (${ago(it.published_at)})</span>
    </div>
    <div class="ra-actions">${actions}</div>
    <div class="ra-body" id="raBody">${esc(body || "No content collected for this item.")}</div>`;
  el.scrollTop = 0;
  $("raStar").addEventListener("click", () => {
    it.starred = !it.starred;
    readerPersist("star", it, it.starred);
    renderReaderFeeds(); renderReaderList(); renderReaderArticle();
  });
  $("raRead").addEventListener("click", () => {
    it.read = !it.read;
    readerPersist("read", it, it.read);
    renderReaderFeeds(); renderReaderList(); renderReaderArticle();
  });
  const full = $("raFull");
  if (full) full.addEventListener("click", () => readerLoadFulltext(it));
}
function readerMarkAll() {
  const v = readerVisible();
  const n = v.filter((i) => !i.read).length;
  v.forEach((it) => { it.read = true; });
  if (R.api) {
    fetch("/api/read_all", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source: R.source }) }).catch(() => {});
  }
  renderReaderFeeds(); renderReaderList(); renderReaderArticle();
  toast("Marked " + n + " read");
}
async function readerRefresh() {
  if (!R.api) { renderReaderFeeds(); renderReaderList(); renderReaderArticle(); return; }
  try {
    const res = await fetch("/api/data");
    const d = await res.json();
    if (d && d.ok) {
      R.feeds = d.feeds;
      R.items = d.items;
      R.sel = -1;
      renderReaderFeeds(); renderReaderList(); renderReaderArticle();
      toast("Refreshed");
    }
  } catch (e) {}
}
async function readerPing() {
  try {
    const res = await fetch("/api/ping");
    const d = await res.json();
    if (d && d.ok) { R.api = true; readerRefresh(); }
  } catch (e) {}
}
async function readerLoadFulltext(it) {
  const btn = $("raFull");
  if (btn) { btn.disabled = true; btn.textContent = "Loading…"; }
  try {
    const res = await fetch("/api/fulltext", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source: it.source, url: it.url }) });
    const d = await res.json();
    if (d && d.content) { it.content = d.content; it.full = true; const b = $("raBody"); if (b) b.textContent = d.content; toast("Full text loaded"); }
    else toast("Extraction failed: " + ((d && d.error) || "unknown"));
  } catch (e) { toast("Full text failed"); }
  renderReaderArticle();
}
function renderReader() {
  $("pageTitle").textContent = "Reader";
  $("crumbs").innerHTML = `<span>JobCollector</span><span class="sep">/</span><b>Reader</b>`;
  renderNav();
  $("view").innerHTML = `
    <div class="reader">
      <div class="r-feeds">
        <div class="r-nav-sec">Inbox</div>
        <div id="rInbox"></div>
        <div class="r-nav-sec">Feeds</div>
        <div id="rFeedsList"></div>
        <div class="r-feed-actions">
          <button id="rRefresh" title="Reload data (R)">↻ Refresh</button>
          <button id="rMarkAll" title="Mark all visible read">✓ Read all</button>
        </div>
      </div>
      <div class="r-list">
        <div class="r-list-head"><input id="rQ" placeholder="Search articles… ( / )"></div>
        <div class="r-rows" id="rRows"></div>
      </div>
      <div class="r-article" id="rArticle"></div>
    </div>`;
  $("rRefresh").addEventListener("click", () => { readerRefresh(); });
  $("rMarkAll").addEventListener("click", readerMarkAll);
  $("rQ").addEventListener("input", (e) => { R.q = e.target.value; R.sel = -1; renderReaderList(); renderReaderArticle(); });
  renderReaderFeeds();
  if (R.sel < 0) {
    const v = readerVisible();
    const firstUnread = v.findIndex((i) => !i.read);
    R.sel = firstUnread >= 0 ? firstUnread : (v.length ? 0 : -1);
  }
  renderReaderList();
  renderReaderArticle();
  readerPing();
}
function readerKey(e) {
  const k = e.key.toLowerCase();
  if (k === "j" || k === "k") {
    const v = readerVisible();
    if (!v.length) return true;
    R.sel = Math.max(0, Math.min(v.length - 1, R.sel + (k === "j" ? 1 : -1)));
    renderReaderList(); renderReaderArticle();
    return true;
  }
  if (k === "m") { const it = readerVisible()[R.sel]; if (it) { it.read = !it.read; readerPersist("read", it, it.read); renderReaderFeeds(); renderReaderList(); renderReaderArticle(); } return true; }
  if (k === "s") { const it = readerVisible()[R.sel]; if (it) { it.starred = !it.starred; readerPersist("star", it, it.starred); renderReaderList(); renderReaderArticle(); } return true; }
  if (k === "u") {
    R.filter = R.filter === "all" ? "unread" : R.filter === "unread" ? "starred" : "all";
    R.sel = -1;
    renderReaderFeeds(); renderReaderList(); renderReaderArticle();
    return true;
  }
  if (k === "r") { readerRefresh(); return true; }
  if (k === "o" || e.key === "Enter") { const it = readerVisible()[R.sel]; if (it && it.url) window.open(it.url, "_blank", "noopener"); return true; }
  return false;
}

/* ------------------------------------------------------------------ export */
function exportCsv() {
  const isJobs = state.page === "jobs";
  const rows = isJobs ? jobs() : items();
  const cols = isJobs
    ? ["title", "company", "location", "source", "url", "posted_at"]
    : ["title", "category", "source", "url", "published_at"];
  const Q = String.fromCharCode(34);
  const line = (r) => cols.map((c) => Q + String(r[c] || "").replace(new RegExp(Q, "g"), Q + Q) + Q).join(",");
  const NL = String.fromCharCode(10);
  const csv = [cols.join(",")].concat(rows.map(line)).join(NL);
  const a = document.createElement("a");
  a.href = "data:text/csv;charset=utf-8," + encodeURIComponent(csv);
  a.download = (isJobs ? "jobs" : "items") + "-filtered.csv";
  a.click();
  toast("Exported " + rows.length + " rows");
}

/* --------------------------------------------------------------- theme/ui */
function applyTheme() {
  const dark = document.documentElement.dataset.theme === "dark";
  $("themeBtn").textContent = dark ? "☀️" : "🌙";
  $("themeBtn").title = dark ? "Switch to light mode (D)" : "Switch to dark mode (D)";
  try { localStorage.setItem("jc-theme", dark ? "dark" : "light"); } catch (e) {}
}
function toggleTheme() {
  document.documentElement.dataset.theme =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme();
  toast(document.documentElement.dataset.theme === "dark" ? "Dark mode on" : "Light mode on");
}
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 2400);
}
function toggleHelp(force) {
  const h = $("help");
  const show = force === undefined ? h.classList.contains("hidden") : force;
  h.classList.toggle("hidden", !show);
}
function focusFilter() {
  if (state.page === "analytics") { $("sideQ").focus(); }
  else if (state.page === "reader") { const rq = $("rQ"); if (rq) rq.focus(); }
  else { const q = $("q"); if (q) { q.focus(); q.select(); } }
}
function cycleGroup() {
  if (state.page === "jobs") state.group = state.group === "source" ? "company" : "source";
  else state.group = state.group === "category" ? "source" : "category";
}

/* -------------------------------------------------------------------- main */
function render() {
  const isJobs = state.page === "jobs";
  const TITLES = { jobs: "Jobs", items: "Engine Items", reader: "Reader", analytics: "Analytics", companies: "Companies" };
  $("pageTitle").textContent = TITLES[state.page] || "Jobs";
  $("crumbs").innerHTML = `<span>JobCollector</span><span class="sep">/</span><b>${$("pageTitle").textContent}</b>`;
  renderNav();
  if (state.page === "reader" || state.page === "analytics" || state.page === "companies") {
    $("toolbar").innerHTML = "";
    $("toolbar").style.display = "none";
    if (state.page === "reader") { renderReader(); return; }
    if (state.page === "companies") { renderCompanies(); return; }
    renderAnalytics();
    return;
  }
  $("toolbar").style.display = "";
  renderToolbar();
  if (state.layout === "board") renderBoard();
  else renderTable();
}

/* ------------------------------------------------------------- init/events */
$("sideQ").addEventListener("input", (e) => { state.sideQ = e.target.value; render(); });
$("scrim").addEventListener("click", closeDrawer);
$("themeBtn").addEventListener("click", toggleTheme);
$("helpBtn").addEventListener("click", () => toggleHelp());
$("helpClose").addEventListener("click", () => toggleHelp(false));
$("collapseBtn").addEventListener("click", () => {
  const app = $("app");
  app.classList.toggle("side-collapsed");
  $("collapseBtn").textContent = app.classList.contains("side-collapsed") ? "»" : "«";
  try { localStorage.setItem("jc-collapsed", app.classList.contains("side-collapsed") ? "1" : "0"); } catch (e) {}
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { if (!$("help").classList.contains("hidden")) toggleHelp(false); else closeDrawer(); return; }
  const tag = (e.target.tagName || "").toLowerCase();
  const typing = tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable;
  if (typing) return;
  if (e.key === "?") { toggleHelp(); return; }
  if (state.page === "reader" && readerKey(e)) return;
  switch (e.key.toLowerCase()) {
    case "/": e.preventDefault(); focusFilter(); break;
    case "t": state.layout = "table"; render(); break;
    case "b": state.layout = "board"; render(); break;
    case "g": cycleGroup(); render(); break;
    case "d": toggleTheme(); break;
    case "1": state.page = "jobs"; state.sel = null; render(); break;
    case "2": state.page = "items"; state.sel = null; render(); break;
    case "3": state.page = "analytics"; state.sel = null; render(); break;
    case "4": state.page = "companies"; state.sel = null; render(); break;
    case "4": state.page = "reader"; state.sel = null; render(); break;
    case "e": exportCsv(); break;
  }
});
// sticky toolbar shadow on scroll
const mainEl = document.querySelector(".main");
mainEl.addEventListener("scroll", () => {
  const tb = $("toolbar");
  if (tb) tb.classList.toggle("pinned", mainEl.scrollTop > 8);
});
// restore collapsed sidebar
try {
  if (localStorage.getItem("jc-collapsed") === "1") {
    $("app").classList.add("side-collapsed");
    $("collapseBtn").textContent = "»";
  }
} catch (e) {}
$("generated").textContent = "Last refreshed " + DATA.generated;
applyTheme();
render();
watchPing();
</script>
</body>
</html>
"""


def _watchlist_payload(store: Store) -> list[dict]:
    """Watchlist rows with match counts, computed in one pass over active jobs.

    Single-entry counts use the same semantics as store.count_matches (SQL
    LIKE), just batched so 1000+ entries stay fast.
    """
    items = store.watchlist_all()
    counts = store.watchlist_counts()
    return [
        {"id": w["id"], "kind": w["kind"], "value": w["value"],
         "count": counts.get(w["id"], 0)}
        for w in items
    ]


def render_dashboard(store: Store, out_path: str | Path, limit: int = 1000) -> int:
    rows = store.search(limit=limit)  # newest first
    s = store.stats()
    runs = store.conn.execute(
        "SELECT started_at, jobs_seen, jobs_new, jobs_expired FROM runs ORDER BY id"
    ).fetchall()
    runs_payload = [
        {
            "at": r["started_at"],
            "seen": r["jobs_seen"],
            "new": r["jobs_new"],
            "expired": r["jobs_expired"],
        }
        for r in runs
    ][-24:]
    item_stats = store.items_stats()
    item_rows = store.search_items(limit=1000)
    unread = store.unread_totals()
    feed_by_source = {f["source"]: f for f in store.feed_meta()}
    reader_feeds = []
    for src, total in (item_stats.get("by_source") or {}).items():
        meta = feed_by_source.get(src, {})
        reader_feeds.append({
            "source": src,
            "name": meta.get("name") or src.split(":", 1)[-1],
            "category": meta.get("category") or ("" if src.startswith("rss:") else "scraped"),
            "url": meta.get("url", ""),
            "unread": unread.get(src, 0),
            "total": total,
        })
    reader_feeds.sort(key=lambda f: (-f["unread"], f["name"].lower()))
    by_company: dict[str, int] = {}
    for r in rows:
        by_company[r["company"]] = by_company.get(r["company"], 0) + 1
    payload = {
        "generated": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "stats": {
            "total": s["total"],
            "active": s["active"],
            "expired": s["total"] - s["active"],
            "seen_last_run": runs_payload[-1]["seen"] if runs_payload else 0,
            "sources": len(s["by_source"]),
            "by_source": s["by_source"],
            "by_company": by_company,
        },
        "itemStats": {
            "total": item_stats["total"],
            "by_source": item_stats.get("by_source") or {},
            "by_category": item_stats.get("by_category") or {},
        },
        "jobsTotal": s["total"],
        "itemTotal": item_stats["total"],
        "readerFeeds": reader_feeds,
        "readerUnread": sum(unread.values()),
        "watchlist": _watchlist_payload(store),
        "runs": runs_payload,
        "lastRun": runs_payload[-1] if runs_payload else None,
        "jobs": [
            {
                "title": r["title"],
                "company": r["company"],
                "location": r["location"],
                "url": r["url"],
                "source": r["source"],
                "source_kind": r["source_kind"],
                "tags": r["tags"],
                "salary": r["salary"],
                "posted_at": r["posted_at"],
                "first_seen_at": r["first_seen_at"],
                "description": (r["description"] or "")[:800],
                "is_active": bool(r["is_active"]),
            }
            for r in rows
        ],
        "items": [
            {
                "title": r["title"],
                "category": r["category"],
                "url": r["url"],
                "source": r["source"],
                "summary": (r["summary"] or "")[:500],
                "content": (r["content"] or "")[:1500],
                "author": r["author"],
                "published_at": r["published_at"],
                "read": bool(r["read"]),
                "starred": bool(r["starred"]),
                "full": (r["content"] or "") != (r["summary"] or ""),
            }
            for r in item_rows
        ],
    }
    html = TEMPLATE.replace("{DATA}", json.dumps(payload, ensure_ascii=False))
    Path(out_path).write_text(html, encoding="utf-8")
    return len(rows)
