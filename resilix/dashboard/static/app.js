"use strict";
/* ResiliX Operations Console front-end.
 *
 * Presentation + navigation + API calls ONLY. Every domain number (resilience
 * score, degradation, recovery, recommendations, safety) is rendered verbatim
 * from the backend's normalized payloads — no business logic lives here.
 *
 * Security contract: text-only DOM (createElement/textContent/appendChild/
 * classList/dataset). No raw-HTML assignment, no dynamic code execution, no
 * external/CDN resources; the only network surface is the local /api plane.
 */
(function() {
  const app = {
    currentView: "overview",
    currentTestId: null,
    livePollingInterval: null,
    liveFinal: null,
    testsSearch: "",
    testsStatus: "all",
    findingsSeverity: "",
    state: {
      overview: null,
      tests: [],
      testsLoading: false,
      testsError: null,
      findings: [],
      findingsLoading: false,
      findingsError: null,
      settings: null,
      live: null,
      consoleState: "idle",
      searchResults: null,
      validationResult: null
    },

    async init() {
      this.attachListeners();
      await this.loadAllData();
      this.render("overview");
    },

    async loadAllData() {
      try {
        const [s, o, t, f, st] = await Promise.all([
          api.state(), api.overview(), api.tests(), api.findings(),
          api.settings()
        ]);
        // The console state payload carries the lifecycle in `status`.
        this.state.consoleState = s.status || "idle";
        this.state.overview = o;
        this.state.tests = t.tests || [];
        this.state.findings = f.findings || [];
        this.state.settings = st;
        this.updateStatusIndicator();
      } catch (e) {
        console.error("Load error:", e);
      }
    },

    async loadTests() {
      this.state.testsLoading = true;
      this.state.testsError = null;
      try {
        const t = await api.tests(this.testsSearch);
        this.state.tests = t.tests || [];
      } catch (e) {
        this.state.testsError = "Could not load tests: " + e.message;
      } finally {
        this.state.testsLoading = false;
      }
    },

    async loadFindings() {
      this.state.findingsLoading = true;
      this.state.findingsError = null;
      try {
        const f = await api.findings(this.findingsSeverity);
        this.state.findings = f.findings || [];
      } catch (e) {
        this.state.findingsError = "Could not load findings: " + e.message;
      } finally {
        this.state.findingsLoading = false;
      }
    },

    updateStatusIndicator() {
      const dot = document.querySelector(".status-dot");
      const text = document.querySelector(".status-text");
      if (dot) dot.className = "status-dot " + this.state.consoleState;
      if (text) text.textContent = this.state.consoleState.toUpperCase();
    },

    attachListeners() {
      document.addEventListener("click", (e) => {
        const nav = e.target.closest("[data-view]");
        if (nav) {
          document.querySelectorAll(".nav-item").forEach(
            b => b.classList.remove("active"));
          const navBtn = nav.classList.contains("nav-item")
            ? nav
            : document.querySelector(".nav-item[data-view='" +
                nav.dataset.view + "']");
          if (navBtn) navBtn.classList.add("active");
          this.render(nav.dataset.view);
          return;
        }
        const action = e.target.closest("[data-action]");
        if (action) {
          const a = action.dataset.action;
          if (a === "stop-test") this.stopTest();
          if (a === "emergency-stop") this.confirmEmergencyStop();
          if (a === "view-test") this.viewTestDetail(action.dataset.testId);
          if (a === "new-test-nav") this.render("new-test");
          if (a === "navigate-tests") this.render("tests");
        }
      });
      const si = document.getElementById("search-input");
      if (si) {
        let st;
        si.addEventListener("input", (e) => {
          clearTimeout(st);
          const q = e.target.value.trim();
          if (q.length > 2) {
            st = setTimeout(() => this.performSearch(q), 300);
          }
        });
      }
    },

    render(name) {
      document.querySelectorAll(".view").forEach(v => v.classList.add("hidden"));
      const view = document.getElementById("view-" + name);
      if (view) {
        view.classList.remove("hidden");
        this.currentView = name;
        this.renderView(name);
      }
    },

    renderView(name) {
      const views = {
        "overview": () => this.renderOverview(),
        "new-test": () => this.renderNewTest(),
        "tests": () => this.renderTests(),
        "test-detail": () => this.renderTestDetail(),
        "findings": () => this.renderFindings(),
        "live": () => this.renderLive(),
        "reports": () => this.renderReports(),
        "settings": () => this.renderSettings()
      };
      if (views[name]) views[name]();
    },

    // ------------------------------------------------------------------
    // DOM + formatting helpers (text-only rendering)
    // ------------------------------------------------------------------
    ce(tag, cls, parent) {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (parent) parent.appendChild(e);
      return e;
    },

    txt(text, parent) {
      const e = document.createTextNode(text == null ? "" : text);
      if (parent) parent.appendChild(e);
      return e;
    },

    fmtScore(v) { return v == null ? "N/A" : Number(v).toFixed(1); },
    fmtSec(v) { return v == null || v === "" ? "N/A" : v + "s"; },
    fmtNum(v) { return v == null ? "N/A" : String(v); },

    statusLabel(status) {
      const labels = {
        completed: "Completed", stopped: "Stopped", failed: "Failed",
        emergency_stopped: "Emergency Stopped", running: "Running",
        stopping: "Stopping", idle: "Idle", aborted: "Aborted"
      };
      return labels[status] || status || "N/A";
    },

    statusBadge(status, parent) {
      const s = this.ce("span", "status-badge " + (status || "unknown"),
                        parent);
      s.textContent = this.statusLabel(status);
      return s;
    },

    sevBadge(severity, parent) {
      const s = this.ce("span", "severity-badge " + (severity || "info"),
                        parent);
      s.textContent = (severity || "info").toUpperCase();
      return s;
    },

    viewHeader(view, title, subtitle) {
      const header = this.ce("div", "view-header", view);
      this.ce("h1", null, header).textContent = title;
      if (subtitle) this.ce("p", null, header).textContent = subtitle;
      return header;
    },

    panel(view, title) {
      const section = this.ce("section", "panel", view);
      this.ce("h2", "panel-title", section).textContent = title;
      return section;
    },

    setLoading(container, message) {
      container.textContent = "";
      const box = this.ce("div", "state-box loading", container);
      this.ce("span", "spinner", box);
      this.ce("p", null, box).textContent = message || "Loading...";
    },

    setError(container, message, retry) {
      container.textContent = "";
      const box = this.ce("div", "state-box error", container);
      this.ce("p", "state-title", box).textContent = "Something went wrong";
      this.ce("p", null, box).textContent = message || "Unknown error.";
      if (retry) {
        const btn = this.ce("button", "btn btn-small", box);
        btn.textContent = "RETRY";
        btn.onclick = retry;
      }
    },

    setEmpty(container, title, hint, actionLabel, action) {
      container.textContent = "";
      const box = this.ce("div", "empty-state", container);
      this.ce("p", "state-title", box).textContent = title;
      if (hint) this.ce("p", null, box).textContent = hint;
      if (actionLabel) {
        const btn = this.ce("button", "btn btn-primary", box);
        btn.textContent = actionLabel;
        btn.dataset.action = action || "new-test-nav";
      }
    },

    renderOverview() {
      const v = document.getElementById("view-overview");
      if (!v) return;
      const ov = this.state.overview || {};
      const stats = ov.stats || {};
      v.textContent = "";
      const header = this.viewHeader(v, "Overview",
        "Resilience Operations Console");
      const grid = this.ce("div", "overview-grid", v);
      const kpis = [
        ["Tests", this.fmtNum(stats.tests_run)],
        ["Latest Score", this.fmtScore(stats.latest_score)],
        ["Findings", this.fmtNum(stats.findings)],
        ["Console", this.statusLabel(this.state.consoleState)]
      ];
      kpis.forEach(([label, value]) => {
        const card = this.ce("div", "kpi-card", grid);
        this.ce("div", "kpi-label", card).textContent = label;
        this.ce("div", "kpi-value", card).textContent = value;
      });
      const section = this.panel(v, "Recent Tests");
      const list = this.ce("div", "recent-tests", section);
      this.populateRecentTests(list);
    },

    populateRecentTests(container) {
      container.textContent = "";
      const tests = this.state.tests || [];
      if (tests.length === 0) {
        const empty = this.ce("p", "empty-message", container);
        empty.textContent = "No tests recorded yet.";
        return;
      }
      const table = this.ce("table", "data-table", container);
      const thead = this.ce("thead", null, table);
      const hrow = this.ce("tr", null, thead);
      ["Status", "Test ID", "Target", "Engine", "Score", "Started"].forEach(
        h => this.ce("th", null, hrow).textContent = h);
      const tbody = this.ce("tbody", null, table);
      tests.slice(0, 5).forEach(t => {
        const row = this.ce("tr", null, tbody);
        row.style.cursor = "pointer";
        row.onclick = () => this.viewTestDetail(t.test_id);
        const sc = this.ce("td", null, row);
        this.statusBadge(t.status, sc);
        this.ce("td", null, row).textContent = t.test_id || "N/A";
        this.ce("td", null, row).textContent = t.target || "N/A";
        this.ce("td", null, row).textContent = t.engine || "N/A";
        this.ce("td", null, row).textContent = this.fmtScore(t.score);
        this.ce("td", null, row).textContent = t.started_at || "N/A";
      });
    },

    renderNewTest() {
      const v = document.getElementById("view-new-test");
      if (!v) return;
      const s = this.state.settings || {};
      const f = s.safety || {};
      const e = s.engines || [];
      const c = s.scenarios || [];
      v.textContent = "";
      this.viewHeader(v, "New Test", "Configure a controlled resilience test");
      const form = this.ce("form", "test-form", v);
      form.id = "f";
      // Target
      const tg = this.ce("div", "form-group", form);
      this.ce("label", null, tg).textContent = "Target *";
      const ti = this.ce("input", null, tg);
      ti.type = "text"; ti.id = "target"; ti.required = true;
      ti.placeholder = "127.0.0.1:8080";
      // Engine
      const eg = this.ce("div", "form-group", form);
      this.ce("label", null, eg).textContent = "Engine *";
      const es = this.ce("select", null, eg);
      es.id = "engine";
      e.forEach(en => {
        const opt = this.ce("option", null, es);
        opt.value = en;
        opt.textContent = en.toUpperCase();
      });
      // Scenario
      const sg = this.ce("div", "form-group", form);
      this.ce("label", null, sg).textContent = "Scenario *";
      const ss = this.ce("select", null, sg);
      ss.id = "scenario";
      c.forEach(sc => {
        const opt = this.ce("option", null, ss);
        opt.value = sc;
        opt.textContent = sc;
      });
      // Optional numbers
      const num = (id, label, ph) => {
        const g = this.ce("div", "form-group", form);
        this.ce("label", null, g).textContent = label;
        const inp = this.ce("input", null, g);
        inp.type = "number"; inp.id = id; inp.placeholder = ph;
      };
      num("duration", "Duration (seconds)", "30");
      num("start_rate", "Start rate (ops/s)", "5");
      num("max_rate", "Max rate (ops/s)", "50");
      num("concurrency", "Concurrency", "5");
      const vr = this.ce("div", "validation-result", form);
      vr.id = "validation-result";
      // Buttons
      const bg = this.ce("div", "btn-group", form);
      const vb = this.ce("button", "btn btn-primary", bg);
      vb.type = "button"; vb.id = "vbtn"; vb.textContent = "VALIDATE";
      const rb = this.ce("button", "btn btn-success", bg);
      rb.type = "button"; rb.id = "rbtn"; rb.textContent = "RUN TEST";
      rb.disabled = true;
      // Safety panel (public envelope from the backend settings payload)
      const sp = this.ce("div", "safety-panel", v);
      this.ce("h3", null, sp).textContent = "Safety Envelope";
      const sl = this.ce("ul", "safety-list", sp);
      const items = [
        "Emergency Stop: " + (f.allow_emergency_stop ? "ENABLED" : "DISABLED"),
        "Target Allowlist: " + (f.require_authorized_target ? "ENABLED"
                                                            : "DISABLED"),
        "Max Duration: " + this.fmtSec(f.max_duration_sec),
        "Max Rate: " + this.fmtNum(f.max_rate_per_sec) + "/s",
        "Max Concurrency: " + this.fmtNum(f.max_concurrency),
        "Max Operations: " + this.fmtNum(f.max_total_operations),
        "Max Connections: " + this.fmtNum(f.max_connections)
      ];
      items.forEach(i => this.ce("li", null, sl).textContent = i);
      this.setupNewTestForm();
    },

    setupNewTestForm() {
      const vb = document.getElementById("vbtn");
      const rb = document.getElementById("rbtn");
      if (!vb || !rb) return;
      vb.onclick = async () => {
        const config = this.collectFormData();
        if (!config) return;
        vb.textContent = "VALIDATING...";
        vb.disabled = true;
        try {
          const res = await api.validate(config);
          this.displayValidationResult(res);
          rb.disabled = !res.ok;
        } catch (e) {
          this.displayValidationError(e.message);
          rb.disabled = true;
        } finally {
          vb.textContent = "VALIDATE";
          vb.disabled = false;
        }
      };
      rb.onclick = async () => {
        const config = this.collectFormData();
        if (!config) return;
        rb.textContent = "STARTING...";
        rb.disabled = true;
        try {
          await api.start(config);
          await this.loadAllData();
          this.render("live");
        } catch (e) {
          this.displayValidationError("Start failed: " + e.message);
          rb.textContent = "RUN TEST";
          rb.disabled = false;
        }
      };
    },

    collectFormData() {
      const t = document.getElementById("target");
      const e = document.getElementById("engine");
      const s = document.getElementById("scenario");
      if (!t || !t.value.trim()) {
        this.displayValidationError("Target is required"); return null;
      }
      if (!e || !e.value) {
        this.displayValidationError("Engine is required"); return null;
      }
      if (!s || !s.value) {
        this.displayValidationError("Scenario is required"); return null;
      }
      // Field names match the backend ConfigRequest contract exactly.
      const config = { target: t.value.trim(), engine: e.value,
                       scenario: s.value };
      const d = document.getElementById("duration");
      if (d && d.value) config.duration = parseFloat(d.value);
      const sr = document.getElementById("start_rate");
      if (sr && sr.value) config.start_rate = parseFloat(sr.value);
      const mr = document.getElementById("max_rate");
      if (mr && mr.value) config.max_rate = parseFloat(mr.value);
      const c = document.getElementById("concurrency");
      if (c && c.value) config.concurrency = parseInt(c.value, 10);
      return config;
    },

    displayValidationResult(result) {
      const el = document.getElementById("validation-result");
      if (!el) return;
      el.textContent = "";
      if (result.ok) {
        const p = this.ce("p", "validation-success", el);
        p.textContent = "Configuration Valid";
      } else {
        const p = this.ce("p", "validation-error", el);
        p.textContent = "Validation Failed";
      }
      if (result.checks) {
        const list = this.ce("ul", "validation-checks", el);
        result.checks.forEach(check => {
          const li = this.ce("li", null, list);
          li.textContent = (check.ok ? "[PASS] " : "[FAIL] ") +
            check.name + ": " + (check.detail || "");
        });
      }
    },

    displayValidationError(message) {
      const el = document.getElementById("validation-result");
      if (el) {
        el.textContent = "";
        const p = this.ce("p", "validation-error", el);
        p.textContent = message || "Validation failed";
      }
    },

    // ------------------------------------------------------------------
    // Tests view: investigation/history workspace
    // ------------------------------------------------------------------
    renderTests() {
      const v = document.getElementById("view-tests");
      if (!v) return;
      v.textContent = "";
      this.viewHeader(v, "Tests",
        "Test history — select a run to open its detail workspace");
      const toolbar = this.ce("div", "tests-toolbar", v);
      const search = this.ce("input", "toolbar-input", toolbar);
      search.type = "text";
      search.id = "tests-search";
      search.placeholder = "Search tests (id, target, scenario)...";
      search.value = this.testsSearch;
      const statusSel = this.ce("select", "toolbar-select", toolbar);
      statusSel.id = "tests-status-filter";
      [["all", "All statuses"], ["completed", "Completed"],
       ["stopped", "Stopped"], ["failed", "Failed"],
       ["emergency_stopped", "Emergency Stopped"]].forEach(([val, label]) => {
        const opt = this.ce("option", null, statusSel);
        opt.value = val;
        opt.textContent = label;
      });
      statusSel.value = this.testsStatus;
      const refresh = this.ce("button", "btn btn-small", toolbar);
      refresh.textContent = "REFRESH";
      refresh.onclick = () => this.refreshTests();

      const listWrap = this.ce("div", "tests-list-wrap", v);
      listWrap.id = "tests-list";
      let debounce;
      search.addEventListener("input", () => {
        this.testsSearch = search.value.trim();
        clearTimeout(debounce);
        debounce = setTimeout(() => this.refreshTests(), 300);
      });
      statusSel.addEventListener("change", () => {
        this.testsStatus = statusSel.value;
        this.renderTestRows();
      });
      this.renderTestRows();
    },

    async refreshTests() {
      const wrap = document.getElementById("tests-list");
      if (wrap) this.setLoading(wrap, "Loading test history...");
      await this.loadTests();
      this.renderTestRows();
    },

    renderTestRows() {
      const wrap = document.getElementById("tests-list");
      if (!wrap) return;
      wrap.textContent = "";
      if (this.state.testsLoading) {
        this.setLoading(wrap, "Loading test history...");
        return;
      }
      if (this.state.testsError) {
        this.setError(wrap, this.state.testsError,
                      () => this.refreshTests());
        return;
      }
      const all = this.state.tests || [];
      const tests = all.filter(t => this.testsStatus === "all" ||
        t.status === this.testsStatus);
      if (tests.length === 0) {
        if (all.length === 0) {
          this.setEmpty(wrap, "No tests recorded yet",
            "Run your first controlled resilience test to build history.",
            "+ NEW TEST");
        } else {
          this.setEmpty(wrap, "No tests match the current filters",
            "Adjust the search text or status filter.");
        }
        return;
      }
      const table = this.ce("table", "data-table", wrap);
      const thead = this.ce("thead", null, table);
      const hrow = this.ce("tr", null, thead);
      ["Status", "Test ID", "Target", "Engine", "Scenario", "Started",
       "Duration", "Score", "Findings"].forEach(
         h => this.ce("th", null, hrow).textContent = h);
      const tbody = this.ce("tbody", null, table);
      tests.forEach(t => {
        const row = this.ce("tr", "test-row", tbody);
        row.style.cursor = "pointer";
        row.onclick = () => this.viewTestDetail(t.test_id);
        const sc = this.ce("td", null, row);
        this.statusBadge(t.status, sc);
        this.ce("td", "mono", row).textContent = t.test_id || "N/A";
        this.ce("td", null, row).textContent = t.target || "N/A";
        this.ce("td", null, row).textContent = t.engine || "N/A";
        this.ce("td", null, row).textContent = t.scenario || "N/A";
        this.ce("td", null, row).textContent = t.started_at || "N/A";
        this.ce("td", null, row).textContent = this.fmtSec(t.duration_sec);
        this.ce("td", "score-cell", row).textContent =
          this.fmtScore(t.score);
        this.ce("td", null, row).textContent = this.fmtNum(t.findings);
      });
    },

    viewTestDetail(testId) {
      if (!testId) return;
      this.currentTestId = testId;
      this.render("test-detail");
    },

    // ------------------------------------------------------------------
    // Test Detail workspace — renders backend report data verbatim
    // ------------------------------------------------------------------
    async renderTestDetail() {
      const v = document.getElementById("view-test-detail");
      if (!v) return;
      v.textContent = "";
      const id = this.currentTestId;
      if (!id) {
        this.setEmpty(v, "No test selected",
          "Pick a test from the Tests history to open its detail view.",
          "BROWSE TESTS", "navigate-tests");
        return;
      }
      const header = this.ce("div", "view-header", v);
      const back = this.ce("a", "back-link", header);
      back.href = "#";
      back.textContent = "< Back to Tests";
      back.onclick = (e) => { e.preventDefault(); this.render("tests"); };
      const body = this.ce("div", null, v);
      body.id = "detail-body";
      this.setLoading(body, "Loading test " + id + "...");
      let detail;
      try {
        detail = await api.test(id);
      } catch (e) {
        body.textContent = "";
        if (String(e.message).indexOf("404") !== -1) {
          this.setEmpty(body, "Test not found",
            "No saved result exists for '" + id + "'.",
            "BROWSE TESTS", "navigate-tests");
        } else {
          this.setError(body, "Could not load test detail: " + e.message,
            () => this.renderTestDetail());
        }
        return;
      }
      let eventsPayload = null;
      try {
        eventsPayload = await api.testEvents(id);
      } catch (e) {
        eventsPayload = { test_id: id, events: null, error: e.message };
      }
      this.state.detail = detail;
      this.state.events = eventsPayload;
      body.textContent = "";

      // -- Header: status + identity -------------------------------------
      const title = this.ce("div", "detail-title", body);
      this.ce("h1", null, title).textContent = detail.test_id || id;
      this.statusBadge(detail.status, title);
      const report = detail.report || {};
      const fa = report.final_assessment || {};
      if (fa.category) {
        this.ce("span", "assessment-chip", title).textContent =
          fa.category;
      }
      const meta = this.ce("div", "detail-info-grid", body);
      [["Test ID", detail.test_id], ["Target", detail.target],
       ["Engine", detail.engine], ["Scenario", detail.scenario],
       ["Started", detail.started_at || "N/A"],
       ["Finished", detail.finished_at || "N/A"],
       ["Duration", this.fmtSec(detail.duration_sec)],
       ["Status", this.statusLabel(detail.status)]].forEach(
        ([label, value]) => {
          const item = this.ce("div", "detail-info-item", meta);
          this.ce("span", "detail-info-label", item).textContent = label;
          this.ce("span", "detail-info-value", item).textContent =
            value == null || value === "" ? "N/A" : String(value);
        });

      // -- Report download actions (existing reporting layer) ------------
      const actions = this.ce("div", "detail-actions", body);
      [["JSON", "json"], ["Markdown", "markdown"], ["Terminal", "terminal"]]
        .forEach(([label, fmt]) => {
          const a = this.ce("a", "btn btn-small", actions);
          a.href = "/api/console/tests/" + encodeURIComponent(id) +
            "/report?fmt=" + fmt;
          a.textContent = "REPORT " + label.toUpperCase();
        });

      this.renderDetailScore(body, report);
      this.renderDetailMetrics(body, report);
      this.renderDetailFindings(body, report);
      this.renderDetailRecovery(body, report);
      this.renderDetailConfiguration(body, report);
      this.renderDetailTimeline(body, eventsPayload);
    },

    renderDetailScore(body, report) {
      const section = this.panel(body, "Resilience Score");
      const score = report.resilience_score || {};
      const hero = this.ce("div", "score-hero", section);
      const val = this.ce("div", "score-value", hero);
      val.textContent = this.fmtScore(score.total);
      this.ce("div", "score-max", hero).textContent =
        "/ " + this.fmtNum(score.maximum);
      const side = this.ce("div", "score-side", hero);
      this.ce("div", "score-source", side).textContent =
        "source: " + (score.source || "N/A") +
        (report.final_assessment && report.final_assessment.category
          ? "  ·  " + report.final_assessment.category : "");
      if (report.final_assessment && report.final_assessment.summary) {
        this.ce("p", "score-summary", side).textContent =
          report.final_assessment.summary;
      }
      const comps = this.ce("div", "score-components", section);
      (score.components || []).forEach(c => {
        const row = this.ce("div", "score-component", comps);
        const label = this.ce("div", "score-component-label", row);
        label.textContent = (c.name || "component") + " — " +
          this.fmtScore(c.earned) + " / " + this.fmtNum(c.maximum);
        const bar = this.ce("div", "score-bar", row);
        const fill = this.ce("div", "score-fill", bar);
        const max = Number(c.maximum) || 0;
        const earned = Math.max(0, Math.min(Number(c.earned) || 0, max));
        fill.style.width = (max > 0 ? (earned / max) * 100 : 0) + "%";
        this.ce("div", "score-rationale", row).textContent =
          c.rationale || "";
      });
      if (!score.components || score.components.length === 0) {
        this.ce("p", "empty-message", comps).textContent =
          "No score components supplied by the backend.";
      }
    },

    kvGrid(parent, pairs) {
      const grid = this.ce("div", "detail-info-grid", parent);
      pairs.forEach(([label, value]) => {
        const item = this.ce("div", "detail-info-item", grid);
        this.ce("span", "detail-info-label", item).textContent = label;
        this.ce("span", "detail-info-value", item).textContent =
          value == null || value === "" ? "N/A" : String(value);
      });
      return grid;
    },

    renderDetailMetrics(body, report) {
      const section = this.panel(body, "Key Metrics");
      const pm = report.performance_metrics || {};
      const baseline = pm.baseline || {};
      const peak = pm.peak || {};
      const top = this.ce("div", "detail-info-grid", section);
      [["Operations", this.fmtNum(pm.operations)],
       ["Successes", this.fmtNum(pm.successes)],
       ["Failures", this.fmtNum(pm.failures)],
       ["Availability", pm.availability_pct == null
         ? "N/A" : pm.availability_pct + "%"],
       ["Requested Rate", pm.requested_rate == null
         ? "N/A" : pm.requested_rate + "/s"]].forEach(([l, val]) => {
        const item = this.ce("div", "detail-info-item", top);
        this.ce("span", "detail-info-label", item).textContent = l;
        this.ce("span", "detail-info-value", item).textContent = val;
      });
      const cards = this.ce("div", "metrics-grid", section);
      const metricRows = [
        ["Throughput (rate/s)", baseline.rate_per_sec, peak.rate_per_sec],
        ["Avg latency (ms)", baseline.avg_latency_ms, peak.avg_latency_ms],
        ["p50 (ms)", baseline.p50_ms, peak.p50_ms],
        ["p95 (ms)", baseline.p95_ms, peak.p95_ms],
        ["p99 (ms)", baseline.p99_ms, peak.p99_ms],
        ["Error rate", baseline.error_rate_pct == null
          ? null : baseline.error_rate_pct + "%",
         peak.error_rate_pct == null ? null : peak.error_rate_pct + "%"]
      ];
      metricRows.forEach(([label, base, pk]) => {
        const card = this.ce("div", "metric-card", cards);
        this.ce("div", "metric-label", card).textContent = label;
        this.ce("div", "metric-value", card).textContent =
          "base " + this.fmtNum(base) + " · peak " + this.fmtNum(pk);
      });
      const extra = this.ce("div", "detail-info-grid", section);
      [["Active connections", peak.active_connections],
       ["Timeouts", peak.timeouts],
       ["Connection failures", peak.connection_failures],
       ["Min latency (ms)", peak.min_latency_ms],
       ["Max latency (ms)", peak.max_latency_ms]].forEach(([l, val]) => {
        const item = this.ce("div", "detail-info-item", extra);
        this.ce("span", "detail-info-label", item).textContent = l;
        this.ce("span", "detail-info-value", item).textContent =
          this.fmtNum(val);
      });
      const cmp = pm.comparisons || {};
      if (Object.keys(cmp).length > 0) {
        const cmpWrap = this.ce("div", "comparisons", section);
        this.ce("h3", "sub-title", cmpWrap).textContent =
          "Peak vs baseline (reported by backend)";
        this.kvGrid(cmpWrap, Object.keys(cmp).map(k =>
          [k, cmp[k] == null ? "N/A" : cmp[k] + "%"]));
      }
      // Honest absence: the backend names what it did not collect
      // (e.g. CPU / memory / event-loop lag for engine types without it).
      if (pm.not_collected && pm.not_collected.length > 0) {
        const note = this.ce("p", "na-note", section);
        note.textContent = "Not collected for this run: " +
          pm.not_collected.join("; ") + ".";
      }
    },

    renderDetailFindings(body, report) {
      const section = this.panel(body, "Findings");
      const deg = report.degradation_analysis || {};
      const events = deg.events || [];
      if (deg.summary) {
        this.ce("p", "section-summary", section).textContent = deg.summary;
      }
      if (events.length === 0) {
        this.ce("p", "empty-message", section).textContent =
          "No degradation events recorded for this test.";
        return;
      }
      const table = this.ce("table", "data-table findings-table", section);
      const thead = this.ce("thead", null, table);
      const hrow = this.ce("tr", null, thead);
      ["Severity", "Metric", "Observed", "Threshold", "Phase", "Detail"]
        .forEach(h => this.ce("th", null, hrow).textContent = h);
      const tbody = this.ce("tbody", null, table);
      events.forEach(ev => {
        const row = this.ce("tr", null, tbody);
        const sev = this.ce("td", null, row);
        this.sevBadge(ev.severity, sev);
        this.ce("td", "mono", row).textContent = ev.metric || "N/A";
        this.ce("td", null, row).textContent = this.fmtNum(ev.observed);
        this.ce("td", null, row).textContent = this.fmtNum(ev.threshold);
        this.ce("td", null, row).textContent = ev.phase || "N/A";
        this.ce("td", null, row).textContent = ev.message || "";
      });
    },

    renderDetailRecovery(body, report) {
      const section = this.panel(body, "Recovery");
      const rec = report.recovery_analysis || {};
      if (!rec.collected) {
        this.ce("p", "empty-message", section).textContent =
          rec.summary || "Recovery was not collected for this test.";
        return;
      }
      if (rec.summary) {
        this.ce("p", "section-summary", section).textContent = rec.summary;
      }
      const d = rec.details || {};
      this.kvGrid(section, [
        ["Recovery time", d.recovery_time_sec == null
          ? "N/A" : this.fmtSec(d.recovery_time_sec)],
        ["Latency recovered", d.latency_recovered ? "YES" : "NO"],
        ["Error-rate recovered", d.error_rate_recovered ? "YES" : "NO"],
        ["Throughput recovered", d.throughput_recovered ? "YES" : "NO"],
        ["Note", d.note || "N/A"]
      ]);
    },

    renderDetailConfiguration(body, report) {
      const section = this.panel(body, "Configuration (read-only)");
      const cfg = report.test_configuration || {};
      const target = cfg.target || {};
      this.kvGrid(section, [
        ["Target", target.address || cfg.target],
        ["Engine", cfg.engine],
        ["Scenario", cfg.scenario],
        ["Description", cfg.description],
        ["Duration", this.fmtSec(cfg.duration_sec)],
        ["Max rate requested", cfg.max_rate_requested == null
          ? "N/A" : cfg.max_rate_requested + "/s"],
        ["Concurrency requested", cfg.concurrency_requested == null
          ? "N/A" : String(cfg.concurrency_requested)]
      ]);
      const phases = cfg.phases || [];
      if (phases.length > 0) {
        this.ce("h3", "sub-title", section).textContent =
          "Observed phases (from recorded snapshots)";
        const ul = this.ce("ul", "phase-list", section);
        phases.forEach(p => {
          const li = this.ce("li", null, ul);
          li.textContent = (p.name || "?") + " — " +
            this.fmtNum(p.samples) + " snapshot(s)";
        });
      }
      const safety = cfg.safety || {};
      this.ce("h3", "sub-title", section).textContent =
        "Public safety envelope (" + (safety.status || "N/A") + ")";
      this.kvGrid(section, [
        ["Max duration", this.fmtSec(safety.max_duration_sec)],
        ["Max rate", safety.max_rate_per_sec == null
          ? "N/A" : safety.max_rate_per_sec + "/s"],
        ["Max concurrency", this.fmtNum(safety.max_concurrency)],
        ["Max total operations", this.fmtNum(safety.max_total_operations)],
        ["Emergency stop", safety.allow_emergency_stop ? "ENABLED"
                                                       : "DISABLED"],
        ["Authorized targets only", safety.require_authorized_target
          ? "YES" : "NO"]
      ]);
      const note = this.ce("p", "na-note", section);
      note.textContent = "Configuration is read-only. Use New Test to run " +
        "a different configuration.";
    },

    renderDetailTimeline(body, eventsPayload) {
      const section = this.panel(body, "Timeline / Events");
      const events = (eventsPayload && eventsPayload.events) || [];
      if (eventsPayload && eventsPayload.error) {
        this.setError(section, "Could not load events: " +
          eventsPayload.error, () => this.renderTestDetail());
        return;
      }
      if (events.length === 0) {
        this.ce("p", "empty-message", section).textContent =
          "No structured events were recorded for this test.";
        return;
      }
      if (events.length >= 300) {
        this.ce("p", "na-note", section).textContent =
          "Showing the first 300 events (backend cap).";
      }
      const list = this.ce("ol", "timeline", section);
      events.forEach(ev => {
        const item = this.ce("li", "timeline-item sev-" + (ev.severity ||
          "info"), list);
        const head = this.ce("div", "timeline-head", item);
        this.ce("span", "timeline-time", head).textContent =
          ev.timestamp || "N/A";
        this.sevBadge(ev.severity, head);
        this.ce("span", "timeline-type", head).textContent =
          ev.event_type || "event";
        this.ce("div", "timeline-msg", item).textContent =
          ev.message || "";
      });
    },

    // ------------------------------------------------------------------
    // Findings view: cross-test degradation workspace
    // ------------------------------------------------------------------
    renderFindings() {
      const v = document.getElementById("view-findings");
      if (!v) return;
      v.textContent = "";
      this.viewHeader(v, "Findings",
        "Degradation events across all recorded tests");
      const toolbar = this.ce("div", "tests-toolbar", v);
      const sevSel = this.ce("select", "toolbar-select", toolbar);
      sevSel.id = "findings-severity-filter";
      [["", "All severities"], ["info", "Info"], ["warning", "Warning"],
       ["critical", "Critical"]].forEach(([val, label]) => {
        const opt = this.ce("option", null, sevSel);
        opt.value = val;
        opt.textContent = label;
      });
      sevSel.value = this.findingsSeverity;
      const refresh = this.ce("button", "btn btn-small", toolbar);
      refresh.textContent = "REFRESH";
      refresh.onclick = () => this.refreshFindings();
      const listWrap = this.ce("div", null, v);
      listWrap.id = "findings-list";
      sevSel.addEventListener("change", () => {
        this.findingsSeverity = sevSel.value;
        this.refreshFindings();
      });
      this.renderFindingRows();
    },

    async refreshFindings() {
      const wrap = document.getElementById("findings-list");
      if (wrap) this.setLoading(wrap, "Loading findings...");
      await this.loadFindings();
      this.renderFindingRows();
    },

    renderFindingRows() {
      const wrap = document.getElementById("findings-list");
      if (!wrap) return;
      wrap.textContent = "";
      if (this.state.findingsLoading) {
        this.setLoading(wrap, "Loading findings...");
        return;
      }
      if (this.state.findingsError) {
        this.setError(wrap, this.state.findingsError,
                      () => this.refreshFindings());
        return;
      }
      const findings = this.state.findings || [];
      if (findings.length === 0) {
        this.setEmpty(wrap, "No findings recorded",
          "No degradation events matched the current severity filter.");
        return;
      }
      const table = this.ce("table", "data-table findings-table", wrap);
      const thead = this.ce("thead", null, table);
      const hrow = this.ce("tr", null, thead);
      ["Severity", "Test ID", "Target", "Metric", "Observed", "Threshold",
       "Detail"].forEach(h => this.ce("th", null, hrow).textContent = h);
      const tbody = this.ce("tbody", null, table);
      findings.forEach(f => {
        const row = this.ce("tr", "test-row", tbody);
        row.style.cursor = "pointer";
        row.onclick = () => this.viewTestDetail(f.test_id);
        const sev = this.ce("td", null, row);
        this.sevBadge(f.severity, sev);
        const idCell = this.ce("td", "mono", row);
        idCell.textContent = f.test_id || "N/A";
        this.ce("td", null, row).textContent = f.target || "N/A";
        this.ce("td", null, row).textContent = f.metric || "N/A";
        this.ce("td", null, row).textContent = this.fmtNum(f.observed);
        this.ce("td", null, row).textContent = this.fmtNum(f.threshold);
        this.ce("td", null, row).textContent = f.message || "";
      });
    },

    // ------------------------------------------------------------------
    // Live Feed — real console state + live collector payloads
    // ------------------------------------------------------------------
    renderLive() {
      const v = document.getElementById("view-live");
      if (!v) return;
      v.textContent = "";
      const header = this.viewHeader(v, "Live Feed",
        "Real-time view of the active or most recent run");
      const body = this.ce("div", null, v);
      body.id = "live-body";
      this.setLoading(body, "Checking console state...");
      this.stopLivePolling();
      this.pollLive();
    },

    async pollLive() {
      if (this.currentView !== "live") return;
      const body = document.getElementById("live-body");
      if (!body) return;
      let live;
      try {
        live = await api.live();
      } catch (e) {
        this.setError(body, "Could not reach the console API: " + e.message,
          () => this.pollLive());
        return;
      }
      this.state.live = live;
      if (live.active) {
        this.renderLiveRunning(body, live);
        if (!this.livePollingInterval) {
          this.livePollingInterval = setInterval(
            () => this.pollLive(), 2000);
        }
        return;
      }
      // Terminal / idle: stop polling, keep the final state visible.
      this.stopLivePolling();
      let st = null;
      try {
        st = await api.state();
        this.state.consoleState = st.status || this.state.consoleState;
        this.updateStatusIndicator();
      } catch (e) { /* final panel still renders below */ }
      this.renderLiveFinal(body, live, st || {});
    },

    renderLiveRunning(body, live) {
      body.textContent = "";
      const active = live.active || {};
      const m = live.metrics || {};
      const banner = this.ce("div", "live-banner", body);
      this.ce("span", "live-pulse", banner);
      this.ce("span", null, banner).textContent = "TEST RUNNING";
      this.statusBadge("running", banner);
      const info = this.ce("div", "detail-info-grid", body);
      [["Test ID", active.test_id], ["Target", active.target],
       ["Engine", active.engine], ["Scenario", active.scenario],
       ["Elapsed", this.fmtSec(live.elapsed_sec)],
       ["Phase", m.phase || "N/A"]].forEach(([l, val]) => {
        const item = this.ce("div", "detail-info-item", info);
        this.ce("span", "detail-info-label", item).textContent = l;
        this.ce("span", "detail-info-value", item).textContent =
          val == null || val === "" ? "N/A" : String(val);
      });
      this.ce("h2", "section-title", body).textContent = "Live Metrics";
      const grid = this.ce("div", "metrics-grid", body);
      [["Operations", this.fmtNum(m.requests)],
       ["Successful", this.fmtNum(m.successes)],
       ["Errors", this.fmtNum(m.failures)],
       ["Current rate", m.rate_per_sec == null
         ? "N/A" : m.rate_per_sec + "/s"],
       ["P95 latency", m.p95_ms == null ? "N/A" : m.p95_ms + "ms"],
       ["Error rate", m.error_rate == null
         ? "N/A" : m.error_rate + "%"]].forEach(([l, val]) => {
        const card = this.ce("div", "metric-card", grid);
        this.ce("div", "metric-label", card).textContent = l;
        this.ce("div", "metric-value", card).textContent = val;
      });
      const actions = this.ce("div", "live-actions", body);
      const stopBtn = this.ce("button", "btn btn-danger", actions);
      stopBtn.textContent = "STOP TEST";
      stopBtn.dataset.action = "stop-test";
      const emBtn = this.ce("button", "btn btn-danger-glow", actions);
      emBtn.textContent = "EMERGENCY STOP";
      emBtn.dataset.action = "emergency-stop";
    },

    renderLiveFinal(body, live, state) {
      body.textContent = "";
      const status = state.status || "idle";
      const runId = state.last_run_id || null;
      const box = this.ce("div", "live-final", body);
      this.ce("h2", "section-title", box).textContent =
        status === "idle" ? "No Active Test" : "Run Finished";
      const line = this.ce("div", "live-final-status", box);
      this.statusBadge(status, line);
      if (runId) {
        const idLine = this.ce("p", "live-final-id", box);
        idLine.textContent = "Last run: " + runId;
      }
      if (state.last_error) {
        const err = this.ce("p", "validation-error", box);
        err.textContent = "Last error: " + state.last_error;
      }
      const actions = this.ce("div", "btn-group", box);
      if (runId) {
        const btn = this.ce("button", "btn btn-primary", actions);
        btn.textContent = "VIEW TEST DETAILS";
        btn.dataset.action = "view-test";
        btn.dataset.testId = runId;
      }
      const newBtn = this.ce("button", "btn btn-success", actions);
      newBtn.textContent = "+ NEW TEST";
      newBtn.dataset.action = "new-test-nav";
    },

    stopLivePolling() {
      if (this.livePollingInterval) {
        clearInterval(this.livePollingInterval);
        this.livePollingInterval = null;
      }
    },

    async stopTest() {
      if (!confirm("Stop the current test?")) return;
      try {
        await api.stop();
        this.stopLivePolling();
        await this.loadAllData();
        if (this.currentView === "live") this.pollLive();
      } catch (e) {
        alert("Stop failed: " + e.message);
      }
    },

    async confirmEmergencyStop() {
      if (!confirm("EMERGENCY STOP: This will immediately halt all " +
          "operations. Continue?")) return;
      try {
        await api.emergencyStop();
        this.stopLivePolling();
        await this.loadAllData();
        if (this.currentView === "live") this.pollLive();
      } catch (e) {
        alert("Emergency stop failed: " + e.message);
      }
    },

    renderReports() {
      const v = document.getElementById("view-reports");
      if (!v) return;
      v.textContent = "";
      this.viewHeader(v, "Reports", "Download saved results as reports");
      const tests = this.state.tests || [];
      const completed = tests.filter(t => t.status === "completed" ||
        t.status === "stopped" || t.status === "emergency_stopped");
      if (completed.length === 0) {
        this.setEmpty(v, "No reports available",
          "Finish a test to generate a downloadable report.",
          "BROWSE TESTS", "navigate-tests");
        return;
      }
      const table = this.ce("table", "data-table", v);
      const thead = this.ce("thead", null, table);
      const hrow = this.ce("tr", null, thead);
      ["Test ID", "Target", "Score", "Status", "Downloads"].forEach(
        h => this.ce("th", null, hrow).textContent = h);
      const tbody = this.ce("tbody", null, table);
      completed.forEach(t => {
        const row = this.ce("tr", null, tbody);
        const idCell = this.ce("td", "mono", row);
        const link = this.ce("a", null, idCell);
        link.href = "#";
        link.textContent = t.test_id || "N/A";
        link.onclick = (e) => {
          e.preventDefault();
          this.viewTestDetail(t.test_id);
        };
        this.ce("td", null, row).textContent = t.target || "N/A";
        this.ce("td", null, row).textContent = this.fmtScore(t.score);
        const st = this.ce("td", null, row);
        this.statusBadge(t.status, st);
        const actions = this.ce("td", null, row);
        const base = "/api/console/tests/" +
          encodeURIComponent(t.test_id) + "/report?fmt=";
        [["JSON", "json"], ["MD", "markdown"], ["TXT", "terminal"]]
          .forEach(([label, fmt]) => {
            const a = this.ce("a", "btn btn-small", actions);
            a.href = base + fmt;
            a.textContent = label;
          });
      });
    },

    renderSettings() {
      const v = document.getElementById("view-settings");
      if (!v) return;
      v.textContent = "";
      this.viewHeader(v, "Settings", "Platform safety envelope and engines");
      const s = this.state.settings || {};
      const f = s.safety || {};
      const e = s.engines || [];
      const sc = s.scenarios || [];
      const section = this.panel(v, "Safety Envelope");
      const table = this.ce("table", "settings-table", section);
      [["Emergency Stop", f.allow_emergency_stop ? "ENABLED" : "DISABLED"],
       ["Target Allowlist", f.require_authorized_target ? "ENABLED"
                                                        : "DISABLED"],
       ["Max Duration", this.fmtSec(f.max_duration_sec)],
       ["Max Rate", this.fmtNum(f.max_rate_per_sec) + "/s"],
       ["Max Concurrency", this.fmtNum(f.max_concurrency)],
       ["Max Operations", this.fmtNum(f.max_total_operations)],
       ["Max Connections", this.fmtNum(f.max_connections)],
       ["Max Payload Bytes", this.fmtNum(f.max_payload_bytes)]
      ].forEach(([label, value]) => {
        const row = this.ce("tr", null, table);
        this.ce("td", null, row).textContent = label;
        this.ce("td", "setting-value", row).textContent = value;
      });
      const engineSection = this.panel(v, "Engine Availability");
      const engineList = this.ce("ul", "engine-list", engineSection);
      e.forEach(en => {
        const li = this.ce("li", null, engineList);
        li.textContent = en.toUpperCase() + ": AVAILABLE";
      });
      if (e.length === 0) {
        this.ce("p", "empty-message", engineSection).textContent =
          "Engine availability unavailable (settings not loaded).";
      }
      const scenarioSection = this.panel(v, "Available Scenarios");
      const scenarioList = this.ce("ul", "engine-list", scenarioSection);
      sc.forEach(name => {
        const li = this.ce("li", null, scenarioList);
        li.textContent = name;
      });
      if (sc.length === 0) {
        this.ce("p", "empty-message", scenarioSection).textContent =
          "Scenario list unavailable (settings not loaded).";
      }
      const serverSection = this.panel(v, "Server Mode");
      const serverTable = this.ce("table", "settings-table", serverSection);
      // The console is local-only by design (loopback binding enforced by
      // the CLI). We surface this from the backend payload; the value comes
      // from the server — it is never hardcoded here.
      const isLocal = s.local_only === true;
      [["Access", isLocal ? "Local / Loopback Only" : "Network (non-loopback)"],
       ["Version", s.version || "N/A"]
      ].forEach(([label, value]) => {
        const row = this.ce("tr", null, serverTable);
        this.ce("td", null, row).textContent = label;
        const td = this.ce("td", "setting-value", row);
        td.textContent = value;
        if (label === "Access") {
          td.className = isLocal
            ? "setting-value settings-badge settings-badge-local"
            : "setting-value settings-badge";
        }
      });
    },

    async performSearch(query) {
      this._showSearchPanel("loading", query);
      try {
        const res = await api.search(query);
        this.state.searchResults = res;
        this.renderSearchResults(res);
      } catch (e) {
        console.error("Search error:", e);
        this._showSearchPanel("error", query);
      }
    },

    // Build a simple transient search panel for loading / no-results / error.
    // Results panel is built by renderSearchResults; this covers the other
    // three states. All text via textContent — no HTML interpolation.
    _showSearchPanel(state, query) {
      const old = document.getElementById("search-results-panel");
      if (old) old.remove();
      const panel = document.createElement("div");
      panel.id = "search-results-panel";
      panel.className = "search-results-panel";
      const msg = document.createElement("p");
      msg.className = "search-state-msg";
      if (state === "loading") {
        msg.textContent = "Searching\u2026";
      } else if (state === "empty") {
        msg.textContent = "No results found for \u201c" + query + "\u201d";
      } else {
        msg.textContent = "Search unavailable";
        const sub = document.createElement("span");
        sub.className = "search-state-sub";
        sub.textContent = "Try again.";
        panel.appendChild(msg);
        panel.appendChild(sub);
        const topbar = document.querySelector(".topbar-left");
        if (topbar) topbar.appendChild(panel);
        const dismiss = (ev) => {
          if (!panel.contains(ev.target) && ev.target.id !== "search-input") {
            panel.remove();
            document.removeEventListener("click", dismiss);
          }
        };
        setTimeout(() => document.addEventListener("click", dismiss), 0);
        return;
      }
      panel.appendChild(msg);
      const topbar = document.querySelector(".topbar-left");
      if (topbar) topbar.appendChild(panel);
    },

    renderSearchResults(res) {
      // Remove any existing results panel (including loading state).
      const old = document.getElementById("search-results-panel");
      if (old) old.remove();
      if (!res || (!res.tests.length && !res.findings.length)) {
        this._showSearchPanel("empty", res ? res.query : "");
        return;
      }

      const panel = document.createElement("div");
      panel.id = "search-results-panel";
      panel.className = "search-results-panel";

      const header = document.createElement("div");
      header.className = "search-results-header";
      const title = document.createElement("span");
      title.textContent = "Results for \u201c" + res.query + "\u201d";
      const close = document.createElement("button");
      close.className = "search-results-close";
      close.textContent = "\u00d7";
      close.onclick = () => panel.remove();
      header.appendChild(title);
      header.appendChild(close);
      panel.appendChild(header);

      if (res.tests.length) {
        const sec = document.createElement("div");
        sec.className = "search-results-section";
        const h = document.createElement("p");
        h.className = "search-results-label";
        h.textContent = "TESTS (" + res.tests.length + ")";
        sec.appendChild(h);
        res.tests.forEach(t => {
          const row = document.createElement("div");
          row.className = "search-result-row";
          row.onclick = () => { panel.remove(); this.viewTestDetail(t.test_id); };
          const id = document.createElement("span");
          id.className = "search-result-id";
          id.textContent = t.test_id || "N/A";
          const tgt = document.createElement("span");
          tgt.className = "search-result-meta";
          tgt.textContent = (t.target || "") +
            (t.status ? "  \u00b7  " + t.status : "");
          row.appendChild(id);
          row.appendChild(tgt);
          sec.appendChild(row);
        });
        panel.appendChild(sec);
      }

      if (res.findings.length) {
        const sec = document.createElement("div");
        sec.className = "search-results-section";
        const h = document.createElement("p");
        h.className = "search-results-label";
        h.textContent = "FINDINGS (" + res.findings.length + ")";
        sec.appendChild(h);
        res.findings.forEach(f => {
          const row = document.createElement("div");
          row.className = "search-result-row";
          row.onclick = () => { panel.remove(); this.viewTestDetail(f.test_id); };
          const id = document.createElement("span");
          id.className = "search-result-id";
          id.textContent = f.test_id || "N/A";
          const meta = document.createElement("span");
          meta.className = "search-result-meta";
          meta.textContent = (f.metric || "") +
            (f.severity ? "  \u00b7  " + f.severity : "");
          row.appendChild(id);
          row.appendChild(meta);
          sec.appendChild(row);
        });
        panel.appendChild(sec);
      }

      // Dismiss on outside click
      const dismiss = (e) => {
        if (!panel.contains(e.target) &&
            e.target.id !== "search-input") {
          panel.remove();
          document.removeEventListener("click", dismiss);
        }
      };
      setTimeout(() => document.addEventListener("click", dismiss), 0);

      const topbar = document.querySelector(".topbar-left");
      if (topbar) topbar.appendChild(panel);
    }
  };

  // ------------------------------------------------------------------
  // API client — the only network surface (local /api plane)
  // ------------------------------------------------------------------
  const api = {
    async get(path) {
      const res = await fetch("/api" + path);
      if (!res.ok) {
        let message = "API error: " + res.status;
        try {
          const j = await res.json();
          if (j && j.error) message = j.error + " (" + res.status + ")";
        } catch (e) { /* keep status-only message */ }
        throw new Error(message);
      }
      return res.json();
    },
    async post(path, data) {
      const res = await fetch("/api" + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data)
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "API error: " + res.status);
      return json;
    },
    state() { return this.get("/console/state"); },
    overview() { return this.get("/console/overview"); },
    tests(q) {
      return this.get("/console/tests" + (q ? "?q=" +
        encodeURIComponent(q) : ""));
    },
    test(id) {
      return this.get("/console/tests/" + encodeURIComponent(id));
    },
    testEvents(id) {
      return this.get("/console/tests/" + encodeURIComponent(id) + "/events");
    },
    testFindings(id) {
      return this.get("/console/tests/" + encodeURIComponent(id) +
        "/findings");
    },
    testConfig(id) {
      return this.get("/console/tests/" + encodeURIComponent(id) + "/config");
    },
    findings(severity) {
      return this.get("/console/findings" + (severity ? "?severity=" +
        encodeURIComponent(severity) : ""));
    },
    settings() { return this.get("/console/settings"); },
    live() { return this.get("/console/live"); },
    search(q) {
      return this.get("/console/search?q=" + encodeURIComponent(q));
    },
    validate(config) { return this.post("/console/validate", config); },
    start(config) { return this.post("/console/start", config); },
    stop() { return this.post("/console/stop", {}); },
    emergencyStop() { return this.post("/console/emergency-stop", {}); }
  };

  window.app = app;
  window.api = api;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => app.init());
  } else {
    app.init();
  }
})();













