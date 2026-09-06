/* ResiliX Dashboard front-end.
 *
 * Strictly a *renderer* for data prepared by the ResiliX Python layer
 * (/api/data). This script contains no scoring, degradation, recovery or
 * recommendation logic — all analysis arrives already computed. It also
 * performs zero network requests other than fetching /api/data from the
 * local dashboard server, and renders every report-provided string via
 * textContent (never innerHTML) so report content can never inject HTML.
 */
"use strict";

(function () {
  // ------------------------------------------------------------- utilities
  function $(id) { return document.getElementById(id); }

  function setText(el, text) {
    if (el) { el.textContent = text == null ? "" : String(text); }
  }

  function fmtNum(value, digits) {
    if (value === null || value === undefined || isNaN(Number(value))) {
      return "—";
    }
    var n = Number(value);
    if (digits === undefined) { digits = 1; }
    return n.toFixed(digits);
  }

  function fmtInt(value) {
    if (value === null || value === undefined || isNaN(Number(value))) {
      return "—";
    }
    return Number(value).toLocaleString("en-US");
  }

  function fmtPct(value, digits) {
    if (value === null || value === undefined || isNaN(Number(value))) {
      return "n/a";
    }
    return Number(value).toFixed(digits === undefined ? 1 : digits) + "%";
  }

  function fmtSignedPct(value) {
    if (value === null || value === undefined || isNaN(Number(value))) {
      return null;
    }
    var n = Number(value);
    return (n > 0 ? "+" : "") + n.toFixed(1) + "%";
  }

  function fmtSec(value) {
    if (value === null || value === undefined || isNaN(Number(value))) {
      return null;
    }
    return Number(value).toFixed(1) + "s";
  }

  function titleCase(text) {
    return String(text || "").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function changeClass(value) {
    if (value === null || value === undefined || isNaN(Number(value))) {
      return "";
    }
    var n = Number(value);
    if (n > 0.05) { return "bad"; }   // higher latency/error than baseline
    if (n < -0.05) { return "good"; }
    return "";
  }

  function clearChildren(el) {
    while (el && el.firstChild) { el.removeChild(el.firstChild); }
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  // ---------------------------------------------------------- state switch
  function showOnly(ids) {
    var all = ["state-error", "state-empty", "dashboard"];
    all.forEach(function (id) {
      $(id).classList.toggle("hidden", ids.indexOf(id) === -1);
    });
  }

  function showError(message) {
    setText($("error-message"), message);
    setText($("meta-status"), "error");
    $("meta-status").className = "meta-chip status-chip err";
    showOnly(["state-error"]);
  }

  function showEmpty() {
    setText($("meta-status"), "no data");
    $("meta-status").className = "meta-chip status-chip err";
    showOnly(["state-empty"]);
  }

  // ------------------------------------------------------------------ boot
  function boot() {
    fetch("/api/data", { credentials: "omit" })
      .then(function (res) {
        if (!res.ok) { throw new Error("HTTP " + res.status + " from /api/data"); }
        return res.json();
      })
      .then(function (payload) {
        if (!payload || payload.ok !== true) {
          showError((payload && payload.error) || "Unknown error loading report data.");
          return;
        }
        render(payload);
      })
      .catch(function (err) {
        showError("Could not load dashboard data: " + err.message);
      });
  }

  // ---------------------------------------------------------------- render
  function render(data) {
    var meta = data.meta || {};
    setText($("meta-version"), "v" + (meta.resilix_version || "?"));

    var test = data.test || {};
    var overview = data.overview || {};
    var metrics = data.metrics || {};
    var score = data.score || {};

    // Insufficient data guard: a result without a test id or without any
    // recorded operations/samples cannot be visualized meaningfully.
    var hasOps = Number(metrics.requests || 0) > 0;
    var hasSamples = Array.isArray(data.series) && data.series.length > 0;
    if (!test.test_id && !hasOps && !hasSamples) {
      showEmpty();
      return;
    }

    setText($("meta-status"), String(test.status || "unknown"));
    $("meta-status").className = "meta-chip status-chip " +
      (String(test.status) === "completed" ? "ok" : "err");

    renderHeader(data);
    renderKpis(data);
    renderScore(score, meta);
    renderComparison(data);
    renderSafety(data);
    renderRecovery(data);
    renderDegradation(data);
    renderRecommendations(data);
    renderSeries(data);
    renderFooter(data, meta);
    showOnly(["dashboard"]);
  }

  function renderHeader(data) {
    var test = data.test || {};
    var overview = data.overview || {};

    setText($("test-target"), test.target || "(no target recorded)");

    var status = $("test-status");
    var statusText = String(test.status || "unknown");
    setText(status, statusText);
    status.className = "status-badge " +
      (statusText === "completed" ? "" :
       (statusText === "aborted" || statusText === "error") ? "bad" : "warn");

    var parts = [];
    if (test.engine) { parts.push(titleCase(test.engine) + " engine"); }
    if (test.scenario) { parts.push("scenario: " + test.scenario); }
    if (overview.test_duration_sec !== null &&
        overview.test_duration_sec !== undefined) {
      parts.push("duration: " + fmtSec(overview.test_duration_sec));
    }
    if (test.test_id) { parts.push("test id: " + test.test_id); }
    setText($("test-subtitle"), parts.join("  ·  "));

    var phaseRow = $("test-phases");
    clearChildren(phaseRow);
    (test.phases || []).forEach(function (phase) {
      if (phase && phase.name) {
        phaseRow.appendChild(el("span", "phase-pill",
          phase.name + (phase.samples ? " (" + phase.samples + ")" : "")));
      }
    });
  }

  function renderKpis(data) {
    var overview = data.overview || {};
    var metrics = data.metrics || {};
    var comparisons = data.comparisons || {};

    // Score card
    var max = Number(overview.score_maximum || 100);
    var total = Number(overview.resilience_score || 0);
    setText($("kpi-score"), fmtNum(total, 1));
    setText($("kpi-score-max"), "/ " + fmtNum(max, max % 1 ? 1 : 0));
    var pct = max > 0 ? Math.max(0, Math.min(100, (total / max) * 100)) : 0;
    $("kpi-score-bar").style.width = pct.toFixed(1) + "%";
    setText($("kpi-assessment"), titleCase(overview.assessment || ""));

    // Availability
    setText($("kpi-availability"), fmtPct(metrics.availability_pct));
    setText($("kpi-operations"),
      fmtInt(metrics.successes) + " ok / " + fmtInt(metrics.failures) +
      " failed of " + fmtInt(metrics.requests) + " ops");

    // Peak p95 latency
    setText($("kpi-p95"), fmtNum(metrics.p95_latency_ms, 0) + " ms");
    var p95Change = fmtSignedPct(comparisons.p95_change_pct);
    var p95Note = $("kpi-p95-change");
    if (p95Change === null) {
      setText(p95Note, "no baseline comparison");
      p95Note.className = "kpi-note";
    } else {
      setText(p95Note, p95Change + " vs baseline");
      p95Note.className = "kpi-note " + changeClass(comparisons.p95_change_pct);
    }

    // Throughput
    setText($("kpi-throughput"), fmtNum(metrics.throughput_req_sec, 1) + " req/s");
    var thrNote = $("kpi-throughput-change");
    var thrVsBaseline = comparisons.throughput_vs_baseline_pct;
    if (thrVsBaseline === null || thrVsBaseline === undefined) {
      setText(thrNote, "baseline not recorded");
      thrNote.className = "kpi-note";
    } else {
      setText(thrNote, fmtNum(thrVsBaseline, 0) + "% of baseline throughput");
      thrNote.className = "kpi-note " +
        (Number(thrVsBaseline) >= 100 ? "good" : "warn");
    }

    // Error rate
    setText($("kpi-errors"), fmtPct(metrics.error_rate_pct));
    var errNote = $("kpi-error-detail");
    var errChange = comparisons.error_rate_change_pct;
    if (Number(metrics.failures) === 0 && Number(metrics.error_rate_pct) === 0) {
      setText(errNote, "no failures recorded");
      errNote.className = "kpi-note good";
    } else if (errChange === null || errChange === undefined) {
      setText(errNote, "baseline error rate not recorded");
      errNote.className = "kpi-note warn";
    } else {
      setText(errNote, fmtSignedPct(errChange) + " vs baseline");
      errNote.className = "kpi-note " + changeClass(errChange);
    }

    // Recovery
    var recovery = data.recovery || {};
    var recValue = $("kpi-recovery");
    var recNote = $("kpi-recovery-note");
    if (recovery.collected === true && recovery.details &&
        recovery.details.recovery_time_sec !== null &&
        recovery.details.recovery_time_sec !== undefined) {
      setText(recValue, fmtSec(recovery.details.recovery_time_sec));
      setText(recNote, "recovery time");
    } else if (recovery.collected === true) {
      setText(recValue, "measured");
      setText(recNote, recovery.summary || "");
    } else {
      setText(recValue, "—");
      setText(recNote, "recovery metrics not collected");
    }
  }

  // -------------------------------------------------------- section panels
  function renderScore(score) {
    var list = $("score-components");
    clearChildren(list);
    (score.components || []).forEach(function (comp) {
      var maximum = Number(comp.maximum || 100);
      var earned = Number(comp.earned || 0);
      var fraction = maximum > 0 ? Math.max(0, Math.min(1, earned / maximum)) : 0;
      var item = el("li");
      var row = el("div", "score-row");
      row.appendChild(el("span", "score-name", comp.name || "Component"));
      row.appendChild(el("span", "score-nums",
        fmtNum(earned, 1) + " / " + fmtNum(maximum, maximum % 1 ? 1 : 0)));
      item.appendChild(row);
      var track = el("div", "score-track");
      var fill = el("div", "score-fill" +
        (fraction < 0.4 ? " low" : fraction < 0.7 ? " mid" : ""));
      fill.style.width = (fraction * 100).toFixed(1) + "%";
      track.appendChild(fill);
      item.appendChild(track);
      if (comp.rationale) {
        item.appendChild(el("p", "score-rationale", comp.rationale));
      }
      list.appendChild(item);
    });

    var bits = [];
    if (score.assessment) { bits.push("Assessment: " + titleCase(score.assessment)); }
    if (score.source) { bits.push("score source: " + score.source); }
    setText($("score-summary"), bits.join("  ·  "));
  }

  function kvTable(rows) {
    var table = el("table", "kv-table");
    var tbody = el("tbody");
    rows.forEach(function (row) {
      var tr = el("tr");
      tr.appendChild(el("td", null, row.label));
      if (row.value === null || row.value === undefined || row.value === "") {
        tr.appendChild(el("td", "na", row.fallback || "not recorded"));
      } else {
        tr.appendChild(el("td", "kv-value" + (row.cls ? " " + row.cls : ""),
          String(row.value)));
      }
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function renderComparison(data) {
    var body = $("comparison-body");
    clearChildren(body);
    var baseline = data.baseline || {};
    var peak = data.peak || {};
    var comparisons = data.comparisons || {};
    var thrVs = comparisons.throughput_vs_baseline_pct;
    var thrKnown = thrVs !== null && thrVs !== undefined;

    body.appendChild(kvTable([
      { label: "Throughput (baseline → peak)",
        value: fmtNum(baseline.rate_per_sec, 1) + " → " +
               fmtNum(peak.rate_per_sec, 1) + " req/s" },
      { label: "Throughput vs baseline",
        value: thrKnown ? fmtNum(thrVs, 0) + "%" : null,
        cls: thrKnown && Number(thrVs) >= 100 ? "good" : "warn" },
      { label: "Avg latency (baseline → peak)",
        value: fmtNum(baseline.avg_latency_ms, 0) + " → " +
               fmtNum(peak.avg_latency_ms, 0) + " ms" },
      { label: "Avg latency change",
        value: fmtSignedPct(comparisons.avg_latency_change_pct),
        cls: changeClass(comparisons.avg_latency_change_pct) },
      { label: "p95 latency (baseline → peak)",
        value: fmtNum(baseline.p95_ms, 0) + " → " + fmtNum(peak.p95_ms, 0) + " ms" },
      { label: "p95 change",
        value: fmtSignedPct(comparisons.p95_change_pct),
        cls: changeClass(comparisons.p95_change_pct) },
      { label: "p99 latency (baseline → peak)",
        value: fmtNum(baseline.p99_ms, 0) + " → " + fmtNum(peak.p99_ms, 0) + " ms" },
      { label: "Error rate (baseline → peak)",
        value: fmtPct(baseline.error_rate_pct) + " → " + fmtPct(peak.error_rate_pct) },
    ]));

    var notCollected = data.not_collected || [];
    if (notCollected.length) {
      body.appendChild(el("p", "score-rationale",
        "Not collected by this engine: " + notCollected.join(", ")));
    }
  }

  function renderSafety(data) {
    var body = $("safety-body");
    clearChildren(body);
    var safety = data.safety || {};
    var status = String(safety.status || "");
    body.appendChild(kvTable([
      { label: "Guards", value: status,
        cls: status.indexOf("ENABLED") === 0 ? "good" : "warn" },
      { label: "Target allowlist",
        value: safety.require_authorized_target ? "required" : "not required" },
      { label: "Emergency stop",
        value: safety.allow_emergency_stop ? "enabled" : "disabled" },
      { label: "Max duration", value: fmtSec(safety.max_duration_sec) },
      { label: "Max concurrency", value: fmtInt(safety.max_concurrency) },
      { label: "Max rate", value: fmtNum(safety.max_rate_per_sec, 0) + " req/s" },
      { label: "Max total operations", value: fmtInt(safety.max_total_operations) },
      { label: "Max connections", value: fmtInt(safety.max_connections) },
    ]));
  }

  function renderRecovery(data) {
    var body = $("recovery-body");
    clearChildren(body);
    var recovery = data.recovery || {};
    if (recovery.collected !== true || !recovery.details) {
      body.appendChild(el("p", "empty-note", recovery.summary ||
        "Recovery metrics were not collected for this test."));
      return;
    }
    var details = recovery.details;
    body.appendChild(kvTable([
      { label: "Recovery time", value: fmtSec(details.recovery_time_sec) },
      { label: "Note", value: details.note || "" },
    ]));
    var row = el("div", "check-row");
    [["latency_recovered", "latency"],
     ["error_rate_recovered", "error rate"],
     ["throughput_recovered", "throughput"]].forEach(function (pair) {
      var ok = details[pair[0]] === true;
      row.appendChild(el("span", "check-pill " + (ok ? "ok" : "fail"),
        pair[1] + (ok ? " recovered" : " not recovered")));
    });
    body.appendChild(row);
  }

  function renderDegradation(data) {
    var body = $("degradation-body");
    clearChildren(body);
    var degradation = data.degradation || {};
    var events = degradation.events || [];
    setText($("degradation-count"), String(events.length));
    if (!events.length) {
      body.appendChild(el("p", "empty-note",
        degradation.summary || "No degradation events recorded."));
      return;
    }
    events.forEach(function (ev) {
      var row = el("div", "event");
      var sev = String(ev.severity || "info");
      row.appendChild(el("span", "sev-tag " + sev, sev));
      var main = el("div", "event-main");
      main.appendChild(el("p", "event-message", ev.message || ev.metric || ""));
      var metaBits = [];
      if (ev.phase) { metaBits.push("phase: " + ev.phase); }
      if (ev.metric) { metaBits.push("metric: " + ev.metric); }
      if (ev.change_pct !== null && ev.change_pct !== undefined) {
        metaBits.push("change: " + fmtSignedPct(ev.change_pct));
      }
      metaBits.push("observed: " + fmtNum(ev.observed, 1) +
                    " vs threshold " + fmtNum(ev.threshold, 1));
      if (ev.timestamp !== null && ev.timestamp !== undefined) {
        metaBits.push("t=" + fmtNum(ev.timestamp, 1) + "s");
      }
      main.appendChild(el("p", "event-meta", metaBits.join("  ·  ")));
      row.appendChild(main);
      body.appendChild(row);
    });
  }

  function renderRecommendations(data) {
    var body = $("recommendations-body");
    clearChildren(body);
    var recs = data.recommendations || {};
    var items = recs.items || recs.recommendations || [];
    setText($("recommendations-count"),
      String(recs.count !== undefined ? recs.count : items.length));
    if (!items.length) {
      body.appendChild(el("p", "empty-note",
        "No recommendations recorded for this test."));
      return;
    }
    items.forEach(function (rec) {
      var row = el("div", "rec");
      var priority = String(rec.priority || "medium");
      row.appendChild(el("span", "rec-priority " + priority, priority));
      var main = el("div", "event-main");
      main.appendChild(el("p", "rec-title",
        (rec.category ? "[" + rec.category + "] " : "") + (rec.title || "")));
      if (rec.detail) { main.appendChild(el("p", "rec-detail", rec.detail)); }
      if (rec.evidence) { main.appendChild(el("p", "rec-evidence", rec.evidence)); }
      row.appendChild(main);
      body.appendChild(row);
    });
  }

  function renderFooter(data, meta) {
    var overview = data.overview || {};
    var bits = [];
    if (meta.generated_at) { bits.push("report generated " + meta.generated_at); }
    if (meta.report_schema_version !== undefined) {
      bits.push("schema v" + meta.report_schema_version);
    }
    if (overview.target) { bits.push("target " + overview.target); }
    setText($("footer-meta"), bits.join("  ·  "));
  }

  // ------------------------------------------------------------ time series
  var SERIES_METRICS = [
    { key: "p95_ms", label: "p95 latency", unit: "ms", digits: 0, color: "#00e5a0" },
    { key: "avg_latency_ms", label: "avg latency", unit: "ms", digits: 0, color: "#4da3ff" },
    { key: "error_rate_pct", label: "error rate", unit: "%", digits: 1, color: "#ff5d73" },
    { key: "rate_per_sec", label: "throughput", unit: "req/s", digits: 1, color: "#9d7bff" }
  ];

  var currentMetricKey = SERIES_METRICS[0].key;

  function svgEl(tag, attrs) {
    var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (name) {
      node.setAttribute(name, String(attrs[name]));
    });
    return node;
  }

  function renderSeries(data) {
    var series = Array.isArray(data.series) ? data.series : [];
    var chart = $("series-chart");
    var caption = $("series-caption");
    var toggle = $("series-toggle");
    var empty = $("series-empty");
    clearChildren(chart);
    clearChildren(toggle);

    if (series.length < 2) {
      empty.classList.remove("hidden");
      $("series-figure").classList.add("hidden");
      setText(caption, "");
      return;
    }
    empty.classList.add("hidden");
    $("series-figure").classList.remove("hidden");

    // Metric selector (pure view concern: which recorded series to display).
    SERIES_METRICS.forEach(function (metric) {
      var button = el("button", metric.key === currentMetricKey ? "active" : "",
        metric.label);
      button.addEventListener("click", function () {
        currentMetricKey = metric.key;
        renderSeries(data);
      });
      toggle.appendChild(button);
    });

    var metric = null;
    SERIES_METRICS.forEach(function (m) {
      if (m.key === currentMetricKey) { metric = m; }
    });
    if (!metric) { metric = SERIES_METRICS[0]; }

    var width = 900, height = 300;
    var padLeft = 56, padRight = 18, padTop = 16, padBottom = 34;
    var plotW = width - padLeft - padRight;
    var plotH = height - padTop - padBottom;

    var xs = series.map(function (p) { return Number(p.t_rel || 0); });
    var ys = series.map(function (p) { return Number(p[metric.key] || 0); });
    var xMin = Math.min.apply(null, xs);
    var xMax = Math.max.apply(null, xs);
    var yMax = Math.max.apply(null, ys);
    if (xMax === xMin) { xMax = xMin + 1; }
    if (yMax <= 0) { yMax = 1; }
    yMax = yMax * 1.08;

    function px(x) {
      return padLeft + ((x - xMin) / (xMax - xMin)) * plotW;
    }
    function py(y) {
      return padTop + plotH - (y / yMax) * plotH;
    }

    // Horizontal grid lines + y labels (5 ticks).
    for (var i = 0; i <= 4; i++) {
      var yVal = yMax * i / 4;
      var yPix = py(yVal);
      chart.appendChild(svgEl("line", {
        x1: padLeft, x2: width - padRight, y1: yPix, y2: yPix,
        "class": "chart-grid-line"
      }));
      var label = svgEl("text", {
        x: padLeft - 8, y: yPix + 4, "text-anchor": "end",
        "class": "chart-axis-label"
      });
      label.textContent = yVal >= 100 ? yVal.toFixed(0) : yVal.toFixed(1);
      chart.appendChild(label);
    }

    // Area fill + line.
    var points = series.map(function (p, idx) {
      return px(xs[idx]).toFixed(1) + "," + py(ys[idx]).toFixed(1);
    });
    chart.appendChild(svgEl("polygon", {
      points: padLeft + "," + py(0) + " " + points.join(" ") + " " +
              px(xMax).toFixed(1) + "," + py(0),
      fill: metric.color, "class": "chart-area"
    }));
    chart.appendChild(svgEl("polyline", {
      points: points.join(" "), stroke: metric.color, "class": "chart-line"
    }));

    // Dots (with title tooltip showing the recorded values).
    series.forEach(function (p, idx) {
      var dot = svgEl("circle", {
        cx: px(xs[idx]).toFixed(1), cy: py(ys[idx]).toFixed(1), r: 3.2,
        fill: metric.color, "class": "chart-dot"
      });
      var tip = svgEl("title");
      tip.textContent = "t=" + fmtNum(p.t_rel, 1) + "s — " +
        metric.label + ": " + fmtNum(ys[idx], metric.digits) + " " + metric.unit +
        (p.phase ? " (" + p.phase + ")" : "");
      dot.appendChild(tip);
      chart.appendChild(dot);
    });

    // X axis labels (start / mid / end).
    [xMin, (xMin + xMax) / 2, xMax].forEach(function (xVal, idx) {
      var anchor = idx === 0 ? "start" : idx === 1 ? "middle" : "end";
      var xLabel = svgEl("text", {
        x: px(xVal).toFixed(1), y: height - 10, "text-anchor": anchor,
        "class": "chart-axis-label"
      });
      xLabel.textContent = fmtNum(xVal, 1) + "s";
      chart.appendChild(xLabel);
    });

    // Legend.
    var legend = svgEl("g", { transform: "translate(" + width / 2 + ", " + (padTop + 4) + ")" });
    var legendText = svgEl("text", { "text-anchor": "middle", "class": "chart-axis-label" });
    legendText.textContent = metric.label + " (" + metric.unit + ") over test time";
    legend.appendChild(legendText);
    chart.appendChild(legend);

    setText(caption, "Recorded metric samples only (n=" + series.length +
      ") — plotted directly from the saved result; no interpolation.");
  }

  if (document.readyState === "loading") {




    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
