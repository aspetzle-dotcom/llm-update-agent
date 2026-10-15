// Forecasting engine: CSV parsing, series cleaning, models, backtests and scenarios.
// Pure functions on window.Forecast; the dashboard UI lives in dashboard.js.
(function (root) {
  "use strict";

  var DAY = 86400000;
  var MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

  // Parsing ----------------------------------------------------------------------------

  function detectDelimiter(text) {
    var line = text.split(/\r?\n/).find(function (l) { return l.trim(); }) || "";
    var best = ",", count = 0;
    [",", ";", "\t", "|"].forEach(function (d) {
      var n = line.split(d).length - 1;
      if (n > count) { best = d; count = n; }
    });
    return best;
  }

  function parseCSV(text) {
    var delim = detectDelimiter(text), rows = [], row = [], cell = "", quoted = false;
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (quoted) {
        if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
        else if (ch === '"') quoted = false;
        else cell += ch;
      } else if (ch === '"' && cell === "") quoted = true;
      else if (ch === delim) { row.push(cell); cell = ""; }
      else if (ch === "\n" || ch === "\r") {
        if (ch === "\r" && text[i + 1] === "\n") i++;
        row.push(cell); cell = "";
        if (row.some(function (c) { return c.trim(); })) rows.push(row);
        row = [];
      } else cell += ch;
    }
    row.push(cell);
    if (row.some(function (c) { return c.trim(); })) rows.push(row);
    return rows.map(function (r) { return r.map(function (c) { return c.trim(); }); });
  }

  function parseNumber(s) {
    if (typeof s === "number") return s;
    s = String(s || "").trim();
    if (!s) return NaN;
    var negative = /^\(.*\)$/.test(s) || /^-/.test(s.replace(/^[^\d-]+/, ""));
    s = s.replace(/[()\s$€£¥%]/g, "").replace(/^-/, "");
    if (/^\d{1,3}(\.\d{3})+(,\d+)?$/.test(s)) s = s.replace(/\./g, "").replace(",", ".");
    else s = s.replace(/,/g, "");
    if (!/^\d*\.?\d+(e[+-]?\d+)?$/i.test(s)) return NaN;
    var v = parseFloat(s);
    return negative ? -v : v;
  }

  function utc(y, m, d) { return Date.UTC(y, m, d || 1); }

  function parseDate(s) {
    s = String(s || "").trim();
    var m;
    if ((m = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/))) return utc(+m[1], +m[2] - 1, +m[3]);
    if ((m = s.match(/^(\d{4})[-/.](\d{1,2})$/))) return utc(+m[1], +m[2] - 1);
    if ((m = s.match(/^(\d{4})\s*-?\s*Q([1-4])$/i)) || (m = s.match(/^Q([1-4])\s*-?\s*(\d{4})$/i))) {
      var year = m[1].length === 4 ? +m[1] : +m[2], q = m[1].length === 4 ? +m[2] : +m[1];
      return utc(year, (q - 1) * 3);
    }
    if ((m = s.match(/^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$/))) {
      var a = +m[1], b = +m[2], y = +m[3];
      if (y < 100) y += 2000;
      return a > 12 ? utc(y, b - 1, a) : utc(y, a - 1, b); // US month/day unless impossible
    }
    if ((m = s.match(/^([A-Za-z]{3,9})[\s,-]+(\d{4})$/)) || (m = s.match(/^(\d{4})[\s,-]+([A-Za-z]{3,9})$/))) {
      var name = /\d/.test(m[1]) ? m[2] : m[1], yy = /\d/.test(m[1]) ? +m[1] : +m[2];
      var idx = MONTHS.indexOf(name.slice(0, 3).toLowerCase());
      if (idx >= 0) return utc(yy, idx);
    }
    if ((m = s.match(/^(\d{4})$/)) && +m[1] > 1800 && +m[1] < 2200) return utc(+m[1], 0);
    var t = Date.parse(s);
    if (!isNaN(t) && /[a-z]{3}/i.test(s)) {
      var dt = new Date(t);
      return utc(dt.getFullYear(), dt.getMonth(), dt.getDate());
    }
    return NaN;
  }

  function readTable(text) {
    var rows = parseCSV(text || "");
    if (!rows.length) return { columns: [], rows: [] };
    var first = rows[0];
    var looksLikeData = first.some(function (c) { return !isNaN(parseNumber(c)); }) && first.some(function (c) { return !isNaN(parseDate(c)); });
    var columns = looksLikeData ? first.map(function (_, i) { return "Column " + (i + 1); }) : first.map(function (c, i) { return c || "Column " + (i + 1); });
    var body = looksLikeData ? rows : rows.slice(1);
    var width = columns.length;
    return { columns: columns, rows: body.map(function (r) { return r.slice(0, width); }) };
  }

  function guessColumns(table) {
    var dateCol = -1, valueCols = [];
    table.columns.forEach(function (_, i) {
      var sample = table.rows.slice(0, 50).map(function (r) { return r[i]; }).filter(Boolean);
      if (!sample.length) return;
      var dates = sample.filter(function (c) { return !isNaN(parseDate(c)); }).length;
      var nums = sample.filter(function (c) { return !isNaN(parseNumber(c)); }).length;
      if (dateCol < 0 && dates >= sample.length * 0.8 && !(nums === sample.length && sample.every(function (c) { return !/^\d{4}$/.test(c) && !/[-/]/.test(c); }))) dateCol = i;
      else if (nums >= sample.length * 0.8) valueCols.push(i);
    });
    return { date: dateCol, values: valueCols };
  }

  // Calendar ---------------------------------------------------------------------------

  var FREQS = {
    day: { label: "daily", season: 7, perYear: 365 },
    week: { label: "weekly", season: 52, perYear: 52 },
    month: { label: "monthly", season: 12, perYear: 12 },
    quarter: { label: "quarterly", season: 4, perYear: 4 },
    year: { label: "yearly", season: 0, perYear: 1 },
  };

  function detectFrequency(dates) {
    if (dates.length < 2) return "month";
    var diffs = [];
    for (var i = 1; i < dates.length; i++) diffs.push((dates[i] - dates[i - 1]) / DAY);
    diffs.sort(function (a, b) { return a - b; });
    var d = diffs[Math.floor(diffs.length / 2)];
    if (d <= 1.5) return "day";
    if (d <= 8) return "week";
    if (d <= 32) return "month";
    if (d <= 95) return "quarter";
    return "year";
  }

  function step(t, freq, k) {
    var d = new Date(t);
    if (freq === "day") return t + k * DAY;
    if (freq === "week") return t + k * 7 * DAY;
    var months = freq === "month" ? k : freq === "quarter" ? 3 * k : 12 * k;
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + months, Math.min(d.getUTCDate(), 28));
  }

  function key(t, freq) {
    var d = new Date(t);
    if (freq === "day") return Math.round(t / DAY);
    if (freq === "week") return Math.round(t / (7 * DAY));
    var m = d.getUTCFullYear() * 12 + d.getUTCMonth();
    return freq === "month" ? m : freq === "quarter" ? Math.floor(m / 3) : d.getUTCFullYear();
  }

  function formatDate(t, freq) {
    var d = new Date(t), y = d.getUTCFullYear(), mo = d.getUTCMonth();
    if (freq === "year") return String(y);
    if (freq === "quarter") return y + " Q" + (Math.floor(mo / 3) + 1);
    if (freq === "month") return MONTHS[mo][0].toUpperCase() + MONTHS[mo].slice(1) + " " + y;
    return y + "-" + String(mo + 1).padStart(2, "0") + "-" + String(d.getUTCDate()).padStart(2, "0");
  }

  function futureDates(series, h) {
    var last = series.dates[series.dates.length - 1], out = [];
    for (var k = 1; k <= h; k++) out.push(step(last, series.freq, k));
    return out;
  }

  // Turn raw rows into a sorted, regular series. Duplicates are summed; gaps interpolated.
  function toSeries(table, dateCol, valueCol) {
    var warnings = [], skipped = 0, byKey = {};
    var points = [];
    table.rows.forEach(function (r) {
      var t = parseDate(r[dateCol]), v = parseNumber(r[valueCol]);
      if (isNaN(t) || !isFinite(v)) { skipped++; return; }
      points.push([t, v]);
    });
    if (skipped) warnings.push(skipped + " row" + (skipped > 1 ? "s" : "") + " skipped: date or number could not be read.");
    points.sort(function (a, b) { return a[0] - b[0]; });
    var freq = detectFrequency(points.map(function (p) { return p[0]; }));
    var duplicates = 0;
    points.forEach(function (p) {
      var k = key(p[0], freq);
      if (byKey[k]) { byKey[k][1] += p[1]; duplicates++; } else byKey[k] = [p[0], p[1]];
    });
    if (duplicates) warnings.push(duplicates + (duplicates > 1 ? " rows" : " row") + " shared a " + FREQS[freq].label.replace("ly", "").replace("dai", "day") + " with another row and were added together.");
    var unique = Object.keys(byKey).map(Number).sort(function (a, b) { return a - b; }).map(function (k) { return byKey[k]; });
    var dates = [], values = [], filled = 0;
    if (unique.length) {
      var expected = key(unique[unique.length - 1][0], freq) - key(unique[0][0], freq) + 1;
      if (expected <= unique.length * 3 && expected <= 50000) {
        var j = 0, t = unique[0][0];
        for (var i = 0; i < expected; i++) {
          var cur = step(unique[0][0], freq, i);
          if (j < unique.length && key(unique[j][0], freq) === key(cur, freq)) {
            dates.push(unique[j][0]); values.push(unique[j][1]); j++;
          } else {
            dates.push(cur); values.push(NaN); filled++;
          }
          t = cur;
        }
        for (i = 0; i < values.length; i++) {
          if (!isNaN(values[i])) continue;
          var a = i - 1, b = i + 1;
          while (b < values.length && isNaN(values[b])) b++;
          values[i] = values[a] + ((values[b] - values[a]) * (i - a)) / (b - a);
        }
        if (filled) warnings.push(filled + " missing period" + (filled > 1 ? "s were" : " was") + " filled by straight-line interpolation.");
      } else {
        unique.forEach(function (p) { dates.push(p[0]); values.push(p[1]); });
        warnings.push("Dates are irregular; treating each row as one period.");
      }
    }
    return { dates: dates, values: values, freq: freq, warnings: warnings };
  }

  // Models -----------------------------------------------------------------------------

  function mean(a) { return a.reduce(function (s, v) { return s + v; }, 0) / a.length; }

  function solve(A, b) {
    var n = b.length;
    for (var i = 0; i < n; i++) {
      var p = i;
      for (var r = i + 1; r < n; r++) if (Math.abs(A[r][i]) > Math.abs(A[p][i])) p = r;
      var t = A[i]; A[i] = A[p]; A[p] = t;
      var tb = b[i]; b[i] = b[p]; b[p] = tb;
      if (Math.abs(A[i][i]) < 1e-12) A[i][i] = 1e-12;
      for (r = i + 1; r < n; r++) {
        var f = A[r][i] / A[i][i];
        for (var c = i; c < n; c++) A[r][c] -= f * A[i][c];
        b[r] -= f * b[i];
      }
    }
    var x = new Array(n);
    for (i = n - 1; i >= 0; i--) {
      var s = b[i];
      for (var k = i + 1; k < n; k++) s -= A[i][k] * x[k];
      x[i] = s / A[i][i];
    }
    return x;
  }

  function sse(y, fitted) {
    var s = 0, n = 0;
    for (var t = 0; t < y.length; t++) if (isFinite(fitted[t])) { s += Math.pow(y[t] - fitted[t], 2); n++; }
    return { sse: s, n: n };
  }

  function holt(y, alpha, beta) {
    var L = y[0], B = y[1] - y[0], fitted = [NaN];
    for (var t = 1; t < y.length; t++) {
      fitted.push(L + B);
      var L2 = alpha * y[t] + (1 - alpha) * (L + B);
      B = beta * (L2 - L) + (1 - beta) * B;
      L = L2;
    }
    return { fitted: fitted, L: L, B: B };
  }

  function holtWinters(y, m, alpha, beta, gamma) {
    var first = y.slice(0, m), second = y.slice(m, 2 * m);
    // Seasonal starting values remove the first season's trend; the level starts at t = m - 1.
    var mid = mean(first), B = (mean(second) - mid) / m, L = mid + ((m - 1) / 2) * B, s = [], fitted = [];
    for (var i = 0; i < m; i++) { s.push(y[i] - (mid + (i - (m - 1) / 2) * B)); fitted.push(NaN); }
    for (var t = m; t < y.length; t++) {
      fitted.push(L + B + s[t - m]);
      var L2 = alpha * (y[t] - s[t - m]) + (1 - alpha) * (L + B);
      B = beta * (L2 - L) + (1 - beta) * B;
      s.push(gamma * (y[t] - L2) + (1 - gamma) * s[t - m]);
      L = L2;
    }
    return { fitted: fitted, L: L, B: B, s: s };
  }

  var GRID = [0.05, 0.15, 0.3, 0.5, 0.7, 0.9];

  // Each model returns one-step fitted values, a point forecast and a growth factor
  // for the forecast error's standard deviation at each horizon step.
  var MODELS = [
    {
      id: "naive", name: "Naive", needs: function () { return 2; },
      about: "Next value equals the last one. The baseline every model should beat.",
      fit: function (y) {
        var fitted = [NaN].concat(y.slice(0, -1)), last = y[y.length - 1];
        return { fitted: fitted, forecast: function (h) { return Array(h).fill(last); }, spread: function (k) { return Math.sqrt(k + 1); } };
      },
    },
    {
      id: "seasonal_naive", name: "Seasonal naive", needs: function (m) { return m > 1 ? m + 1 : Infinity; },
      about: "Next value equals the same period one cycle ago (e.g. last year's month).",
      fit: function (y, m) {
        var n = y.length, fitted = y.map(function (_, t) { return t >= m ? y[t - m] : NaN; });
        return {
          fitted: fitted,
          forecast: function (h) { var out = []; for (var k = 0; k < h; k++) out.push(y[n - m + (k % m)]); return out; },
          spread: function (k) { return Math.sqrt(Math.floor(k / m) + 1); },
        };
      },
    },
    {
      id: "moving_average", name: "Moving average", needs: function () { return 4; },
      about: "Average of the most recent periods. Smooth, but ignores trend.",
      fit: function (y, m) {
        var w = Math.max(2, Math.min(m > 1 ? m : 4, Math.floor(y.length / 2)));
        var fitted = y.map(function (_, t) { return t >= w ? mean(y.slice(t - w, t)) : NaN; });
        var level = mean(y.slice(-w));
        return { fitted: fitted, forecast: function (h) { return Array(h).fill(level); }, spread: function () { return Math.sqrt(1 + 1 / w); } };
      },
    },
    {
      id: "drift", name: "Drift", needs: function () { return 3; },
      about: "Draws a line from the first to the last value and extends it.",
      fit: function (y) {
        var n = y.length, slope = (y[n - 1] - y[0]) / (n - 1);
        var fitted = y.map(function (_, t) { return t ? y[t - 1] + slope : NaN; });
        return {
          fitted: fitted,
          forecast: function (h) { var out = []; for (var k = 1; k <= h; k++) out.push(y[n - 1] + k * slope); return out; },
          spread: function (k) { return Math.sqrt((k + 1) * (1 + (k + 1) / (n - 1))); },
        };
      },
    },
    {
      id: "trend_season", name: "Trend + seasonality", needs: function (m) { return m > 1 ? 2 * m : 4; },
      about: "Regression on a straight-line trend plus an average effect for each season.",
      fit: function (y, m) {
        var n = y.length, seasonal = m > 1 && n >= 2 * m, p = seasonal ? m + 1 : 2;
        function row(t) {
          var r = [1, t / n];
          if (seasonal) for (var j = 1; j < m; j++) r.push(t % m === j ? 1 : 0);
          return r;
        }
        var A = [], b = [];
        for (var i = 0; i < p; i++) { A.push(Array(p).fill(0)); b.push(0); }
        y.forEach(function (v, t) {
          var r = row(t);
          for (var i = 0; i < p; i++) { b[i] += r[i] * v; for (var j = 0; j < p; j++) A[i][j] += r[i] * r[j]; }
        });
        var w = solve(A, b);
        function predict(t) { return row(t).reduce(function (s, v, i) { return s + v * w[i]; }, 0); }
        return {
          fitted: y.map(function (_, t) { return predict(t); }),
          forecast: function (h) { var out = []; for (var k = 0; k < h; k++) out.push(predict(n + k)); return out; },
          spread: function (k) { return Math.sqrt(1 + p / n + Math.pow((k + 1) / n, 2) * 3); },
          params: p,
        };
      },
    },
    {
      id: "holt", name: "Holt (trend smoothing)", needs: function () { return 4; },
      about: "Exponential smoothing that tracks a moving level and trend; weights recent data more.",
      fit: function (y) {
        var best = null;
        GRID.forEach(function (a) {
          GRID.forEach(function (b) {
            var r = holt(y, a, b), e = sse(y, r.fitted).sse;
            if (!best || e < best.e) best = { e: e, a: a, b: b, r: r };
          });
        });
        var r = best.r, a = best.a, b = best.b;
        return {
          fitted: r.fitted,
          forecast: function (h) { var out = []; for (var k = 1; k <= h; k++) out.push(r.L + k * r.B); return out; },
          spread: function (k) { var s = 1; for (var j = 1; j <= k; j++) s += Math.pow(a * (1 + j * b), 2); return Math.sqrt(s); },
          settings: "α=" + a + ", β=" + b,
        };
      },
    },
    {
      id: "holt_winters", name: "Holt-Winters", needs: function (m) { return m > 1 ? 2 * m + 2 : Infinity; },
      about: "Exponential smoothing of level, trend and a repeating seasonal pattern.",
      fit: function (y, m) {
        var best = null, n = y.length;
        GRID.forEach(function (a) {
          [0.05, 0.15, 0.3].forEach(function (b) {
            [0.05, 0.15, 0.3, 0.5].forEach(function (g) {
              var r = holtWinters(y, m, a, b, g), e = sse(y, r.fitted).sse;
              if (!best || e < best.e) best = { e: e, a: a, b: b, g: g, r: r };
            });
          });
        });
        var r = best.r, a = best.a, b = best.b, g = best.g;
        return {
          fitted: r.fitted,
          forecast: function (h) { var out = []; for (var k = 0; k < h; k++) out.push(r.L + (k + 1) * r.B + r.s[n - m + (k % m)]); return out; },
          spread: function (k) {
            var s = 1;
            for (var j = 1; j <= k; j++) s += Math.pow(a * (1 + j * b) + (j % m === 0 ? g : 0), 2);
            return Math.sqrt(s);
          },
          settings: "α=" + a + ", β=" + b + ", γ=" + g,
        };
      },
    },
  ];

  function errors(actual, predicted) {
    var abs = 0, sq = 0, pct = 0, pn = 0, bias = 0, n = actual.length;
    for (var i = 0; i < n; i++) {
      var e = predicted[i] - actual[i];
      abs += Math.abs(e); sq += e * e; bias += e;
      if (actual[i] !== 0) { pct += Math.abs(e / actual[i]); pn++; }
    }
    return { mae: abs / n, rmse: Math.sqrt(sq / n), mape: pn ? (100 * pct) / pn : NaN, bias: bias / n };
  }

  // Fit every eligible model, score it on a held-out tail, then refit on all data.
  function run(series, options) {
    var y = series.values, n = y.length, h = Math.max(1, options.horizon | 0);
    var m = options.season > 1 ? options.season : 0;
    var hold = Math.max(1, Math.min(h, Math.floor(n * 0.2)));
    var train = y.slice(0, n - hold), test = y.slice(n - hold);
    var results = [];
    MODELS.forEach(function (model) {
      var entry = { id: model.id, name: model.name, about: model.about, available: false };
      results.push(entry);
      if (n < model.needs(m) + 2 || train.length < model.needs(m)) {
        entry.reason = "needs more history";
        return;
      }
      var back = model.fit(train, m).forecast(hold);
      var full = model.fit(y, m), point = full.forecast(h);
      var res = sse(y, full.fitted), sigma = res.n > 1 ? Math.sqrt(res.sse / res.n) : 0;
      var lo80 = [], hi80 = [], lo95 = [], hi95 = [];
      point.forEach(function (v, k) {
        var s = sigma * full.spread(k);
        lo80.push(v - 1.2816 * s); hi80.push(v + 1.2816 * s);
        lo95.push(v - 1.96 * s); hi95.push(v + 1.96 * s);
      });
      entry.available = true;
      entry.metrics = errors(test, back);
      entry.backtest = back;
      entry.fitted = full.fitted;
      entry.forecast = point;
      entry.lo80 = lo80; entry.hi80 = hi80; entry.lo95 = lo95; entry.hi95 = hi95;
      entry.sigma = sigma;
      entry.settings = full.settings || "";
    });
    var ranked = results.filter(function (r) { return r.available; })
      .sort(function (a, b) { return a.metrics.mae - b.metrics.mae || a.metrics.rmse - b.metrics.rmse; });
    ranked.forEach(function (r, i) { r.rank = i + 1; });
    return { models: results, best: ranked.length ? ranked[0].id : null, holdout: hold, season: m, horizon: h };
  }

  // What-if adjustments applied on top of a model's forecast.
  function scenario(model, opts) {
    var perYear = opts.perYear || 12, out = { forecast: [], lo80: [], hi80: [], lo95: [], hi95: [] };
    model.forecast.forEach(function (_, k) {
      var f = Math.pow(1 + (opts.growth || 0) / 100, (k + 1) / perYear);
      var start = (opts.shockStart || 1) - 1, len = opts.shockLength || 0;
      if (opts.shock && k >= start && (!len || k < start + len)) f *= 1 + opts.shock / 100;
      ["forecast", "lo80", "hi80", "lo95", "hi95"].forEach(function (name) {
        out[name].push(model[name][k] * f);
      });
    });
    return out;
  }

  // Sample data --------------------------------------------------------------------------

  function rng(seed) {
    return function () {
      seed = (seed + 0x6d2b79f5) | 0;
      var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function sampleCSV(id) {
    var r = rng(id.length * 101), lines = [], i, t, v;
    function noise(s) { return (r() + r() + r() - 1.5) * s; }
    if (id === "retail") {
      lines.push("month,sales_usd,orders");
      var bump = [0.92, 0.88, 0.97, 0.99, 1.02, 1.0, 0.98, 1.01, 0.97, 1.03, 1.12, 1.31];
      for (i = 0; i < 84; i++) {
        t = Date.UTC(2019, i, 1);
        v = (82000 + 950 * i) * bump[i % 12] + noise(4200);
        if (i >= 14 && i <= 16) v *= 0.7;
        lines.push(new Date(t).toISOString().slice(0, 7) + "," + Math.round(v) + "," + Math.round(v / 61 + noise(40)));
      }
    } else if (id === "traffic") {
      lines.push("date,visits,signups");
      var week = [0.78, 1.08, 1.12, 1.1, 1.04, 0.92, 0.7];
      for (i = 0; i < 210; i++) {
        t = Date.UTC(2026, 2, 1) + i * DAY;
        v = (5200 + 9 * i) * week[new Date(t).getUTCDay()] + noise(380);
        if (i === 120) v *= 1.9;
        lines.push(new Date(t).toISOString().slice(0, 10) + "," + Math.round(v) + "," + Math.round(v * 0.021 + noise(12)));
      }
    } else {
      lines.push("quarter,revenue_musd");
      var qs = [0.94, 1.0, 0.97, 1.09];
      for (i = 0; i < 28; i++) {
        v = (41 + 1.6 * i + 0.03 * i * i) * qs[i % 4] + noise(1.4);
        lines.push(2019 + Math.floor(i / 4) + " Q" + ((i % 4) + 1) + "," + v.toFixed(2));
      }
    }
    return lines.join("\n");
  }

  var SAMPLES = [
    ["retail", "Monthly retail sales (seasonal, with a 2020 dip)"],
    ["traffic", "Daily web traffic (weekly cycle, one spike)"],
    ["revenue", "Quarterly revenue (accelerating growth)"],
  ];

  root.Forecast = {
    parseCSV: parseCSV, parseNumber: parseNumber, parseDate: parseDate, readTable: readTable,
    guessColumns: guessColumns, toSeries: toSeries, detectFrequency: detectFrequency,
    futureDates: futureDates, formatDate: formatDate, run: run, scenario: scenario,
    errors: errors, sampleCSV: sampleCSV, SAMPLES: SAMPLES, FREQS: FREQS, MODELS: MODELS,
  };
})(window);
