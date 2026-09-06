"use strict";
(function() {
  const app = {
    currentView: "overview",
    currentTestId: null,
    livePollingInterval: null,
    state: {
      overview: null,
      tests: [],
      findings: [],
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
          api.state(), api.overview(), api.tests(), api.findings(), api.settings()
        ]);
        this.state.consoleState = s.state;
        this.state.overview = o;
        this.state.tests = t.tests || [];
        this.state.findings = f.findings || [];
        this.state.settings = st;
        this.updateStatusIndicator();
      } catch(e) {
        console.error("Load error:", e);
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
        if (e.target.classList.contains("nav-item")) {
          document.querySelectorAll(".nav-item").forEach(b => b.classList.remove("active"));
          e.target.classList.add("active");
          this.render(e.target.dataset.view);
        }
        if (e.target.dataset.action === "stop-test") this.stopTest();
        if (e.target.dataset.action === "emergency-stop") this.confirmEmergencyStop();
        if (e.target.dataset.action === "view-test") this.viewTestDetail(e.target.dataset.testId);
        if (e.target.dataset.action === "new-test-nav") {
          document.querySelectorAll(".nav-item").forEach(b => b.classList.remove("active"));
          const nt = document.querySelector("[data-view='new-test']");
          if (nt) nt.classList.add("active");
          this.render("new-test");
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
    renderOverview() {
      const v = document.getElementById("view-overview");
      if (!v) return;
      const ov = this.state.overview || {};
      v.textContent = "";
      const header = this.ce("div", "view-header", v);
      this.ce("h1", null, header).textContent = "Overview";
      this.ce("p", null, header).textContent = "Resilience Operations Console";
      const grid = this.ce("div", "overview-grid", v);
      const kpis = [
        ["Tests", ov.total_tests || 0],
        ["Latest Score", ov.latest_score != null ? ov.latest_score.toFixed(1) : "—"],
        ["Findings", this.state.findings.length || 0],
        ["Active Test", ov.active_test_id || "NONE"]
      ];
      kpis.forEach(([label, value]) => {
        const card = this.ce("div", "kpi-card", grid);
        this.ce("div", "kpi-label", card).textContent = label;
        this.ce("div", "kpi-value", card).textContent = value;
      });
      const section = this.ce("section", "panel", v);
      this.ce("h2", "panel-title", section).textContent = "Recent Tests";
      this.ce("div", "recent-tests", section);
      this.populateRecentTests();
    },

    populateRecentTests(container) {
      if (!container) container = document.getElementById("recent-tests");
      if (!container) return;
      container.textContent = "";
      const table = this.ce("table", "data-table", container);
      const thead = this.ce("thead", null, table);
      const hrow = this.ce("tr", null, thead);
      ["Status", "ID", "Target", "Engine", "Score"].forEach(h => this.ce("th", null, hrow).textContent = h);
      const tbody = this.ce("tbody", null, table);
      (this.state.tests || []).slice(0, 5).forEach(t => {
        const row = this.ce("tr", null, tbody);
        const sc = this.ce("td", null, row);
        const ss = this.ce("span", "status-badge " + (t.status || ""), sc);
        ss.textContent = t.status || "—";
        const ic = this.ce("td", null, row);
        const il = this.ce("a", null, ic);
        il.href = "#";
        il.textContent = t.id || "—";
        il.dataset.action = "view-test";
        il.dataset.testId = t.id || "";
        this.ce("td", null, row).textContent = t.target || "—";
        this.ce("td", null, row).textContent = t.engine || "—";
        this.ce("td", null, row).textContent = t.score != null ? t.score.toFixed(1) : "—";
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
      const header = this.ce("div", "view-header", v);
      this.ce("h1", null, header).textContent = "New Test";
      this.ce("p", null, header).textContent = "Configure a controlled resilience test";
      const form = this.ce("form", "test-form", v);
      form.id = "f";
      // Target
      const tg = this.ce("div", "form-group", form);
      this.ce("label", null, tg).textContent = "Target *";
      const ti = this.ce("input", null, tg);
      ti.type = "text"; ti.id = "target"; ti.required = true; ti.placeholder = "127.0.0.1";
      // Engine
      const eg = this.ce("div", "form-group", form);
      this.ce("label", null, eg).textContent = "Engine *";
      const es = this.ce("select", null, eg);
      es.id = "engine"; es.required = true;
      this.ce("option", null, es).textContent = "— Select Engine —";
      e.forEach(x => { const o = this.ce("option", null, es); o.value = x; o.textContent = x.toUpperCase(); });
      // Scenario
      const sg = this.ce("div", "form-group", form);
      this.ce("label", null, sg).textContent = "Scenario *";
      const ss = this.ce("select", null, sg);
      ss.id = "scenario"; ss.required = true;
      this.ce("option", null, ss).textContent = "— Select Scenario —";
      c.forEach(x => { const o = this.ce("option", null, ss); o.value = x; o.textContent = x.replace(/-/g, " "); });
      // Duration
      const dg = this.ce("div", "form-group", form);
      this.ce("label", null, dg).textContent = "Duration (s)";
      const di = this.ce("input", null, dg);
      di.type = "number"; di.id = "duration"; di.value = "30"; di.min = 1; di.max = f.max_duration_sec || 120;
      this.ce("small", "form-hint", dg).textContent = "Max: " + (f.max_duration_sec || 120) + "s";
      // Start Rate
      const srg = this.ce("div", "form-group", form);
      this.ce("label", null, srg).textContent = "Start Rate";
      const sri = this.ce("input", null, srg);
      sri.type = "number"; sri.id = "start_rate"; sri.value = "10"; sri.min = 1;
      this.ce("small", "form-hint", srg).textContent = "Max: " + (f.max_rate_per_sec || 1000);
      // Max Rate
      const mrg = this.ce("div", "form-group", form);
      this.ce("label", null, mrg).textContent = "Max Rate";
      const mri = this.ce("input", null, mrg);
      mri.type = "number"; mri.id = "max_rate"; mri.value = "50"; mri.min = 1;
      this.ce("small", "form-hint", mrg).textContent = "Max: " + (f.max_rate_per_sec || 1000);
      // Concurrency
      const cg = this.ce("div", "form-group", form);
      this.ce("label", null, cg).textContent = "Concurrency";
      const ci = this.ce("input", null, cg);
      ci.type = "number"; ci.id = "concurrency"; ci.value = "5"; ci.min = 1;
      this.ce("small", "form-hint", cg).textContent = "Max: " + (f.max_concurrency || 100);
      // Validation result
      this.ce("div", "validation-result", form).id = "validation-result";
      // Buttons
      const bg = this.ce("div", "btn-group", form);
      const vb = this.ce("button", "btn btn-primary", bg);
      vb.type = "button"; vb.id = "vbtn"; vb.textContent = "VALIDATE";
      const rb = this.ce("button", "btn btn-success", bg);
      rb.type = "button"; rb.id = "rbtn"; rb.textContent = "RUN TEST"; rb.disabled = true;
      // Safety panel
      const sp = this.ce("div", "safety-panel", v);
      this.ce("h3", null, sp).textContent = "Safety Envelope";
      const sl = this.ce("ul", "safety-list", sp);
      ["Emergency Stop: ENABLED", "Target Allowlist: ENABLED", "Max Duration: " + (f.max_duration_sec || 120) + "s", "Max Rate: " + (f.max_rate_per_sec || 1000), "Max Concurrency: " + (f.max_concurrency || 100), "Max Operations: " + (f.max_operations || 10000), "Max Connections: " + (f.max_connections || 50)].forEach(i => this.ce("li", null, sl).textContent = i);
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
        } catch(e) {
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
        } catch(e) {
          alert("Failed: " + e.message);
          rb.textContent = "RUN TEST";
          rb.disabled = false;
        }
      };
    },

    collectFormData() {
      const t = document.getElementById("target");
      const e = document.getElementById("engine");
      const s = document.getElementById("scenario");
      if (!t || !t.value.trim()) { this.displayValidationError("Target is required"); return null; }
      if (!e || !e.value) { this.displayValidationError("Engine is required"); return null; }
      if (!s || !s.value) { this.displayValidationError("Scenario is required"); return null; }
      const config = { target: t.value.trim(), engine: e.value, scenario: s.value };
      const d = document.getElementById("duration");
      if (d && d.value) config.duration_sec = parseInt(d.value, 10) || 30;
      const sr = document.getElementById("start_rate");
      if (sr && sr.value) config.start_rate = parseInt(sr.value, 10) || 10;
      const mr = document.getElementById("max_rate");
      if (mr && mr.value) config.max_rate = parseInt(mr.value, 10) || 50;
      const c = document.getElementById("concurrency");
      if (c && c.value) config.concurrency = parseInt(c.value, 10) || 5;
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
        if (result.checks) {
          const list = this.ce("ul", "validation-checks", el);
          result.checks.forEach(check => {
            const li = this.ce("li", null, list);
            li.textContent = (check.passed ? "[PASS] " : "[FAIL] ") + (check.message || check.name || "Check");
          });
        }
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
    renderLive() {
      const v = document.getElementById("view-live");
      if (!v) return;
      v.textContent = "";
      const header = this.ce("div", "view-header", v);
      this.ce("h1", null, header).textContent = "Live Feed";
      const isRunning = this.state.consoleState === "running" || this.state.consoleState === "starting";
      if (!isRunning) {
        const empty = this.ce("div", "empty-state", v);
        this.ce("p", null, empty).textContent = "No Active Test";
        this.ce("p", null, empty).textContent = "There is currently no running resilience test.";
        const btn = this.ce("button", "btn btn-primary", empty);
        btn.textContent = "+ New Test";
        btn.dataset.action = "new-test-nav";
        return;
      }
      const ls = this.state.live || {};
      const grid = this.ce("div", "live-info-grid", v);
      [["Status", ls.status || "Running"], ["Test ID", ls.test_id || "—"], ["Target", ls.target || "—"], ["Engine", ls.engine || "—"], ["Scenario", ls.scenario || "—"], ["Elapsed", ls.elapsed_sec ? ls.elapsed_sec + "s" : "—"]].forEach(([label, value]) => {
        const item = this.ce("div", "live-info-item", grid);
        this.ce("span", "live-info-label", item).textContent = label;
        this.ce("span", "live-info-value", item).textContent = value;
      });
      const metrics = this.ce("div", "live-metrics", v);
      this.ce("h2", "section-title", metrics).textContent = "Live Metrics";
      const mg = this.ce("div", "metrics-grid", metrics);
      [["Operations", ls.operations || 0], ["Successful", ls.successful || 0], ["Errors", ls.errors || 0], ["Current Rate", ls.current_rate || 0], ["P95 Latency", ls.p95_latency_ms ? ls.p95_latency_ms + "ms" : "—"], ["Error Rate", ls.error_rate ? (ls.error_rate * 100).toFixed(1) + "%" : "—"]].forEach(([label, value]) => {
        const card = this.ce("div", "metric-card", mg);
        this.ce("div", "metric-label", card).textContent = label;
        this.ce("div", "metric-value", card).textContent = value;
      });
      const actions = this.ce("div", "live-actions", v);
      const stopBtn = this.ce("button", "btn btn-danger", actions);
      stopBtn.textContent = "STOP TEST";
      stopBtn.dataset.action = "stop-test";
      const emBtn = this.ce("button", "btn btn-danger-glow", actions);
      emBtn.textContent = "EMERGENCY STOP";
      emBtn.dataset.action = "emergency-stop";
      this.startLivePolling();
    },

    async startLivePolling() {
      if (this.livePollingInterval) return;
      this.livePollingInterval = setInterval(async () => {
        try {
          const state = await api.state();
          this.state.consoleState = state.state;
          this.updateStatusIndicator();
          if (state.state !== "running" && state.state !== "starting") {
            this.stopLivePolling();
            await this.loadAllData();
            if (this.currentView === "live") this.render("tests");
            return;
          }
          const live = await api.live();
          this.state.live = live;
        } catch(e) {
          console.error("Live poll error:", e);
        }
      }, 2000);
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
        this.render("tests");
      } catch(e) {
        alert("Stop failed: " + e.message);
      }
    },

    async confirmEmergencyStop() {
      if (!confirm("EMERGENCY STOP: This will immediately halt all operations. Continue?")) return;
      try {
        await api.emergencyStop();
        this.stopLivePolling();
        await this.loadAllData();
        this.render("tests");
      } catch(e) {
        alert("Emergency stop failed: " + e.message);
      }
    },
    renderTests() {
      const v = document.getElementById("view-tests");
      if (!v) return;
      v.textContent = "";
      const header = this.ce("div", "view-header", v);
      this.ce("h1", null, header).textContent = "Tests";
      const tests = this.state.tests || [];
      if (tests.length === 0) {
        const empty = this.ce("div", "empty-state", v);
        this.ce("p", null, empty).textContent = "No tests recorded yet.";
        const btn = this.ce("button", "btn btn-primary", empty);
        btn.textContent = "+ New Test";
        btn.dataset.action = "new-test-nav";
        return;
      }
      const table = this.ce("table", "data-table", v);
      const thead = this.ce("thead", null, table);
      const hrow = this.ce("tr", null, thead);
      ["Status", "ID", "Target", "Engine", "Scenario", "Score", "Duration"].forEach(h => this.ce("th", null, hrow).textContent = h);
      const tbody = this.ce("tbody", null, table);
      tests.forEach(t => {
        const row = this.ce("tr", null, tbody);
        row.style.cursor = "pointer";
        row.onclick = () => this.viewTestDetail(t.id);
        const sc = this.ce("td", null, row);
        const ss = this.ce("span", "status-badge " + (t.status || ""), sc);
        ss.textContent = t.status || "—";
        this.ce("td", null, row).textContent = t.id || "—";
        this.ce("td", null, row).textContent = t.target || "—";
        this.ce("td", null, row).textContent = t.engine || "—";
        this.ce("td", null, row).textContent = t.scenario || "—";
        this.ce("td", null, row).textContent = t.score != null ? t.score.toFixed(1) : "—";
        this.ce("td", null, row).textContent = t.duration_sec ? t.duration_sec + "s" : "—";
      });
    },

    async viewTestDetail(testId) {
      this.currentTestId = testId;
      this.render("test-detail");
    },

    renderTestDetail() {
      const v = document.getElementById("view-test-detail");
      if (!v) return;
      v.textContent = "";
      const test = this.state.tests.find(t => t.id === this.currentTestId);
      const header = this.ce("div", "view-header", v);
      const back = this.ce("a", "back-link", header);
      back.href = "#";
      back.textContent = "< All Tests";
      back.onclick = (e) => { e.preventDefault(); this.render("tests"); };
      if (!test) {
        this.ce("p", null, v).textContent = "Test not found";
        return;
      }
      this.ce("h1", null, header).textContent = test.id || "Test Detail";
      const ss = this.ce("span", "status-badge " + (test.status || ""), header);
      ss.textContent = test.status || "—";
      const info = this.ce("div", "detail-info-grid", v);
      [["Target", test.target || "—"], ["Engine", test.engine || "—"], ["Scenario", test.scenario || "—"], ["Duration", test.duration_sec ? test.duration_sec + "s" : "—"], ["Score", test.score != null ? test.score.toFixed(1) : "—"]].forEach(([label, value]) => {
        const item = this.ce("div", "detail-info-item", info);
        this.ce("span", "detail-info-label", item).textContent = label;
        this.ce("span", "detail-info-value", item).textContent = value;
      });
      const actions = this.ce("div", "detail-actions", v);
      const repBtn = this.ce("button", "btn btn-primary", actions);
      repBtn.textContent = "View Report";
      repBtn.onclick = () => this.render("reports");
      if (test.status === "running") {
        const stopBtn = this.ce("button", "btn btn-danger", actions);
        stopBtn.textContent = "Stop Test";
        stopBtn.dataset.action = "stop-test";
      }
      const findingsSection = this.ce("section", "panel", v);
      this.ce("h2", "panel-title", findingsSection).textContent = "Findings";
      const findingsList = this.ce("div", "findings-list", findingsSection);
      const testFindings = this.state.findings.filter(f => f.test_id === test.id);
      if (testFindings.length === 0) {
        this.ce("p", "empty-message", findingsList).textContent = "No findings for this test";
      } else {
        testFindings.forEach(f => {
          const item = this.ce("div", "finding-item", findingsList);
          const sev = this.ce("span", "severity-badge " + (f.severity || "info"), item);
          sev.textContent = f.severity || "info";
          this.ce("p", null, item).textContent = f.message || f.description || "—";
        });
      }
    },
    renderFindings() {
      const v = document.getElementById("view-findings");
      if (!v) return;
      v.textContent = "";
      const header = this.ce("div", "view-header", v);
      this.ce("h1", null, header).textContent = "Findings";
      const findings = this.state.findings || [];
      if (findings.length === 0) {
        this.ce("p", "empty-state", v).textContent = "No findings recorded";
        return;
      }
      const list = this.ce("div", "findings-list", v);
      findings.forEach(f => {
        const item = this.ce("div", "finding-item", list);
        const sev = this.ce("span", "severity-badge " + (f.severity || "info"), item);
        sev.textContent = f.severity || "info";
        this.ce("p", null, item).textContent = f.message || f.description || "—";
        if (f.test_id) {
          const link = this.ce("a", null, item);
          link.href = "#";
          link.textContent = "Test: " + f.test_id;
          link.onclick = (e) => { e.preventDefault(); this.viewTestDetail(f.test_id); };
        }
      });
    },
    renderReports() {
      const v = document.getElementById("view-reports");
      if (!v) return;
      v.textContent = "";
      const header = this.ce("div", "view-header", v);
      this.ce("h1", null, header).textContent = "Reports";
      const tests = this.state.tests || [];
      const completed = tests.filter(t => t.status === "completed" || t.status === "stopped");
      if (completed.length === 0) {
        this.ce("p", "empty-state", v).textContent = "No reports available";
        return;
      }
      const table = this.ce("table", "data-table", v);
      const thead = this.ce("thead", null, table);
      const hrow = this.ce("tr", null, thead);
      ["Test ID", "Target", "Score", "Status", "Actions"].forEach(h => this.ce("th", null, hrow).textContent = h);
      const tbody = this.ce("tbody", null, table);
      completed.forEach(t => {
        const row = this.ce("tr", null, tbody);
        this.ce("td", null, row).textContent = t.id || "—";
        this.ce("td", null, row).textContent = t.target || "—";
        this.ce("td", null, row).textContent = t.score != null ? t.score.toFixed(1) : "—";
        this.ce("td", null, row).textContent = t.status || "—";
        const actions = this.ce("td", null, row);
        const viewBtn = this.ce("button", "btn btn-small", actions);
        viewBtn.textContent = "View";
        viewBtn.onclick = () => this.viewTestDetail(t.id);
      });
    },

    renderSettings() {
      const v = document.getElementById("view-settings");
      if (!v) return;
      v.textContent = "";
      const header = this.ce("div", "view-header", v);
      this.ce("h1", null, header).textContent = "Settings";
      const s = this.state.settings || {};
      const f = s.safety || {};
      const e = s.engines || [];
      const section = this.ce("section", "panel", v);
      this.ce("h2", "panel-title", section).textContent = "Safety Envelope";
      const table = this.ce("table", "settings-table", section);
      [["Emergency Stop", "ENABLED"], ["Target Allowlist", "ENABLED"], ["Max Duration", (f.max_duration_sec || 120) + "s"], ["Max Rate", (f.max_rate_per_sec || 1000) + "/s"], ["Max Concurrency", f.max_concurrency || 100], ["Max Operations", f.max_operations || 10000], ["Max Connections", f.max_connections || 50]].forEach(([label, value]) => {
        const row = this.ce("tr", null, table);
        this.ce("td", null, row).textContent = label;
        this.ce("td", "setting-value", row).textContent = value;
      });
      const engineSection = this.ce("section", "panel", v);
      this.ce("h2", "panel-title", engineSection).textContent = "Engine Availability";
      const engineList = this.ce("ul", "engine-list", engineSection);
      ["http", "api", "database", "mobile"].forEach(en => {
        const li = this.ce("li", null, engineList);
        li.textContent = en.toUpperCase() + ": " + (e.includes(en) ? "AVAILABLE" : "NOT AVAILABLE");
      });
    },

    async performSearch(query) {
      try {
        const res = await api.search(query);
        this.state.searchResults = res;
      } catch(e) {
        console.error("Search error:", e);
      }
    }
  };
  // API client
  const api = {
    async get(path) {
      const res = await fetch("/api" + path);
      if (!res.ok) throw new Error("API error: " + res.status);
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
    tests() { return this.get("/console/tests"); },
    findings() { return this.get("/console/findings"); },
    settings() { return this.get("/console/settings"); },
    live() { return this.get("/console/live"); },
    search(q) { return this.get("/console/search?q=" + encodeURIComponent(q)); },
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