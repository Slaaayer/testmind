"""
Enhanced HTML dashboard focused on computed test metrics.

Produces a single self-contained HTML file with:
- Per-project summary + pass-rate trend chart
- Sortable/searchable test table: stability, fail rate, flip rate, prediction, reruns, sparkline
- Click-to-open test detail panel with full metrics + history chart
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from testmind.analysis.flaky import FlakyDetector
from testmind.analysis.predictor import FailurePredictor
from testmind.analysis.regression import RegressionDetector
from testmind.analysis.stability import StabilityAnalyzer
from testmind.domain.models import TestResult, TestStatus
from testmind.storage.base import Store

_CHART_JS = "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"

_STATUS_CODE: dict[TestStatus, str] = {
    TestStatus.PASSED:  "P",
    TestStatus.FAILED:  "F",
    TestStatus.ERROR:   "E",
    TestStatus.SKIPPED: "S",
    TestStatus.UNKNOWN: "?",
}


def render_dashboard(store: Store, projects: list[str]) -> str:
    """Return a complete HTML string for the given projects."""
    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data = {
        "generated_at": generated_at,
        "projects": [_build_project_data(project, store) for project in projects],
    }
    data_json = json.dumps(data, default=str)
    return _HTML_TEMPLATE.replace("__DATA_JSON__", data_json)


# ---------------------------------------------------------------------------
# Data builders
# ---------------------------------------------------------------------------


def _build_project_data(project: str, store: Store) -> dict:
    reports = store.get_reports(project, limit=30)

    # Build per-test history from already-loaded reports (no extra queries).
    # reports is newest-first; reverse for oldest-first ordering required by analyzers.
    ordered = list(reversed(reports))

    history_map: dict[str, list[tuple[datetime, TestResult]]] = {}
    for report in ordered:
        for test in report.tests:
            history_map.setdefault(test.name, []).append((report.timestamp, test))

    flaky_det = FlakyDetector()
    regr_det  = RegressionDetector()
    stab_det  = StabilityAnalyzer()
    pred_det  = FailurePredictor()

    tests_data: list[dict] = []
    for name, history in sorted(history_map.items()):
        fr = flaky_det.analyze(name, history)
        rr = regr_det.analyze(name, history)
        sr = stab_det.analyze(name, history)
        pr = pred_det.analyze(name, history)

        hist_entries = [
            {
                "date":      ts.strftime("%m-%d"),
                "full_date": ts.strftime("%Y-%m-%d %H:%M"),
                "status":    _STATUS_CODE.get(result.status, "?"),
                "duration":  round(result.duration, 2),
                "reruns":    result.rerun_count,
            }
            for ts, result in history
        ]

        tests_data.append({
            "name":         name,
            "stability":    round(sr.score, 1)                    if not sr.insufficient_data else None,
            "fail_rate":    round(fr.fail_rate * 100, 1)          if not fr.insufficient_data else None,
            "flip_rate":    round(fr.flip_rate * 100, 1)          if not fr.insufficient_data else None,
            "prediction":   round(pr.failure_probability * 100, 1) if not pr.insufficient_data else None,
            "trend":        pr.trend.value                         if not pr.insufficient_data else "stable",
            "confidence":   round(pr.confidence * 100, 0)         if not pr.insufficient_data else 0,
            "reruns":       sum(e["reruns"] for e in hist_entries),
            "run_count":    len(history),
            "is_flaky":     fr.is_flaky,
            "is_regression": rr.is_regression,
            "insufficient": sr.insufficient_data,
            "history":      hist_entries,
        })

    # Default sort: worst stability first; insufficient-data tests last
    tests_data.sort(key=lambda t: (t["stability"] is None, t["stability"] or 100.0))

    if reports:
        latest = reports[0]
        summary = {
            "total_tests":    len(tests_data),
            "failing_tests":  latest.failed + latest.errors,
            "flaky_tests":    sum(1 for t in tests_data if t["is_flaky"]),
            "degrading_tests": sum(1 for t in tests_data if t["trend"] == "degrading"),
            "total_reruns":   sum(t["reruns"] for t in tests_data),
            "last_run":       latest.timestamp.strftime("%Y-%m-%d %H:%M"),
            "pass_rate":      round(latest.pass_rate * 100, 1),
        }
        report_trend = {
            "labels":     [r.timestamp.strftime("%m-%d") for r in ordered],
            "pass_rates": [round(r.pass_rate * 100, 1) for r in ordered],
        }
    else:
        summary = {
            "total_tests": 0, "failing_tests": 0, "flaky_tests": 0,
            "degrading_tests": 0, "total_reruns": 0, "last_run": "—", "pass_rate": 0,
        }
        report_trend = {"labels": [], "pass_rates": []}

    return {
        "id":           _safe_id(project),
        "name":         project,
        "summary":      summary,
        "report_trend": report_trend,
        "tests":        tests_data,
    }


def _safe_id(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s)


# ---------------------------------------------------------------------------
# HTML template — placeholder __DATA_JSON__ is replaced, not format()-escaped
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TestMind Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;font-size:13px}
/* ── header ── */
header{background:#1e293b;border-bottom:1px solid #334155;padding:14px 24px;display:flex;align-items:center;gap:16px}
header h1{font-size:17px;font-weight:700;letter-spacing:.3px;flex:1}
header .meta{font-size:11px;color:#64748b}
/* ── project tabs ── */
.tabs{display:flex;gap:4px;padding:16px 24px 0;border-bottom:1px solid #334155}
.tab{padding:8px 16px;border-radius:6px 6px 0 0;cursor:pointer;font-size:12px;font-weight:500;color:#94a3b8;border:1px solid transparent;border-bottom:none;background:transparent}
.tab:hover{color:#e2e8f0}
.tab.active{background:#1e293b;border-color:#334155;color:#f1f5f9}
/* ── project pane ── */
.project-pane{display:none;padding:20px 24px}
.project-pane.active{display:block}
/* ── summary cards ── */
.cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px}
.card{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:14px 18px;min-width:130px;flex:1}
.card .val{font-size:22px;font-weight:700;margin-bottom:2px}
.card .lbl{font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px}
.card.highlight .val{color:#22c55e}
.card.warn .val{color:#f59e0b}
.card.danger .val{color:#ef4444}
.card.purple .val{color:#a78bfa}
/* ── two-column layout ── */
.main-row{display:flex;gap:20px;flex-wrap:wrap}
.trend-col{flex:0 0 320px}
.trend-col canvas{max-height:200px}
.chart-box{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:14px}
.chart-box h3{font-size:11px;font-weight:600;color:#64748b;text-transform:uppercase;margin-bottom:10px}
.table-col{flex:1 1 500px;overflow:hidden}
/* ── search + filter ── */
.table-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.search-box{flex:1;background:#1e293b;border:1px solid #334155;border-radius:6px;padding:6px 10px;color:#e2e8f0;font-size:12px;outline:none}
.search-box:focus{border-color:#60a5fa}
.filter-btn{padding:5px 10px;border-radius:5px;border:1px solid #334155;background:#1e293b;color:#94a3b8;cursor:pointer;font-size:11px}
.filter-btn.on{background:#1d4ed8;border-color:#3b82f6;color:#fff}
/* ── test table ── */
.tbl-wrap{overflow-x:auto;max-height:420px;overflow-y:auto;border:1px solid #334155;border-radius:8px}
table{width:100%;border-collapse:collapse;font-size:12px}
thead th{background:#1e293b;padding:7px 10px;text-align:left;font-size:10px;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.3px;position:sticky;top:0;cursor:pointer;user-select:none;white-space:nowrap}
thead th:hover{color:#e2e8f0}
thead th .sort-arrow{margin-left:4px;opacity:.4;font-size:9px}
thead th.sorted .sort-arrow{opacity:1}
tbody tr{border-top:1px solid #1e293b;cursor:pointer}
tbody tr:hover td{background:#1e293b}
tbody tr.selected td{background:#1e3a5f!important}
td{padding:6px 10px;vertical-align:middle}
.td-name{font-family:monospace;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.td-name .rbadge{font-size:9px;background:#6d28d9;color:#fff;border-radius:3px;padding:1px 3px;margin-left:4px;vertical-align:middle}
/* ── value cells ── */
.v-good{color:#22c55e;font-weight:600}
.v-warn{color:#f59e0b;font-weight:600}
.v-bad{color:#ef4444;font-weight:600}
.v-dim{color:#475569}
.v-flag{font-size:10px;font-weight:700;padding:2px 5px;border-radius:3px}
.v-flag.flaky{background:#7c3aed22;color:#a78bfa}
.v-flag.regr{background:#dc262622;color:#f87171}
/* ── sparkline ── */
.spark{display:flex;gap:2px;align-items:center}
.spark-dot{width:8px;height:8px;border-radius:2px;flex-shrink:0}
.spark-dot.P{background:#22c55e}
.spark-dot.F{background:#ef4444}
.spark-dot.E{background:#f97316}
.spark-dot.S{background:#334155}
.spark-dot.unknown{background:#475569}
/* ── trend arrow ── */
.trend-up{color:#22c55e}
.trend-down{color:#ef4444}
.trend-flat{color:#64748b}
/* ── detail panel ── */
#detail-panel{display:none;margin-top:20px;background:#1e293b;border:1px solid #334155;border-radius:10px;padding:20px}
#detail-panel.visible{display:block}
.detail-header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:16px}
.detail-name{font-family:monospace;font-size:13px;color:#f1f5f9;word-break:break-all;flex:1;margin-right:12px}
.close-btn{background:transparent;border:none;color:#64748b;cursor:pointer;font-size:18px;line-height:1;padding:0 4px}
.close-btn:hover{color:#e2e8f0}
.metrics-grid{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px}
.metric{background:#0f172a;border:1px solid #334155;border-radius:6px;padding:10px 14px;min-width:110px;flex:1}
.metric .m-val{font-size:18px;font-weight:700;margin-bottom:2px}
.metric .m-lbl{font-size:9px;color:#64748b;text-transform:uppercase;letter-spacing:.4px}
.metric.good .m-val{color:#22c55e}
.metric.warn .m-val{color:#f59e0b}
.metric.bad .m-val{color:#ef4444}
.metric.purple .m-val{color:#a78bfa}
.metric.dim .m-val{color:#475569}
/* ── detail sparkline ── */
.detail-spark{display:flex;flex-wrap:wrap;gap:3px;margin-bottom:16px}
.detail-spark-dot{width:16px;height:16px;border-radius:3px;position:relative;cursor:default}
.detail-spark-dot:hover::after{content:attr(title);position:absolute;bottom:22px;left:50%;transform:translateX(-50%);background:#0f172a;border:1px solid #334155;border-radius:4px;padding:4px 8px;white-space:nowrap;font-size:10px;color:#e2e8f0;z-index:10;pointer-events:none}
/* ── detail chart + history table ── */
.detail-body{display:flex;gap:20px;flex-wrap:wrap}
.detail-chart-col{flex:0 0 360px}
.detail-chart-col canvas{max-height:180px}
.detail-hist-col{flex:1 1 300px;overflow-x:auto}
.hist-table{width:100%;border-collapse:collapse;font-size:11px}
.hist-table th{background:#0f172a;padding:5px 8px;text-align:left;color:#64748b;font-size:10px;text-transform:uppercase;border-bottom:1px solid #334155}
.hist-table td{padding:5px 8px;border-bottom:1px solid #1e293b}
</style>
</head>
<body>

<header>
  <h1>🧠 TestMind Dashboard</h1>
  <span class="meta" id="gen-at"></span>
</header>

<div class="tabs" id="tabs"></div>

<div id="panes"></div>

<script>
const DATA = __DATA_JSON__;

document.getElementById('gen-at').textContent = 'Generated ' + DATA.generated_at;

// ── helpers ──────────────────────────────────────────────────────────────────

function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function fmt(v, unit){ return v === null ? '<span class="v-dim">—</span>' : esc(v) + (unit||''); }

function stabilityClass(v){
  if (v === null) return 'v-dim';
  return v >= 80 ? 'v-good' : v >= 60 ? 'v-warn' : 'v-bad';
}
function failClass(v){
  if (v === null) return 'v-dim';
  return v === 0 ? 'v-good' : v < 20 ? 'v-warn' : 'v-bad';
}
function predClass(v){
  if (v === null) return 'v-dim';
  return v < 10 ? 'v-good' : v < 30 ? 'v-warn' : 'v-bad';
}
function flipClass(v){
  if (v === null) return 'v-dim';
  return v === 0 ? 'v-good' : v < 15 ? 'v-warn' : 'v-bad';
}
function trendArrow(t){
  if (t === 'degrading')  return '<span class="trend-down">↓</span>';
  if (t === 'improving')  return '<span class="trend-up">↑</span>';
  return '<span class="trend-flat">→</span>';
}
function sparkline(history, size){
  size = size || 8;
  return history.map(function(h){
    var cls = 'spark-dot ' + (h.status || 'unknown');
    return '<span class="spark-dot ' + h.status + '" title="' + esc(h.full_date) + ' · ' + h.status + '"></span>';
  }).join('');
}
function metricCard(val, label, cls){
  return '<div class="metric ' + cls + '"><div class="m-val">' + val + '</div><div class="m-lbl">' + label + '</div></div>';
}

// ── chart instances ───────────────────────────────────────────────────────────

var charts = {};
function destroyChart(id){ if (charts[id]){ charts[id].destroy(); delete charts[id]; } }
function makeChart(canvasId, labels, datasets, yMin, yMax, tickSuffix){
  destroyChart(canvasId);
  var ctx = document.getElementById(canvasId);
  if (!ctx) return;
  charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: { labels: labels, datasets: datasets },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: { legend: { display: datasets.length > 1, labels: { color:'#94a3b8', font:{size:10} } } },
      scales: {
        y: { min: yMin, max: yMax,
             grid: { color:'#1e293b' },
             ticks: { color:'#94a3b8', callback: function(v){ return v + (tickSuffix||''); } } },
        x: { grid: { color:'#1e293b' }, ticks: { color:'#94a3b8' } }
      }
    }
  });
}

// ── tabs ─────────────────────────────────────────────────────────────────────

var activeProject = null;

function buildTabs(){
  var tabsEl = document.getElementById('tabs');
  if (DATA.projects.length <= 1) { tabsEl.style.display='none'; return; }
  DATA.projects.forEach(function(p, i){
    var t = document.createElement('div');
    t.className = 'tab' + (i===0 ? ' active' : '');
    t.textContent = p.name;
    t.dataset.pid = p.id;
    t.addEventListener('click', function(){ switchProject(p.id); });
    tabsEl.appendChild(t);
  });
}

function switchProject(pid){
  document.querySelectorAll('.tab').forEach(function(t){ t.classList.toggle('active', t.dataset.pid===pid); });
  document.querySelectorAll('.project-pane').forEach(function(p){ p.classList.toggle('active', p.id==='pane-'+pid); });
  activeProject = pid;
}

// ── project pane builder ──────────────────────────────────────────────────────

function buildAllPanes(){
  var container = document.getElementById('panes');
  DATA.projects.forEach(function(p, idx){
    var pane = document.createElement('div');
    pane.className = 'project-pane' + (idx===0 ? ' active' : '');
    pane.id = 'pane-' + p.id;
    pane.innerHTML = buildPane(p);
    container.appendChild(pane);
    // wire up table interactions after DOM insertion
    initTable(p);
  });
}

function buildPane(p){
  var s = p.summary;
  var passColor = s.pass_rate >= 95 ? 'highlight' : s.pass_rate >= 80 ? 'warn' : 'danger';
  var cards = [
    ['card ' + passColor, s.pass_rate + '%',   'Pass rate'],
    ['card danger',        s.failing_tests,      'Failing now'],
    ['card warn',          s.flaky_tests,        'Flaky'],
    ['card danger',        s.degrading_tests,    'Degrading'],
    ['card purple',        s.total_reruns,       'Total reruns'],
    ['card',               s.total_tests,        'Tests tracked'],
    ['card',               'Last: ' + s.last_run,''],
  ].map(function(c){
    return '<div class="' + c[0] + '"><div class="val">' + c[1] + '</div><div class="lbl">' + c[2] + '</div></div>';
  }).join('');

  var tableId = 'tbl-' + p.id;
  var trendId = 'trend-' + p.id;
  var detailId = 'detail-' + p.id;

  return [
    '<div class="cards">' + cards + '</div>',
    '<div class="main-row">',
    '  <div class="trend-col">',
    '    <div class="chart-box"><h3>Pass rate trend</h3><canvas id="' + trendId + '"></canvas></div>',
    '  </div>',
    '  <div class="table-col">',
    '    <div class="table-toolbar">',
    '      <input class="search-box" id="search-' + p.id + '" placeholder="Filter tests…" type="search">',
    '      <button class="filter-btn" id="fltr-flaky-' + p.id + '" onclick="toggleFilter(this,\'' + p.id + '\',\'flaky\')">Flaky only</button>',
    '      <button class="filter-btn" id="fltr-bad-' + p.id + '" onclick="toggleFilter(this,\'' + p.id + '\',\'bad\')">Issues only</button>',
    '    </div>',
    '    <div class="tbl-wrap">',
    '      <table id="' + tableId + '">',
    '        <thead><tr>',
    '          <th data-col="name"     data-pid="' + p.id + '">Test <span class="sort-arrow">⇅</span></th>',
    '          <th data-col="stability" data-pid="' + p.id + '">Stability <span class="sort-arrow">⇅</span></th>',
    '          <th data-col="fail_rate" data-pid="' + p.id + '">Fail rate <span class="sort-arrow">⇅</span></th>',
    '          <th data-col="flip_rate" data-pid="' + p.id + '">Flip rate <span class="sort-arrow">⇅</span></th>',
    '          <th data-col="prediction" data-pid="' + p.id + '">Prediction <span class="sort-arrow">⇅</span></th>',
    '          <th data-col="reruns"   data-pid="' + p.id + '">Reruns <span class="sort-arrow">⇅</span></th>',
    '          <th data-col="run_count" data-pid="' + p.id + '">Runs <span class="sort-arrow">⇅</span></th>',
    '          <th>Trend</th>',
    '        </tr></thead>',
    '        <tbody id="tbody-' + p.id + '"></tbody>',
    '      </table>',
    '    </div>',
    '  </div>',
    '</div>',
    '<div id="' + detailId + '" class="detail-panel"></div>',
  ].join('\n');
}

// ── table logic ───────────────────────────────────────────────────────────────

var tableState = {};  // pid → { sort, dir, filter, flaky, bad, selected }

function initTable(p){
  tableState[p.id] = { sort:'stability', dir:'asc', filter:'', flaky:false, bad:false, selected:null };

  // trend chart
  var trend = p.report_trend;
  makeChart('trend-' + p.id, trend.labels, [{
    label:'Pass %', data: trend.pass_rates,
    borderColor:'#22c55e', backgroundColor:'rgba(34,197,94,.1)', fill:true, tension:.3, pointRadius:3
  }], 0, 100, '%');

  // sort header click
  document.querySelectorAll('th[data-pid="' + p.id + '"]').forEach(function(th){
    th.addEventListener('click', function(){
      var col = th.dataset.col;
      var st = tableState[p.id];
      if (st.sort === col) { st.dir = st.dir==='asc' ? 'desc' : 'asc'; }
      else { st.sort = col; st.dir = col==='name' ? 'asc' : 'asc'; }
      // update arrow visibility
      document.querySelectorAll('th[data-pid="' + p.id + '"]').forEach(function(t){
        t.classList.toggle('sorted', t.dataset.col===col);
      });
      renderTable(p);
    });
  });

  // search
  document.getElementById('search-' + p.id).addEventListener('input', function(e){
    tableState[p.id].filter = e.target.value.toLowerCase();
    renderTable(p);
  });

  renderTable(p);
}

function toggleFilter(btn, pid, type){
  tableState[pid][type] = !tableState[pid][type];
  btn.classList.toggle('on', tableState[pid][type]);
  var pdata = DATA.projects.find(function(p){ return p.id===pid; });
  renderTable(pdata);
}

function renderTable(p){
  var st = tableState[p.id];
  var tests = p.tests.slice();

  // filter
  if (st.filter) tests = tests.filter(function(t){ return t.name.toLowerCase().includes(st.filter); });
  if (st.flaky)  tests = tests.filter(function(t){ return t.is_flaky; });
  if (st.bad)    tests = tests.filter(function(t){ return (t.fail_rate||0)>0 || t.is_flaky || t.is_regression; });

  // sort
  var col = st.sort, dir = st.dir;
  tests.sort(function(a,b){
    var av = a[col], bv = b[col];
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    var cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return dir==='asc' ? cmp : -cmp;
  });

  var tbody = document.getElementById('tbody-' + p.id);
  tbody.innerHTML = tests.map(function(t){
    var spark = t.history.slice(-12).map(function(h){
      return '<span class="spark-dot ' + h.status + '" title="' + esc(h.full_date) + '"></span>';
    }).join('');
    var rb = t.reruns > 0 ? '<span class="rbadge">' + t.reruns + '↺</span>' : '';
    var flags = '';
    if (t.is_flaky)      flags += '<span class="v-flag flaky">FLAKY</span> ';
    if (t.is_regression) flags += '<span class="v-flag regr">REGR</span>';

    return [
      '<tr data-name="' + esc(t.name) + '" data-pid="' + p.id + '" class="' + (st.selected===t.name ? 'selected':'') + '">',
      '<td class="td-name" title="' + esc(t.name) + '">' + esc(t.name) + rb + ' ' + flags + '</td>',
      '<td class="' + stabilityClass(t.stability) + '">' + (t.stability !== null ? t.stability : '<span class="v-dim">—</span>') + '</td>',
      '<td class="' + failClass(t.fail_rate) + '">' + (t.fail_rate !== null ? t.fail_rate+'%' : '<span class="v-dim">—</span>') + '</td>',
      '<td class="' + flipClass(t.flip_rate) + '">' + (t.flip_rate !== null ? t.flip_rate+'%' : '<span class="v-dim">—</span>') + '</td>',
      '<td class="' + predClass(t.prediction) + '">' + (t.prediction !== null ? t.prediction+'% '+trendArrow(t.trend) : '<span class="v-dim">—</span>') + '</td>',
      '<td class="' + (t.reruns > 0 ? 'v-flag flaky' : 'v-dim') + '">' + (t.reruns || '—') + '</td>',
      '<td class="v-dim">' + t.run_count + '</td>',
      '<td><div class="spark">' + spark + '</div></td>',
      '</tr>',
    ].join('');
  }).join('');

  // row click → detail
  tbody.querySelectorAll('tr').forEach(function(tr){
    tr.addEventListener('click', function(){
      var name = tr.dataset.name, pid = tr.dataset.pid;
      var pdata = DATA.projects.find(function(p){ return p.id===pid; });
      var test  = pdata.tests.find(function(t){ return t.name===name; });
      tbody.querySelectorAll('tr').forEach(function(r){ r.classList.remove('selected'); });
      var panel = document.getElementById('detail-' + pid);
      if (tableState[pid].selected === name){
        tableState[pid].selected = null;
        panel.className = 'detail-panel';
        panel.innerHTML = '';
        destroyChart('detail-chart-' + pid);
      } else {
        tableState[pid].selected = name;
        tr.classList.add('selected');
        showDetail(test, pid);
      }
    });
  });
}

// ── detail panel ─────────────────────────────────────────────────────────────

function showDetail(test, pid){
  var panelId = 'detail-' + pid;
  var chartId = 'detail-chart-' + pid;
  destroyChart(chartId);

  var stab  = test.stability !== null ? test.stability : null;
  var fail  = test.fail_rate  !== null ? test.fail_rate  + '%' : null;
  var flip  = test.flip_rate  !== null ? test.flip_rate  + '%' : null;
  var pred  = test.prediction !== null ? test.prediction + '%' : null;

  var metrics = [
    metricCard(stab !== null ? stab : '—', 'Stability /100', stab===null?'dim':stab>=80?'good':stab>=60?'warn':'bad'),
    metricCard(fail !== null ? fail : '—', 'Fail rate',      fail===null?'dim':test.fail_rate===0?'good':test.fail_rate<20?'warn':'bad'),
    metricCard(flip !== null ? flip : '—', 'Flip rate',      flip===null?'dim':test.flip_rate===0?'good':test.flip_rate<15?'warn':'bad'),
    metricCard(pred !== null ? pred + ' ' + trendArrow(test.trend) : '—', 'Failure prediction', pred===null?'dim':test.prediction<10?'good':test.prediction<30?'warn':'bad'),
    metricCard(test.is_flaky ? 'YES' : 'No',       'Flaky',      test.is_flaky?'bad':'good'),
    metricCard(test.is_regression ? 'YES' : 'No',  'Regression', test.is_regression?'bad':'good'),
    metricCard(test.reruns || '0', 'Total reruns', test.reruns>0?'purple':'dim'),
    metricCard(test.run_count,     'Runs tracked', 'dim'),
  ].join('');

  // full sparkline
  var fullSpark = test.history.map(function(h){
    return '<span class="detail-spark-dot ' + h.status + '" title="' + esc(h.full_date) + ' · ' + h.status + (h.reruns?' ('+h.reruns+'↺)':'') + ' · '+h.duration+'s"></span>';
  }).join('');

  // history table
  var histRows = test.history.slice().reverse().map(function(h){
    var statusCls = {P:'v-good',F:'v-bad',E:'v-bad',S:'v-dim'}[h.status]||'v-dim';
    var statusLabel = {P:'PASSED',F:'FAILED',E:'ERROR',S:'SKIPPED'}[h.status]||h.status;
    return '<tr><td>' + esc(h.full_date) + '</td><td class="' + statusCls + '">' + statusLabel + '</td><td>' + h.duration + 's</td><td>' + (h.reruns||'—') + '</td></tr>';
  }).join('');

  var panel = document.getElementById(panelId);
  panel.className = 'detail-panel visible';
  panel.innerHTML = [
    '<div class="detail-header">',
    '  <div class="detail-name">' + esc(test.name) + '</div>',
    '  <button class="close-btn" onclick="closeDetail(\'' + pid + '\')">✕</button>',
    '</div>',
    '<div class="metrics-grid">' + metrics + '</div>',
    '<div class="detail-spark">' + fullSpark + '</div>',
    '<div class="detail-body">',
    '  <div class="detail-chart-col">',
    '    <div class="chart-box"><h3>Fail rate over time</h3><canvas id="' + chartId + '"></canvas></div>',
    '  </div>',
    '  <div class="detail-hist-col">',
    '    <table class="hist-table">',
    '      <thead><tr><th>Date</th><th>Status</th><th>Duration</th><th>Reruns</th></tr></thead>',
    '      <tbody>' + histRows + '</tbody>',
    '    </table>',
    '  </div>',
    '</div>',
  ].join('');

  panel.scrollIntoView({ behavior:'smooth', block:'nearest' });

  // chart: fail rate over time (1=fail/error, 0=pass, 0.5=skip)
  var failValues = test.history.map(function(h){ return h.status==='F'||h.status==='E' ? 1 : h.status==='S' ? null : 0; });
  var rerunValues = test.history.map(function(h){ return h.reruns > 0 ? h.reruns : null; });
  var labels = test.history.map(function(h){ return h.date; });

  makeChart(chartId, labels, [
    { label:'Failed', data: failValues, borderColor:'#ef4444', backgroundColor:'rgba(239,68,68,.15)', fill:true, tension:.2, pointRadius:4,
      pointBackgroundColor: failValues.map(function(v){ return v===1?'#ef4444':'#22c55e'; }) },
    { label:'Reruns', data: rerunValues, borderColor:'#a78bfa', borderDash:[4,3], tension:.2, pointRadius:3, fill:false, yAxisID:'y2' },
  ], 0, 1, '');

  // re-configure y axis for binary display
  if (charts[chartId]){
    charts[chartId].options.scales.y.ticks.callback = function(v){ return v===1?'Fail':v===0?'Pass':''; };
    charts[chartId].options.scales.y2 = {
      position:'right', grid:{drawOnChartArea:false},
      ticks:{ color:'#a78bfa', callback:function(v){ return v+'↺'; } },
      title:{ display:false }
    };
    charts[chartId].update();
  }
}

function closeDetail(pid){
  var panel = document.getElementById('detail-' + pid);
  panel.className = 'detail-panel';
  panel.innerHTML = '';
  destroyChart('detail-chart-' + pid);
  if (tableState[pid]) {
    tableState[pid].selected = null;
    document.querySelectorAll('#tbody-' + pid + ' tr').forEach(function(r){ r.classList.remove('selected'); });
  }
}

// ── init ─────────────────────────────────────────────────────────────────────

buildTabs();
buildAllPanes();
if (DATA.projects.length > 0) activeProject = DATA.projects[0].id;
</script>
</body>
</html>
"""
