// Forecasting dashboard UI. Calculations come from forecast.js (window.Forecast).
(function () {
  "use strict";

  var F = window.Forecast;
  var live = document.body.dataset.mode === "live";
  var $ = function (id) { return document.getElementById(id); };
  var DEFAULT_HORIZON = { day: 30, week: 13, month: 12, quarter: 8, year: 5 };
  var UNIT = { day: "days", week: "weeks", month: "months", quarter: "quarters", year: "years" };

  var state = {
    table: null, dateCol: -1, valueCol: -1, series: null, label: "", source: "",
    horizon: 12, season: "auto", model: "best", band: "both", showBacktest: true, showFitted: false,
    scenario: { growth: 0, shock: 0, shockStart: 1, shockLength: 0 },
    result: null, hover: null, projectId: null,
  };

  // Small helpers ------------------------------------------------------------------------

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k]; else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { node.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return node;
  }

  function say(message) {
    var toast = document.querySelector(".toast");
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(say.timer);
    say.timer = setTimeout(function () { toast.classList.remove("show"); }, 2400);
  }

  var compact = typeof Intl !== "undefined" ? new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }) : null;
  function short(v) {
    if (!isFinite(v)) return "—";
    return compact ? compact.format(v) : String(Math.round(v));
  }
  function full(v) {
    if (!isFinite(v)) return "—";
    var a = Math.abs(v);
    return v.toLocaleString(undefined, { maximumFractionDigits: a >= 100 ? 0 : a >= 1 ? 2 : 4 });
  }
  function pct(v, signed) {
    if (!isFinite(v)) return "—";
    return (signed && v > 0 ? "+" : "") + v.toFixed(Math.abs(v) < 10 ? 1 : 0) + "%";
  }

  function alpha(color, a) {
    if (/^#[0-9a-f]{6}$/i.test(color)) {
      var n = parseInt(color.slice(1), 16);
      return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
    }
    return color;
  }

  function slug(s) { return (s || "forecast").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "forecast"; }

  // Data loading -------------------------------------------------------------------------

  function option(select, value, text, selected) {
    var o = el("option", { value: value, text: text });
    if (selected) o.selected = true;
    select.appendChild(o);
  }

  function loadText(text, label, source) {
    var table = F.readTable(text);
    if (!table.rows.length) { say("No rows found in that data."); return false; }
    var guess = F.guessColumns(table);
    if (guess.date < 0 || !guess.values.length) {
      say("Couldn't find a date column and a number column.");
      return false;
    }
    state.table = table;
    state.dateCol = guess.date;
    state.valueCol = guess.values[0];
    state.label = label;
    state.source = source;
    state.projectId = null;
    fillColumns();
    buildSeries(true);
    return true;
  }

  function fillColumns() {
    var dateSel = $("fc-date-col"), valueSel = $("fc-value-col");
    dateSel.textContent = ""; valueSel.textContent = "";
    state.table.columns.forEach(function (name, i) {
      option(dateSel, i, name, i === state.dateCol);
      option(valueSel, i, name, i === state.valueCol);
    });
  }

  function buildSeries(resetHorizon) {
    var series = F.toSeries(state.table, state.dateCol, state.valueCol);
    state.series = series;
    var warnings = $("fc-warnings");
    warnings.textContent = "";
    series.warnings.forEach(function (w) { warnings.appendChild(el("li", { text: w })); });
    if (series.values.length) {
      $("fc-data-info").textContent = series.values.length + " " + F.FREQS[series.freq].label + " points, " +
        F.formatDate(series.dates[0], series.freq) + " to " + F.formatDate(series.dates[series.dates.length - 1], series.freq) + ".";
    } else {
      $("fc-data-info").textContent = "";
    }
    if (resetHorizon) {
      state.horizon = Math.min(DEFAULT_HORIZON[series.freq] || 12, Math.max(1, Math.floor(series.values.length / 2)));
      state.season = "auto";
      state.scenario = { growth: 0, shock: 0, shockStart: 1, shockLength: 0 };
    }
    syncControls();
    compute();
  }

  // Forecast -----------------------------------------------------------------------------

  function seasonLength() {
    if (!state.series) return 0;
    if (state.season === "auto") return F.FREQS[state.series.freq].season;
    return +state.season;
  }

  function compute() {
    var s = state.series;
    if (!s || s.values.length < 6) {
      state.result = null;
      render();
      return;
    }
    var m = seasonLength();
    if (m > 1 && s.values.length < 2 * m) m = 0;
    state.result = F.run(s, { horizon: state.horizon, season: m });
    if (state.model !== "best" && !current(state.model)) state.model = "best";
    render();
  }

  function current(id) {
    if (!state.result) return null;
    id = id || state.model;
    if (id === "best") id = state.result.best;
    return state.result.models.find(function (m) { return m.id === id && m.available; }) || null;
  }

  function scenarioActive() {
    var sc = state.scenario;
    return sc.growth !== 0 || sc.shock !== 0;
  }

  function adjusted(model) {
    var sc = state.scenario;
    return F.scenario(model, {
      growth: sc.growth, shock: sc.shock, shockStart: sc.shockStart, shockLength: sc.shockLength,
      perYear: F.FREQS[state.series.freq].perYear,
    });
  }

  // Controls -----------------------------------------------------------------------------

  function syncControls() {
    var s = state.series, freq = s ? s.freq : "month";
    var horizon = $("fc-horizon");
    horizon.max = Math.max(12, Math.min(120, (DEFAULT_HORIZON[freq] || 12) * 4));
    horizon.value = state.horizon;
    $("fc-horizon-out").textContent = state.horizon + " " + (UNIT[freq] || "periods");

    var season = $("fc-season");
    season.textContent = "";
    var auto = F.FREQS[freq].season;
    option(season, "auto", "Automatic (" + (auto ? "every " + auto + " " + UNIT[freq] : "none") + ")", state.season === "auto");
    option(season, "0", "None", state.season === "0");
    [4, 7, 12, 52].forEach(function (n) { option(season, String(n), "Every " + n + " periods", state.season === String(n)); });

    ["fc-shock-start", "fc-shock-length"].forEach(function (id) { $(id).max = state.horizon; });
    var sc = state.scenario;
    sc.shockStart = Math.min(sc.shockStart, state.horizon);
    sc.shockLength = Math.min(sc.shockLength, state.horizon);
    $("fc-growth").value = sc.growth; $("fc-growth-out").textContent = pct(sc.growth, true);
    $("fc-shock").value = sc.shock; $("fc-shock-out").textContent = pct(sc.shock, true);
    $("fc-shock-start").value = sc.shockStart; $("fc-shock-start-out").textContent = sc.shockStart;
    $("fc-shock-length").value = sc.shockLength;
    $("fc-shock-length-out").textContent = sc.shockLength ? sc.shockLength + " " + (UNIT[freq] || "periods") : "rest";
    $("fc-band").value = state.band;
  }

  function fillModels() {
    var select = $("fc-model");
    select.textContent = "";
    var best = current("best");
    option(select, "best", "Best by backtest" + (best ? " (" + best.name + ")" : ""), state.model === "best");
    (state.result ? state.result.models : []).forEach(function (m) {
      if (m.available) option(select, m.id, m.name, state.model === m.id);
    });
  }

  function bind() {
    document.querySelectorAll(".segmented button").forEach(function (b) {
      b.addEventListener("click", function () {
        document.querySelectorAll(".segmented button").forEach(function (x) { x.setAttribute("aria-selected", String(x === b)); });
        document.querySelectorAll(".pane").forEach(function (p) { p.hidden = p.dataset.pane !== b.dataset.source; });
      });
    });

    var sample = $("fc-sample");
    F.SAMPLES.forEach(function (s) { option(sample, s[0], s[1]); });
    sample.addEventListener("change", function () { loadSample(sample.value); });

    var file = $("fc-file"), drop = $("fc-drop");
    function readFile(f) {
      if (!f) return;
      if (f.size > 20 * 1024 * 1024) { say("That file is over 20 MB."); return; }
      var reader = new FileReader();
      reader.onload = function () { if (loadText(String(reader.result), f.name.replace(/\.[^.]+$/, ""), "upload")) say("Loaded " + f.name); };
      reader.readAsText(f);
    }
    file.addEventListener("change", function () { readFile(file.files[0]); });
    ["dragenter", "dragover"].forEach(function (t) { drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
    ["dragleave", "drop"].forEach(function (t) { drop.addEventListener(t, function () { drop.classList.remove("over"); }); });
    drop.addEventListener("drop", function (e) { e.preventDefault(); readFile(e.dataTransfer.files[0]); });

    $("fc-paste-go").addEventListener("click", function () {
      if (loadText($("fc-paste").value, "Pasted data", "paste")) say("Pasted data loaded");
    });

    $("fc-date-col").addEventListener("change", function (e) { state.dateCol = +e.target.value; buildSeries(true); });
    $("fc-value-col").addEventListener("change", function (e) { state.valueCol = +e.target.value; buildSeries(false); });

    $("fc-horizon").addEventListener("input", function (e) { state.horizon = +e.target.value; syncControls(); compute(); });
    $("fc-season").addEventListener("change", function (e) { state.season = e.target.value; compute(); });
    $("fc-model").addEventListener("change", function (e) { state.model = e.target.value; render(); });
    $("fc-band").addEventListener("change", function (e) { state.band = e.target.value; drawChart(); });
    $("fc-show-backtest").addEventListener("change", function (e) { state.showBacktest = e.target.checked; drawChart(); });
    $("fc-show-fitted").addEventListener("change", function (e) { state.showFitted = e.target.checked; drawChart(); });

    [["fc-growth", "growth"], ["fc-shock", "shock"], ["fc-shock-start", "shockStart"], ["fc-shock-length", "shockLength"]].forEach(function (pair) {
      $(pair[0]).addEventListener("input", function (e) { state.scenario[pair[1]] = +e.target.value; syncControls(); render(); });
    });
    $("fc-scenario-reset").addEventListener("click", function () {
      state.scenario = { growth: 0, shock: 0, shockStart: 1, shockLength: 0 };
      syncControls(); render();
    });

    $("fc-export").addEventListener("click", exportCSV);
    $("fc-print").addEventListener("click", function () { window.print(); });

    var canvas = $("fc-chart");
    canvas.addEventListener("pointermove", function (e) {
      var r = canvas.getBoundingClientRect();
      state.hover = { x: e.clientX - r.left };
      drawChart();
    });
    canvas.addEventListener("pointerleave", function () { state.hover = null; drawChart(); });

    if (live) {
      $("fc-save").addEventListener("click", saveProject);
      loadProjects();
    }
    window.addEventListener("resize", function () { requestAnimationFrame(drawChart); });
    document.addEventListener("themechange", function () { requestAnimationFrame(drawChart); });
    if (window.matchMedia) {
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      if (mq.addEventListener) mq.addEventListener("change", drawChart);
    }
  }

  function loadSample(id) {
    var name = F.SAMPLES.find(function (s) { return s[0] === id; });
    loadText(F.sampleCSV(id), name ? name[1].replace(/ \(.*/, "") : id, "sample");
  }

  // Rendering ----------------------------------------------------------------------------

  function render() {
    fillModels();
    renderKpis();
    renderModels();
    renderTable();
    drawChart();
    var model = current(), s = state.series;
    $("fc-chart-title").textContent = state.label ? state.label + " · " + (state.table.columns[state.valueCol] || "") : "Forecast";
    $("fc-report-title").textContent = "Forecast: " + (($("fc-name") && $("fc-name").value) || state.label || "series");
    $("fc-report-sub").textContent = model && s
      ? model.name + " model · " + state.horizon + " " + UNIT[s.freq] + " ahead · " + s.values.length + " historical points · generated " + new Date().toLocaleDateString()
      : "";
  }

  function kpi(value, label, note) {
    return el("div", { class: "stat" }, [
      el("span", { class: "stat-value", text: value }),
      el("span", { class: "stat-label", text: label }),
    ].concat(note ? [el("span", { class: "stat-note", text: note })] : []));
  }

  function renderKpis() {
    var box = $("fc-kpis"), s = state.series, model = current();
    box.textContent = "";
    if (!s || !model) {
      box.appendChild(el("div", { class: "card empty wide", text: s && s.values.length ? "Need at least 6 data points to forecast." : "Choose a sample, upload a CSV, or paste data to begin." }));
      return;
    }
    var view = scenarioActive() ? adjusted(model) : model;
    var n = s.values.length, h = state.horizon, unit = UNIT[s.freq];
    var total = view.forecast.reduce(function (a, b) { return a + b; }, 0);
    var prev = s.values.slice(Math.max(0, n - h)), prevTotal = prev.reduce(function (a, b) { return a + b; }, 0);
    var change = prev.length === h && prevTotal ? (100 * (total - prevTotal)) / Math.abs(prevTotal) : NaN;
    var end = view.forecast[h - 1];
    box.appendChild(kpi(short(s.values[n - 1]), "Latest actual", F.formatDate(s.dates[n - 1], s.freq)));
    box.appendChild(kpi(short(total), "Next " + h + " " + unit + " total", isFinite(change) ? pct(change, true) + " vs previous " + h : ""));
    box.appendChild(kpi(short(end), "Forecast for " + F.formatDate(F.futureDates(s, h)[h - 1], s.freq), "80%: " + short(view.lo80[h - 1]) + " – " + short(view.hi80[h - 1])));
    box.appendChild(kpi(isFinite(model.metrics.mape) ? "±" + pct(model.metrics.mape) : short(model.metrics.mae), "Typical error", model.name + " backtest"));
  }

  function renderModels() {
    var table = $("fc-models"), res = state.result;
    table.textContent = "";
    $("fc-models-note").textContent = res
      ? "Each model was trained without the last " + res.holdout + " " + UNIT[state.series.freq] + ", then scored on how close it came to them. Click a row to use that model." +
        (res.season ? " Seasonal cycle: " + res.season + " periods." : " No seasonal cycle used.")
      : "";
    if (!res) return;
    var head = el("tr", {}, ["#", "Model", "Typical % error", "MAE", "RMSE", "Bias"].map(function (h) { return el("th", { text: h }); }));
    table.appendChild(el("thead", {}, [head]));
    var body = el("tbody");
    var chosen = current();
    res.models.slice().sort(function (a, b) { return (a.rank || 99) - (b.rank || 99); }).forEach(function (m) {
      var name = el("td", {}, [el("strong", { text: m.name }), m.id === res.best ? el("span", { class: "badge badge-accent", text: "Best" }) : "", el("div", { class: "muted small", text: m.about })]);
      var row = m.available
        ? el("tr", { tabindex: "0", "aria-selected": String(chosen && chosen.id === m.id) }, [
          el("td", { text: String(m.rank) }), name,
          el("td", { text: isFinite(m.metrics.mape) ? "±" + pct(m.metrics.mape) : "—" }),
          el("td", { text: full(m.metrics.mae) }), el("td", { text: full(m.metrics.rmse) }),
          el("td", { text: (m.metrics.bias > 0 ? "+" : "") + full(m.metrics.bias) }),
        ])
        : el("tr", { class: "disabled" }, [el("td", { text: "–" }), name, el("td", { colspan: "4", class: "muted", text: "Not run: " + m.reason })]);
      if (m.available) {
        var pick = function () { state.model = m.id === res.best ? "best" : m.id; render(); };
        row.addEventListener("click", pick);
        row.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
      }
      body.appendChild(row);
    });
    table.appendChild(body);
  }

  function renderTable() {
    var table = $("fc-table"), s = state.series, model = current();
    table.textContent = "";
    if (!s || !model) return;
    var dates = F.futureDates(s, state.horizon), sc = scenarioActive() ? adjusted(model) : null;
    var heads = ["Period", "Forecast", "80% range", "95% range"].concat(sc ? ["Scenario"] : []);
    table.appendChild(el("thead", {}, [el("tr", {}, heads.map(function (h) { return el("th", { text: h }); }))]));
    var body = el("tbody");
    dates.forEach(function (d, k) {
      var cells = [F.formatDate(d, s.freq), full(model.forecast[k]), full(model.lo80[k]) + " – " + full(model.hi80[k]), full(model.lo95[k]) + " – " + full(model.hi95[k])];
      if (sc) cells.push(full(sc.forecast[k]));
      body.appendChild(el("tr", {}, cells.map(function (c) { return el("td", { text: c }); })));
    });
    table.appendChild(body);
  }

  function legend(items) {
    var box = $("fc-legend");
    box.textContent = "";
    items.forEach(function (i) {
      var s = el("span", { text: i[1] });
      s.style.setProperty("--swatch", i[0]);
      box.appendChild(s);
    });
  }

  function ticks(a, b, n) {
    var span = b - a || 1, stepSize = Math.pow(10, Math.floor(Math.log10(span / n))), err = span / n / stepSize;
    if (err >= 7.5) stepSize *= 10; else if (err >= 3.5) stepSize *= 5; else if (err >= 1.5) stepSize *= 2;
    var out = [];
    for (var v = Math.ceil(a / stepSize) * stepSize; v <= b + stepSize * 1e-6; v += stepSize) out.push(+v.toFixed(10));
    return out;
  }

  function drawChart() {
    var c = $("fc-chart"), s = state.series, model = current();
    var dpr = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
    if (!w || !h) return;
    c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
    var ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    var P = { text: css("--text"), muted: css("--muted"), grid: css("--grid"), accent: css("--accent"), c2: css("--c2"), c3: css("--c3"), surface: css("--surface") };
    var font = "11px " + css("--sans");
    if (!s || !model) { legend([]); return; }

    var n = s.values.length, H = state.horizon, total = n + H, sc = scenarioActive() ? adjusted(model) : null;
    var showBand = state.band;
    var vals = s.values.concat(model.forecast);
    if (showBand === "both" || showBand === "95") vals = vals.concat(model.lo95, model.hi95);
    else if (showBand === "80") vals = vals.concat(model.lo80, model.hi80);
    if (sc) vals = vals.concat(sc.forecast);
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (lo > 0 && lo < hi * 0.25) lo = 0;
    var pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;
    var L = 56, R = 16, T = 14, B = 30, iw = w - L - R, ih = h - T - B;
    function sx(i) { return L + (i / (total - 1)) * iw; }
    function sy(v) { return T + (1 - (v - lo) / (hi - lo)) * ih; }

    ctx.font = font; ctx.lineWidth = 1;
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ticks(lo, hi, Math.max(3, ih / 55)).forEach(function (v) {
      var y = Math.round(sy(v)) + 0.5;
      ctx.strokeStyle = P.grid; ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(L + iw, y); ctx.stroke();
      ctx.fillStyle = P.muted; ctx.fillText(short(v), L - 8, y);
    });
    var dates = s.dates.concat(F.futureDates(s, H));
    var labelEvery = Math.max(1, Math.ceil(total / Math.max(2, iw / 90)));
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    for (var i = 0; i < total; i += labelEvery) ctx.fillText(F.formatDate(dates[i], s.freq), sx(i), T + ih + 8);

    // Forecast region and "now" divider.
    ctx.fillStyle = alpha(P.accent, 0.04);
    ctx.fillRect(sx(n - 1), T, sx(total - 1) - sx(n - 1), ih);
    ctx.strokeStyle = P.muted; ctx.setLineDash([2, 4]);
    ctx.beginPath(); ctx.moveTo(sx(n - 1), T); ctx.lineTo(sx(n - 1), T + ih); ctx.stroke();
    ctx.setLineDash([]);

    function band(lower, upper, a) {
      ctx.beginPath();
      ctx.moveTo(sx(n - 1), sy(s.values[n - 1]));
      for (var k = 0; k < H; k++) ctx.lineTo(sx(n + k), sy(upper[k]));
      for (k = H - 1; k >= 0; k--) ctx.lineTo(sx(n + k), sy(lower[k]));
      ctx.closePath();
      ctx.fillStyle = alpha(P.accent, a); ctx.fill();
    }
    var view = sc || model;
    if (showBand === "both" || showBand === "95") band(view.lo95, view.hi95, 0.12);
    if (showBand === "both" || showBand === "80") band(view.lo80, view.hi80, 0.2);

    function line(points, color, width, dash) {
      ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash || []);
      ctx.beginPath();
      var started = false;
      points.forEach(function (p) {
        if (!isFinite(p[1])) { started = false; return; }
        ctx[started ? "lineTo" : "moveTo"](sx(p[0]), sy(p[1])); started = true;
      });
      ctx.stroke(); ctx.setLineDash([]);
    }

    if (state.showFitted) line(model.fitted.map(function (v, i) { return [i, v]; }), alpha(P.c3, 0.9), 1.25);
    line(s.values.map(function (v, i) { return [i, v]; }), P.text, 1.75);
    if (state.showBacktest && model.backtest) {
      var start = n - model.backtest.length;
      line([[start - 1, s.values[start - 1]]].concat(model.backtest.map(function (v, k) { return [start + k, v]; })), P.c2, 2, [5, 4]);
    }
    var head = [[n - 1, s.values[n - 1]]];
    line(head.concat(model.forecast.map(function (v, k) { return [n + k, v]; })), P.accent, 2.5, sc ? [6, 4] : null);
    if (sc) line(head.concat(sc.forecast.map(function (v, k) { return [n + k, v]; })), P.c3, 2.75);

    var items = [[P.text, "Actual"], [P.accent, (sc ? "Base forecast · " : "Forecast · ") + model.name]];
    if (sc) items.push([P.c3, "Scenario"]);
    if (showBand !== "none") items.push([alpha(P.accent, 0.35), showBand === "both" ? "80% / 95% range" : showBand + "% range"]);
    if (state.showBacktest) items.push([P.c2, "Backtest prediction"]);
    if (state.showFitted) items.push([P.c3, "Model fit"]);
    legend(items);

    var out = $("fc-hover");
    if (!state.hover) { out.textContent = "Hover the chart to read values."; return; }
    var idx = Math.max(0, Math.min(total - 1, Math.round(((state.hover.x - L) / iw) * (total - 1))));
    var x = sx(idx);
    ctx.strokeStyle = P.muted; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, T); ctx.lineTo(x, T + ih); ctx.stroke();
    var parts = [["", F.formatDate(dates[idx], s.freq)]];
    if (idx < n) {
      parts.push(["actual", full(s.values[idx])]);
      var bt = model.backtest ? idx - (n - model.backtest.length) : -1;
      if (state.showBacktest && bt >= 0) parts.push(["backtest", full(model.backtest[bt])]);
      if (state.showFitted && isFinite(model.fitted[idx])) parts.push(["fit", full(model.fitted[idx])]);
      ctx.beginPath(); ctx.arc(x, sy(s.values[idx]), 4, 0, Math.PI * 2); ctx.fillStyle = P.text; ctx.fill();
    } else {
      var k = idx - n;
      parts.push(["forecast", full(model.forecast[k])], ["80%", full(view.lo80[k]) + " – " + full(view.hi80[k])]);
      if (sc) parts.push(["scenario", full(sc.forecast[k])]);
      ctx.beginPath(); ctx.arc(x, sy(view.forecast[k]), 4, 0, Math.PI * 2); ctx.fillStyle = sc ? P.c3 : P.accent; ctx.fill();
    }
    out.textContent = "";
    parts.forEach(function (p) { out.appendChild(el("span", {}, [p[0] ? p[0] + " " : "", el("b", { text: p[1] })])); });
  }

  // Export -------------------------------------------------------------------------------

  function csvCell(v) {
    var s = String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function exportCSV() {
    var s = state.series, model = current();
    if (!s || !model) { say("Nothing to export yet."); return; }
    var sc = scenarioActive() ? adjusted(model) : null;
    var rows = [["period", "actual", "forecast", "low_80", "high_80", "low_95", "high_95"].concat(sc ? ["scenario"] : [])];
    s.values.forEach(function (v, i) { rows.push([F.formatDate(s.dates[i], s.freq), v, "", "", "", "", ""].concat(sc ? [""] : [])); });
    F.futureDates(s, state.horizon).forEach(function (d, k) {
      var r = [F.formatDate(d, s.freq), "", model.forecast[k], model.lo80[k], model.hi80[k], model.lo95[k], model.hi95[k]];
      rows.push(r.map(function (v) { return typeof v === "number" ? +v.toFixed(4) : v; }).concat(sc ? [+sc.forecast[k].toFixed(4)] : []));
    });
    var text = rows.map(function (r) { return r.map(csvCell).join(","); }).join("\n") + "\n";
    var url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    var a = el("a", { href: url, download: slug(($("fc-name") && $("fc-name").value) || state.label) + "-forecast.csv" });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  // Projects (local app only) ------------------------------------------------------------

  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.error || "Request failed");
        return data;
      });
    });
  }

  function loadProjects() {
    api("GET", "api/projects").then(function (list) {
      var box = $("fc-projects");
      box.textContent = "";
      if (!list.length) { box.appendChild(el("li", { class: "muted small", text: "No saved projects yet." })); return; }
      list.forEach(function (p) {
        var open = el("button", { type: "button", class: "link", text: p.name });
        open.addEventListener("click", function () { openProject(p.project_id); });
        var del = el("button", { type: "button", class: "icon-btn", "aria-label": "Delete " + p.name, text: "×" });
        del.addEventListener("click", function () {
          if (!window.confirm("Delete “" + p.name + "”? This cannot be undone.")) return;
          api("DELETE", "api/projects/" + p.project_id).then(function () {
            if (state.projectId === p.project_id) state.projectId = null;
            say("Deleted " + p.name); loadProjects();
          }).catch(function (e) { say(e.message); });
        });
        var when = new Date(p.updated_at).toLocaleDateString();
        box.appendChild(el("li", { class: p.project_id === state.projectId ? "active" : "" }, [
          el("div", {}, [open, el("span", { class: "muted small", text: p.points + " points · " + when })]), del,
        ]));
      });
    }).catch(function (e) { say(e.message); });
  }

  function saveProject() {
    var s = state.series, model = current(), name = $("fc-name").value.trim();
    if (!s || !s.values.length) { say("Load some data first."); return; }
    if (!name) { $("fc-name").focus(); say("Give the project a name."); return; }
    api("POST", "api/projects", {
      project_id: state.projectId,
      name: name,
      data: { dates: s.dates, values: s.values, freq: s.freq, label: state.table.columns[state.valueCol] || "value", source: state.label },
      settings: { horizon: state.horizon, season: state.season, model: state.model, band: state.band, scenario: state.scenario },
      summary: model ? { model: model.id, mape: isFinite(model.metrics.mape) ? model.metrics.mape : null, forecast: model.forecast } : {},
    }).then(function (data) {
      state.projectId = data.project_id;
      say("Saved " + name);
      loadProjects();
      render();
    }).catch(function (e) { say(e.message); });
  }

  function openProject(id) {
    api("GET", "api/projects/" + id).then(function (p) {
      var d = p.data, label = d.label || "value";
      var rows = d.dates.map(function (t, i) { return [new Date(t).toISOString().slice(0, 10), String(d.values[i])]; });
      state.table = { columns: ["date", label], rows: rows };
      state.dateCol = 0; state.valueCol = 1; state.label = d.source || p.name; state.source = "project";
      fillColumns();
      buildSeries(true);
      var st = p.settings || {};
      state.horizon = st.horizon || state.horizon;
      state.season = st.season || "auto";
      state.model = st.model || "best";
      state.band = st.band || "both";
      state.scenario = Object.assign({ growth: 0, shock: 0, shockStart: 1, shockLength: 0 }, st.scenario || {});
      state.projectId = p.project_id;
      $("fc-name").value = p.name;
      syncControls(); compute(); loadProjects();
      say("Opened " + p.name);
    }).catch(function (e) { say(e.message); });
  }

  bind();
  loadSample(F.SAMPLES[0][0]);
})();
