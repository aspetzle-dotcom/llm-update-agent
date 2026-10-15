// Interactive figures for the guide pages. Plain canvas, no libraries, works offline.
(function () {
  "use strict";

  var registry = {};
  var redraws = [];

  // Helpers --------------------------------------------------------------------------

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function palette() {
    return {
      bg: css("--surface-2"), surface: css("--surface"), text: css("--text"), muted: css("--muted"),
      grid: css("--grid"), border: css("--border"), accent: css("--accent"),
      good: css("--good"), bad: css("--bad"),
      c: [css("--c1"), css("--c2"), css("--c3"), css("--c4"), css("--c5"), css("--c6")],
    };
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function canvas(fig, cls) {
    var c = el("canvas", cls ? { class: cls } : {});
    fig.appendChild(c);
    return c;
  }

  function fit(c) {
    var dpr = window.devicePixelRatio || 1;
    var w = c.clientWidth, h = c.clientHeight;
    var W = Math.max(1, Math.round(w * dpr)), H = Math.max(1, Math.round(h * dpr));
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    var ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx: ctx, w: w, h: h };
  }

  function controls(fig) {
    var d = el("div", { class: "controls" });
    fig.appendChild(d);
    return d;
  }

  function slider(parent, label, min, max, step, value, onInput, format) {
    var input = el("input", { type: "range", min: min, max: max, step: step, value: value });
    var out = el("output");
    function show() { out.textContent = format ? format(+input.value) : input.value; }
    input.addEventListener("input", function () { show(); onInput(+input.value); });
    show();
    parent.appendChild(el("label", {}, [label, input, out]));
    return input;
  }

  function button(parent, text, onClick, primary) {
    var b = el("button", { type: "button", class: primary ? "btn btn-primary" : "btn", text: text });
    b.addEventListener("click", onClick);
    parent.appendChild(b);
    return b;
  }

  function select(parent, label, options, onChange) {
    var s = el("select");
    options.forEach(function (o) { s.appendChild(el("option", { value: o[0], text: o[1] })); });
    s.addEventListener("change", function () { onChange(s.value); });
    parent.appendChild(el("label", {}, [label, s]));
    return s;
  }

  function checkbox(parent, label, checked, onChange) {
    var input = el("input", { type: "checkbox" });
    input.checked = checked;
    input.addEventListener("change", function () { onChange(input.checked); });
    parent.appendChild(el("label", {}, [input, label]));
    return input;
  }

  function readout(fig) {
    var d = el("div", { class: "readout", "aria-live": "polite" });
    fig.appendChild(d);
    return function (pairs) {
      d.textContent = "";
      pairs.forEach(function (p) {
        d.appendChild(el("span", {}, [p[0] + " ", el("b", { text: String(p[1]) })]));
      });
    };
  }

  function legend(fig, items) {
    var d = el("div", { class: "legend" });
    items.forEach(function (i) {
      var s = el("span", { text: i[1] });
      s.style.setProperty("--swatch", i[0]);
      d.appendChild(s);
    });
    fig.appendChild(d);
    return d;
  }

  function rng(seed) {
    return function () {
      seed = (seed + 0x6d2b79f5) | 0;
      var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function gauss(r) {
    var u = 1 - r(), v = r();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  function alpha(color, a) {
    // Works for #rrggbb tokens; anything else falls back to globalAlpha at draw time.
    if (/^#[0-9a-f]{6}$/i.test(color)) {
      var n = parseInt(color.slice(1), 16);
      return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
    }
    return color;
  }

  function frame(w, h, xd, yd, pad) {
    pad = pad || { l: 40, r: 14, t: 12, b: 28 };
    var iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
    return {
      pad: pad, xd: xd, yd: yd, iw: iw, ih: ih,
      sx: function (x) { return pad.l + ((x - xd[0]) / (xd[1] - xd[0])) * iw; },
      sy: function (y) { return pad.t + (1 - (y - yd[0]) / (yd[1] - yd[0])) * ih; },
      ix: function (px) { return xd[0] + ((px - pad.l) / iw) * (xd[1] - xd[0]); },
      iy: function (py) { return yd[0] + (1 - (py - pad.t) / ih) * (yd[1] - yd[0]); },
    };
  }

  function ticks(a, b, n) {
    var span = b - a, step = Math.pow(10, Math.floor(Math.log10(span / n))), err = span / n / step;
    if (err >= 7.5) step *= 10; else if (err >= 3.5) step *= 5; else if (err >= 1.5) step *= 2;
    var out = [];
    for (var v = Math.ceil(a / step) * step; v <= b + step * 1e-6; v += step) out.push(+v.toFixed(10));
    return out;
  }

  function fmt(v) {
    var a = Math.abs(v);
    if (a >= 1000) return (v / 1000).toFixed(a >= 10000 ? 0 : 1) + "k";
    if (a !== 0 && a < 0.01) return v.toExponential(0);
    return String(+v.toFixed(2));
  }

  function axes(ctx, f, P, labels) {
    ctx.save();
    ctx.font = "11px " + css("--sans");
    ctx.strokeStyle = P.grid;
    ctx.fillStyle = P.muted;
    ctx.lineWidth = 1;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ticks(f.xd[0], f.xd[1], Math.max(3, f.iw / 80)).forEach(function (x) {
      var X = Math.round(f.sx(x)) + 0.5;
      ctx.beginPath(); ctx.moveTo(X, f.pad.t); ctx.lineTo(X, f.pad.t + f.ih); ctx.stroke();
      ctx.fillText(fmt(x), X, f.pad.t + f.ih + 6);
    });
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ticks(f.yd[0], f.yd[1], Math.max(3, f.ih / 50)).forEach(function (y) {
      var Y = Math.round(f.sy(y)) + 0.5;
      ctx.beginPath(); ctx.moveTo(f.pad.l, Y); ctx.lineTo(f.pad.l + f.iw, Y); ctx.stroke();
      ctx.fillText(fmt(y), f.pad.l - 6, Y);
    });
    if (labels) {
      ctx.textAlign = "right";
      ctx.textBaseline = "bottom";
      if (labels[0]) ctx.fillText(labels[0], f.pad.l + f.iw, f.pad.t + f.ih - 4);
      ctx.save();
      ctx.translate(f.pad.l + 12, f.pad.t + 4);
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      if (labels[1]) ctx.fillText(labels[1], 0, 0);
      ctx.restore();
    }
    ctx.restore();
  }

  function dot(ctx, x, y, r, fill, stroke) {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1.5; ctx.stroke(); }
  }

  function clip(ctx, f) {
    ctx.beginPath();
    ctx.rect(f.pad.l, f.pad.t, f.iw, f.ih);
    ctx.clip();
  }

  function pointer(c, handler) {
    c.addEventListener("pointerdown", function (e) {
      var rect = c.getBoundingClientRect();
      handler(e.clientX - rect.left, e.clientY - rect.top, e);
    });
  }

  function solve(A, b) {
    // Gaussian elimination with partial pivoting; A is n x n, modified in place.
    var n = b.length;
    for (var i = 0; i < n; i++) {
      var p = i;
      for (var r = i + 1; r < n; r++) if (Math.abs(A[r][i]) > Math.abs(A[p][i])) p = r;
      var t = A[i]; A[i] = A[p]; A[p] = t;
      var tb = b[i]; b[i] = b[p]; b[p] = tb;
      for (r = i + 1; r < n; r++) {
        var m = A[r][i] / A[i][i];
        for (var c = i; c < n; c++) A[r][c] -= m * A[i][c];
        b[r] -= m * b[i];
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

  // Classical ML ---------------------------------------------------------------------

  registry.scaling = function (fig) {
    var c = canvas(fig);
    var r = rng(7), houses = [];
    for (var i = 0; i < 70; i++) {
      var size = Math.min(4000, Math.max(500, 1800 + 650 * gauss(r)));
      var beds = Math.min(6, Math.max(1, Math.round(size / 700 + 0.6 * gauss(r))));
      houses.push([size, beds]);
    }
    function stats(k) {
      var vals = houses.map(function (h) { return h[k]; });
      var mean = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
      var sd = Math.sqrt(vals.reduce(function (a, v) { return a + (v - mean) * (v - mean); }, 0) / vals.length);
      return { mean: mean, sd: sd, min: Math.min.apply(null, vals), max: Math.max.apply(null, vals) };
    }
    var S = [stats(0), stats(1)];
    var modes = {
      raw: { f: function (v) { return v; }, d: [-200, 4200] },
      minmax: { f: function (v, k) { return (v - S[k].min) / (S[k].max - S[k].min); }, d: [-0.1, 1.1] },
      standard: { f: function (v, k) { return (v - S[k].mean) / S[k].sd; }, d: [-3, 3.2] },
    };
    var mode = "raw", from = "raw", t0 = 0;
    var show = readout(fig);
    var ctl = controls(fig);
    select(ctl, "Scaling", [["raw", "None (raw units)"], ["minmax", "Min–max to 0…1"], ["standard", "Standardize (z-score)"]], function (v) {
      from = mode; mode = v; t0 = performance.now(); requestAnimationFrame(animate);
    });
    function value(m, h, k) { return modes[m].f(h[k], k); }
    function animate(now) {
      draw(Math.min(1, (now - t0) / 450));
      if (now - t0 < 450) requestAnimationFrame(animate);
    }
    function draw(p) {
      if (p === undefined) p = 1;
      var e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
      var g = fit(c), P = palette();
      var d0 = modes[from].d, d1 = modes[mode].d;
      var d = [d0[0] + (d1[0] - d0[0]) * e, d0[1] + (d1[1] - d0[1]) * e];
      var f = frame(g.w, g.h, d, d);
      axes(g.ctx, f, P, ["house size", "bedrooms"]);
      houses.forEach(function (h) {
        var x = value(from, h, 0) + (value(mode, h, 0) - value(from, h, 0)) * e;
        var y = value(from, h, 1) + (value(mode, h, 1) - value(from, h, 1)) * e;
        dot(g.ctx, f.sx(x), f.sy(y), 4, alpha(P.c[0], 0.7));
      });
      var n = function (v) { return v.toFixed(mode === "raw" ? 0 : 2); };
      var a = houses.map(function (h) { return value(mode, h, 0); });
      var b = houses.map(function (h) { return value(mode, h, 1); });
      show([
        ["size range", n(Math.min.apply(null, a)) + " … " + n(Math.max.apply(null, a))],
        ["bedroom range", n(Math.min.apply(null, b)) + " … " + n(Math.max.apply(null, b))],
      ]);
    }
    return draw;
  };

  registry.linreg = function (fig) {
    var c = canvas(fig);
    var pts, residuals = true;
    function reset() {
      var r = rng(3);
      pts = [];
      for (var i = 0; i < 12; i++) {
        var x = 0.5 + r() * 9;
        pts.push([x, 0.6 * x + 1.5 + gauss(r) * 0.9]);
      }
      draw();
    }
    var show = readout(fig);
    var ctl = controls(fig);
    button(ctl, "Reset", reset);
    button(ctl, "Clear", function () { pts = []; draw(); });
    checkbox(ctl, "Show errors (residuals)", true, function (v) { residuals = v; draw(); });
    var f;
    pointer(c, function (x, y) {
      if (!f) return;
      var X = f.ix(x), Y = f.iy(y);
      if (X < 0 || X > 10 || Y < 0 || Y > 10) return;
      pts.push([X, Y]);
      draw();
    });
    function fitLine() {
      var n = pts.length;
      if (n < 2) return null;
      var mx = 0, my = 0;
      pts.forEach(function (p) { mx += p[0] / n; my += p[1] / n; });
      var sxx = 0, sxy = 0, syy = 0;
      pts.forEach(function (p) {
        sxx += (p[0] - mx) * (p[0] - mx); sxy += (p[0] - mx) * (p[1] - my); syy += (p[1] - my) * (p[1] - my);
      });
      if (sxx < 1e-9) return null;
      var a = sxy / sxx, b = my - a * mx, sse = 0;
      pts.forEach(function (p) { var e = p[1] - (a * p[0] + b); sse += e * e; });
      return { a: a, b: b, mse: sse / n, r2: syy > 0 ? 1 - sse / syy : 1 };
    }
    function draw() {
      var g = fit(c), P = palette(), ctx = g.ctx;
      f = frame(g.w, g.h, [0, 10], [0, 10]);
      axes(ctx, f, P, ["x (input)", "y (target)"]);
      var m = fitLine();
      ctx.save();
      clip(ctx, f);
      if (m) {
        if (residuals) {
          ctx.strokeStyle = alpha(P.c[1], 0.8);
          ctx.setLineDash([3, 3]);
          pts.forEach(function (p) {
            ctx.beginPath(); ctx.moveTo(f.sx(p[0]), f.sy(p[1])); ctx.lineTo(f.sx(p[0]), f.sy(m.a * p[0] + m.b)); ctx.stroke();
          });
          ctx.setLineDash([]);
        }
        ctx.strokeStyle = P.accent;
        ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.moveTo(f.sx(0), f.sy(m.b)); ctx.lineTo(f.sx(10), f.sy(m.a * 10 + m.b)); ctx.stroke();
      }
      pts.forEach(function (p) { dot(ctx, f.sx(p[0]), f.sy(p[1]), 4.5, P.c[0], P.surface); });
      ctx.restore();
      show(m ? [
        ["fit", "y = " + m.a.toFixed(2) + "·x " + (m.b < 0 ? "− " : "+ ") + Math.abs(m.b).toFixed(2)],
        ["mean squared error", m.mse.toFixed(3)], ["R²", m.r2.toFixed(3)], ["points", pts.length],
      ] : [["points", pts.length], ["fit", "needs 2+ points with different x"]]);
    }
    reset();
    return draw;
  };

  registry.knn = function (fig) {
    var c = canvas(fig);
    var pts, k = 5, cls = 0;
    function reset() {
      var r = rng(11);
      pts = [];
      for (var i = 0; i < 30; i++) {
        pts.push([0.32 + 0.14 * gauss(r), 0.38 + 0.14 * gauss(r), 0]);
        pts.push([0.66 + 0.14 * gauss(r), 0.62 + 0.14 * gauss(r), 1]);
      }
      draw();
    }
    var show = readout(fig);
    var ctl = controls(fig);
    slider(ctl, "k neighbours", 1, 25, 2, k, function (v) { k = v; draw(); });
    select(ctl, "Click adds", [["0", "Class A"], ["1", "Class B"]], function (v) { cls = +v; });
    button(ctl, "Reset", reset);
    var f;
    pointer(c, function (x, y) {
      if (!f) return;
      pts.push([f.ix(x), f.iy(y), cls]);
      draw();
    });
    function vote(x, y, skip) {
      var d = [];
      for (var i = 0; i < pts.length; i++) {
        if (i === skip) continue;
        var dx = pts[i][0] - x, dy = pts[i][1] - y;
        d.push([dx * dx + dy * dy, pts[i][2]]);
      }
      d.sort(function (a, b) { return a[0] - b[0]; });
      var n = Math.min(k, d.length), ones = 0;
      for (i = 0; i < n; i++) ones += d[i][1];
      return n ? ones / n : 0.5;
    }
    function draw() {
      var g = fit(c), P = palette(), ctx = g.ctx;
      f = frame(g.w, g.h, [0, 1], [0, 1], { l: 8, r: 8, t: 8, b: 8 });
      var cell = 7;
      for (var px = f.pad.l; px < f.pad.l + f.iw; px += cell) {
        for (var py = f.pad.t; py < f.pad.t + f.ih; py += cell) {
          var p = vote(f.ix(px + cell / 2), f.iy(py + cell / 2), -1);
          ctx.fillStyle = alpha(p >= 0.5 ? P.c[1] : P.c[0], 0.08 + Math.abs(p - 0.5) * 0.36);
          ctx.fillRect(px, py, cell, cell);
        }
      }
      var correct = 0;
      pts.forEach(function (q, i) {
        if ((vote(q[0], q[1], i) >= 0.5 ? 1 : 0) === q[2]) correct++;
        dot(ctx, f.sx(q[0]), f.sy(q[1]), 4.5, q[2] ? P.c[1] : P.c[0], P.surface);
      });
      show([["k", k], ["points", pts.length], ["leave-one-out accuracy", pts.length > 1 ? Math.round((100 * correct) / pts.length) + "%" : "—"]]);
    }
    legend(fig, [[css("--c1"), "Class A"], [css("--c2"), "Class B"]]);
    reset();
    return draw;
  };

  registry.kmeans = function (fig) {
    var c = canvas(fig);
    var pts, cents, assign, k = 4, iter = 0, done = false, timer = null, seed = 5;
    function newData() {
      var r = rng(seed++);
      pts = [];
      var centers = [[0.25, 0.3], [0.72, 0.28], [0.3, 0.75], [0.75, 0.72]];
      centers.forEach(function (ce) {
        var sx = 0.06 + r() * 0.05, sy = 0.06 + r() * 0.05;
        for (var i = 0; i < 45; i++) pts.push([ce[0] + sx * gauss(r), ce[1] + sy * gauss(r)]);
      });
      restart();
    }
    function restart() {
      stop();
      var r = rng(seed++ * 31);
      cents = [];
      for (var i = 0; i < k; i++) { var p = pts[Math.floor(r() * pts.length)]; cents.push([p[0], p[1]]); }
      assign = pts.map(function () { return -1; });
      iter = 0; done = false;
      draw();
    }
    function nearest(p) {
      var best = 0, bd = Infinity;
      cents.forEach(function (ce, j) {
        var d = (p[0] - ce[0]) * (p[0] - ce[0]) + (p[1] - ce[1]) * (p[1] - ce[1]);
        if (d < bd) { bd = d; best = j; }
      });
      return best;
    }
    function step() {
      if (done) return;
      var changed = false;
      pts.forEach(function (p, i) { var j = nearest(p); if (j !== assign[i]) { assign[i] = j; changed = true; } });
      cents = cents.map(function (ce, j) {
        var sx = 0, sy = 0, n = 0;
        pts.forEach(function (p, i) { if (assign[i] === j) { sx += p[0]; sy += p[1]; n++; } });
        return n ? [sx / n, sy / n] : ce;
      });
      iter++;
      if (!changed) { done = true; stop(); }
      draw();
    }
    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    var show = readout(fig);
    var ctl = controls(fig);
    slider(ctl, "k clusters", 2, 7, 1, k, function (v) { k = v; restart(); });
    button(ctl, "Step", step, true);
    button(ctl, "Run", function () { stop(); timer = setInterval(step, 450); });
    button(ctl, "New starting centres", restart);
    button(ctl, "New data", newData);
    function draw() {
      var g = fit(c), P = palette(), ctx = g.ctx;
      var f = frame(g.w, g.h, [0, 1], [0, 1], { l: 8, r: 8, t: 8, b: 8 });
      if (iter > 0) {
        var cell = 9;
        for (var px = f.pad.l; px < f.pad.l + f.iw; px += cell)
          for (var py = f.pad.t; py < f.pad.t + f.ih; py += cell) {
            ctx.fillStyle = alpha(P.c[nearest([f.ix(px + cell / 2), f.iy(py + cell / 2)]) % 6], 0.1);
            ctx.fillRect(px, py, cell, cell);
          }
      }
      var inertia = 0;
      pts.forEach(function (p, i) {
        var j = assign[i];
        if (j >= 0) inertia += Math.pow(p[0] - cents[j][0], 2) + Math.pow(p[1] - cents[j][1], 2);
        dot(ctx, f.sx(p[0]), f.sy(p[1]), 3.5, j < 0 ? P.muted : alpha(P.c[j % 6], 0.85));
      });
      cents.forEach(function (ce, j) {
        var X = f.sx(ce[0]), Y = f.sy(ce[1]);
        ctx.save();
        ctx.strokeStyle = P.text; ctx.lineWidth = 3;
        ctx.beginPath(); ctx.moveTo(X - 8, Y - 8); ctx.lineTo(X + 8, Y + 8); ctx.moveTo(X + 8, Y - 8); ctx.lineTo(X - 8, Y + 8); ctx.stroke();
        ctx.strokeStyle = P.c[j % 6]; ctx.lineWidth = 1.5; ctx.stroke();
        ctx.restore();
      });
      show([["iteration", iter], ["inertia (lower is tighter)", iter ? inertia.toFixed(3) : "—"], ["status", done ? "converged" : iter ? "moving" : "press Step"]]);
    }
    newData();
    return draw;
  };

  registry.overfit = function (fig) {
    var c = canvas(fig);
    var c2 = canvas(fig, "short");
    var degree = 3, train, test, seed = 1, curve = [];
    function truth(x) { return Math.sin(3 * x); }
    function basis(x, d) {
      // Legendre polynomials keep high-degree fits numerically stable on [-1, 1].
      var out = [1, x];
      for (var n = 1; n < d; n++) out.push(((2 * n + 1) * x * out[n] - n * out[n - 1]) / (n + 1));
      return out.slice(0, d + 1);
    }
    function fitPoly(d) {
      var n = d + 1, A = [], b = [];
      for (var i = 0; i < n; i++) { A.push(new Array(n).fill(0)); b.push(0); A[i][i] = 1e-9; }
      train.forEach(function (p) {
        var phi = basis(p[0], d);
        for (var i = 0; i < n; i++) { b[i] += phi[i] * p[1]; for (var j = 0; j < n; j++) A[i][j] += phi[i] * phi[j]; }
      });
      var w = solve(A, b);
      return function (x) { var phi = basis(x, d), s = 0; for (var i = 0; i < n; i++) s += w[i] * phi[i]; return s; };
    }
    function mse(model, data) {
      return data.reduce(function (a, p) { var e = model(p[0]) - p[1]; return a + e * e; }, 0) / data.length;
    }
    function sample() {
      var r = rng(seed++ * 97);
      train = []; test = [];
      for (var i = 0; i < 14; i++) { var x = -1 + (2 * (i + r())) / 14; train.push([x, truth(x) + 0.25 * gauss(r)]); }
      for (i = 0; i < 60; i++) { x = -1 + 2 * r(); test.push([x, truth(x) + 0.25 * gauss(r)]); }
      curve = [];
      for (var d = 0; d <= 12; d++) { var m = fitPoly(d); curve.push([mse(m, train), mse(m, test)]); }
      draw();
    }
    var show = readout(fig);
    var ctl = controls(fig);
    slider(ctl, "Polynomial degree", 0, 12, 1, degree, function (v) { degree = v; draw(); });
    button(ctl, "New sample", sample);
    var lg = legend(fig, [[css("--c1"), "training points"], [css("--c2"), "test points (unseen)"], [css("--accent"), "model"], [css("--muted"), "true pattern"]]);
    function draw() {
      var g = fit(c), P = palette(), ctx = g.ctx;
      var f = frame(g.w, g.h, [-1, 1], [-2.2, 2.2]);
      axes(ctx, f, P, ["x", "y"]);
      var model = fitPoly(degree);
      ctx.save();
      clip(ctx, f);
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = P.muted; ctx.lineWidth = 1.5;
      ctx.beginPath();
      for (var i = 0; i <= 200; i++) { var x = -1 + i / 100; ctx[i ? "lineTo" : "moveTo"](f.sx(x), f.sy(truth(x))); }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.strokeStyle = P.accent; ctx.lineWidth = 2.5;
      ctx.beginPath();
      for (i = 0; i <= 400; i++) {
        x = -1 + i / 200;
        var y = Math.max(-10, Math.min(10, model(x)));
        ctx[i ? "lineTo" : "moveTo"](f.sx(x), f.sy(y));
      }
      ctx.stroke();
      test.forEach(function (p) { dot(ctx, f.sx(p[0]), f.sy(p[1]), 3, null, alpha(P.c[1], 0.8)); });
      train.forEach(function (p) { dot(ctx, f.sx(p[0]), f.sy(p[1]), 4.5, P.c[0], P.surface); });
      ctx.restore();

      var h = fit(c2), ctx2 = h.ctx;
      var top = Math.max.apply(null, curve.map(function (e) { return Math.min(e[1], 2); }));
      var f2 = frame(h.w, h.h, [0, 12], [0, Math.max(0.2, top * 1.1)]);
      axes(ctx2, f2, P, ["degree", "error (MSE)"]);
      [[0, P.c[0]], [1, P.c[1]]].forEach(function (s) {
        ctx2.strokeStyle = s[1]; ctx2.lineWidth = 2;
        ctx2.beginPath();
        curve.forEach(function (e, d) { ctx2[d ? "lineTo" : "moveTo"](f2.sx(d), f2.sy(Math.min(e[s[0]], f2.yd[1]))); });
        ctx2.stroke();
        curve.forEach(function (e, d) { dot(ctx2, f2.sx(d), f2.sy(Math.min(e[s[0]], f2.yd[1])), d === degree ? 5 : 2.5, s[1]); });
      });
      var best = 0;
      curve.forEach(function (e, d) { if (e[1] < curve[best][1]) best = d; });
      var verdict = degree < best && curve[degree][1] > curve[best][1] * 1.25 ? "underfitting: too simple"
        : degree > best && curve[degree][1] > curve[best][1] * 1.25 ? "overfitting: memorising noise" : "a reasonable fit";
      show([["training error", curve[degree][0].toFixed(3)], ["test error", curve[degree][1].toFixed(3)], ["best test degree", best], ["verdict", verdict]]);
    }
    fig.insertBefore(lg, c2);
    sample();
    return draw;
  };

  registry.threshold = function (fig) {
    var c = canvas(fig);
    var t = 0.5, gap = 0.27, neg, pos;
    function sample() {
      var r = rng(21);
      neg = []; pos = [];
      for (var i = 0; i < 500; i++) { neg.push(0.5 - gap / 2 + 0.12 * gauss(r)); pos.push(0.5 + gap / 2 + 0.12 * gauss(r)); }
    }
    var cm = el("div", { class: "cm" });
    var show = readout(fig);
    var ctl = controls(fig);
    slider(ctl, "Threshold", 0.05, 0.95, 0.01, t, function (v) { t = v; draw(); }, function (v) { return v.toFixed(2); });
    slider(ctl, "Class separation", 0, 0.6, 0.01, gap, function (v) { gap = v; sample(); draw(); }, function (v) { return v.toFixed(2); });
    fig.appendChild(cm);
    var f;
    function setFromX(x) { if (f) { t = Math.max(0.05, Math.min(0.95, f.ix(x))); ctl.querySelector("input").value = t; ctl.querySelector("output").textContent = t.toFixed(2); draw(); } }
    var dragging = false;
    pointer(c, function (x) { dragging = true; setFromX(x); });
    c.addEventListener("pointermove", function (e) { if (dragging) setFromX(e.clientX - c.getBoundingClientRect().left); });
    window.addEventListener("pointerup", function () { dragging = false; });
    function pdf(x, m) { return Math.exp(-0.5 * Math.pow((x - m) / 0.12, 2)) / (0.12 * Math.sqrt(2 * Math.PI)); }
    function draw() {
      var g = fit(c), P = palette(), ctx = g.ctx;
      f = frame(g.w, g.h, [0, 1], [0, 3.6]);
      axes(ctx, f, P, ["model score", "how many items"]);
      ctx.save();
      clip(ctx, f);
      [[0.5 - gap / 2, P.c[0]], [0.5 + gap / 2, P.c[1]]].forEach(function (s) {
        ctx.beginPath(); ctx.moveTo(f.sx(0), f.sy(0));
        for (var i = 0; i <= 200; i++) ctx.lineTo(f.sx(i / 200), f.sy(pdf(i / 200, s[0])));
        ctx.lineTo(f.sx(1), f.sy(0)); ctx.closePath();
        ctx.fillStyle = alpha(s[1], 0.22); ctx.fill();
        ctx.strokeStyle = s[1]; ctx.lineWidth = 2; ctx.stroke();
      });
      ctx.strokeStyle = P.text; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(f.sx(t), f.pad.t); ctx.lineTo(f.sx(t), f.pad.t + f.ih); ctx.stroke();
      ctx.fillStyle = P.text; ctx.font = "12px " + css("--sans");
      ctx.fillText("predict positive →", f.sx(t) + 6, f.pad.t + 14);
      ctx.restore();
      var tp = pos.filter(function (s) { return s >= t; }).length, fn = pos.length - tp;
      var fp = neg.filter(function (s) { return s >= t; }).length, tn = neg.length - fp;
      var precision = tp + fp ? tp / (tp + fp) : 0, recall = tp / (tp + fn);
      cm.innerHTML = "";
      [["", "h"], ["predicted +", "h"], ["predicted −", "h"], ["actually +", "h"], ["TP " + tp, "good"], ["FN " + fn, "bad"],
        ["actually −", "h"], ["FP " + fp, "bad"], ["TN " + tn, "good"]].forEach(function (cell) {
        cm.appendChild(el("div", { class: cell[1], text: cell[0] }));
      });
      show([["accuracy", Math.round((100 * (tp + tn)) / 1000) + "%"], ["precision", Math.round(precision * 100) + "%"],
        ["recall", Math.round(recall * 100) + "%"], ["F1", (precision + recall ? (2 * precision * recall) / (precision + recall) : 0).toFixed(2)]]);
    }
    legend(fig, [[css("--c1"), "actually negative"], [css("--c2"), "actually positive"]]);
    sample();
    draw();
    return draw;
  };

  // Deep learning --------------------------------------------------------------------

  function gelu(x) { return 0.5 * x * (1 + Math.tanh(Math.sqrt(2 / Math.PI) * (x + 0.044715 * x * x * x))); }
  var ACTIVATIONS = [
    ["ReLU", function (x) { return Math.max(0, x); }, true],
    ["Sigmoid", function (x) { return 1 / (1 + Math.exp(-x)); }, true],
    ["Tanh", Math.tanh, true],
    ["GELU", gelu, false],
    ["Leaky ReLU", function (x) { return x > 0 ? x : 0.1 * x; }, false],
  ];

  registry.activations = function (fig) {
    var c = canvas(fig);
    var on = ACTIVATIONS.map(function (a) { return a[2]; }), deriv = false, hover = null;
    var show = readout(fig);
    var ctl = controls(fig);
    ACTIVATIONS.forEach(function (a, i) { checkbox(ctl, a[0], on[i], function (v) { on[i] = v; draw(); }); });
    checkbox(ctl, "Show slope (derivative)", false, function (v) { deriv = v; draw(); });
    var f;
    c.addEventListener("pointermove", function (e) { if (f) { hover = f.ix(e.clientX - c.getBoundingClientRect().left); draw(); } });
    c.addEventListener("pointerleave", function () { hover = null; draw(); });
    function draw() {
      var g = fit(c), P = palette(), ctx = g.ctx;
      f = frame(g.w, g.h, [-4, 4], [-1.5, 3]);
      axes(ctx, f, P, ["input", "output"]);
      ctx.save();
      clip(ctx, f);
      var info = [];
      ACTIVATIONS.forEach(function (a, i) {
        if (!on[i]) return;
        var color = P.c[i];
        [false, true].forEach(function (d) {
          if (d && !deriv) return;
          ctx.strokeStyle = color; ctx.lineWidth = d ? 1.5 : 2.5;
          ctx.setLineDash(d ? [5, 4] : []);
          ctx.beginPath();
          for (var k = 0; k <= 400; k++) {
            var x = -4 + k / 50;
            var y = d ? (a[1](x + 1e-4) - a[1](x - 1e-4)) / 2e-4 : a[1](x);
            ctx[k ? "lineTo" : "moveTo"](f.sx(x), f.sy(y));
          }
          ctx.stroke();
        });
        if (hover !== null) {
          dot(ctx, f.sx(hover), f.sy(a[1](hover)), 4, color);
          info.push([a[0], a[1](hover).toFixed(3)]);
        }
      });
      ctx.restore();
      show(hover === null ? [["hover", "move over the chart to read values"]] : [["input", hover.toFixed(2)]].concat(info));
    }
    legend(fig, ACTIVATIONS.map(function (a, i) { return [css("--c" + (i + 1)), a[0]]; }));
    return draw;
  };

  registry.descent = function (fig) {
    var c = canvas(fig, "tall");
    var c2 = canvas(fig, "short");
    var surfaces = {
      bowl: { name: "Stretched bowl", f: function (x, y) { return 0.5 * x * x + 5 * y * y; }, g: function (x, y) { return [x, 10 * y]; }, start: [-2.6, 1.5] },
      valleys: { name: "Two valleys", f: function (x, y) { return 0.25 * Math.pow(x * x - 2, 2) + y * y + 0.4 * x + 1; }, g: function (x, y) { return [x * (x * x - 2) + 0.4, 2 * y]; }, start: [2.6, 1.6] },
    };
    var s = surfaces.bowl, lr = 0.08, mom = 0, start = s.start.slice(), path = [], timer = null, bg = null;
    var show = readout(fig);
    var ctl = controls(fig);
    select(ctl, "Loss surface", [["bowl", surfaces.bowl.name], ["valleys", surfaces.valleys.name]], function (v) { s = surfaces[v]; start = s.start.slice(); bg = null; run(); });
    slider(ctl, "Learning rate", 0.01, 0.25, 0.01, lr, function (v) { lr = v; run(); }, function (v) { return v.toFixed(2); });
    slider(ctl, "Momentum", 0, 0.95, 0.05, mom, function (v) { mom = v; run(); }, function (v) { return v.toFixed(2); });
    button(ctl, "Replay", run, true);
    var f;
    pointer(c, function (x, y) { if (f) { start = [f.ix(x), f.iy(y)]; run(); } });
    function run() {
      if (timer) clearInterval(timer);
      var p = start.slice(), v = [0, 0];
      path = [p.slice()];
      var all = [p.slice()];
      for (var i = 0; i < 150; i++) {
        var gr = s.g(p[0], p[1]);
        v = [mom * v[0] - lr * gr[0], mom * v[1] - lr * gr[1]];
        p = [p[0] + v[0], p[1] + v[1]];
        all.push(p.slice());
        if (!isFinite(p[0]) || Math.abs(p[0]) > 1e3 || Math.abs(p[1]) > 1e3) break;
      }
      var n = 1;
      timer = setInterval(function () {
        n += 2;
        path = all.slice(0, n);
        draw();
        if (n >= all.length) clearInterval(timer);
      }, 30);
    }
    function background(g, P) {
      var off = document.createElement("canvas");
      var dpr = window.devicePixelRatio || 1;
      off.width = g.w * dpr; off.height = g.h * dpr;
      var o = off.getContext("2d");
      o.scale(dpr, dpr);
      var lo = Infinity, hi = -Infinity, cell = 4, vals = [];
      for (var px = 0; px < g.w; px += cell) for (var py = 0; py < g.h; py += cell) {
        var v = s.f(f.ix(px + cell / 2), f.iy(py + cell / 2));
        vals.push([px, py, v]); lo = Math.min(lo, v); hi = Math.max(hi, v);
      }
      vals.forEach(function (q) {
        var level = Math.floor((Math.log(q[2] - lo + 0.05) - Math.log(0.05)) / (Math.log(hi - lo + 0.05) - Math.log(0.05)) * 14);
        o.fillStyle = alpha(P.accent, 0.04 + (level % 2 ? 0.05 : 0) + (1 - level / 14) * 0.22);
        o.fillRect(q[0], q[1], cell, cell);
      });
      return { canvas: off, key: g.w + "x" + g.h + P.accent };
    }
    function draw() {
      var g = fit(c), P = palette(), ctx = g.ctx;
      f = frame(g.w, g.h, [-3, 3], [-3 * g.h / g.w, 3 * g.h / g.w], { l: 0, r: 0, t: 0, b: 0 });
      if (!bg || bg.key !== g.w + "x" + g.h + P.accent) bg = background(g, P);
      ctx.drawImage(bg.canvas, 0, 0, g.w, g.h);
      ctx.strokeStyle = P.c[1]; ctx.lineWidth = 2;
      ctx.beginPath();
      path.forEach(function (p, i) { ctx[i ? "lineTo" : "moveTo"](f.sx(p[0]), f.sy(p[1])); });
      ctx.stroke();
      path.forEach(function (p, i) { dot(ctx, f.sx(p[0]), f.sy(p[1]), i === path.length - 1 ? 6 : 2.5, P.c[1], i === path.length - 1 ? P.surface : null); });
      dot(ctx, f.sx(start[0]), f.sy(start[1]), 5, P.text);
      var last = path[path.length - 1] || start, loss = s.f(last[0], last[1]);
      var diverged = !isFinite(loss) || Math.abs(last[0]) > 50 || Math.abs(last[1]) > 50;
      var h = fit(c2), ctx2 = h.ctx;
      var losses = path.map(function (p) { return s.f(p[0], p[1]); }).filter(isFinite);
      var top = Math.min(Math.max.apply(null, losses.concat([1])), 60);
      var f2 = frame(h.w, h.h, [0, 150], [0, top * 1.05]);
      axes(ctx2, f2, P, ["step", "loss"]);
      ctx2.save(); clip(ctx2, f2);
      ctx2.strokeStyle = P.c[1]; ctx2.lineWidth = 2; ctx2.beginPath();
      losses.forEach(function (l, i) { ctx2[i ? "lineTo" : "moveTo"](f2.sx(i), f2.sy(Math.min(l, top * 2))); });
      ctx2.stroke(); ctx2.restore();
      show([["step", path.length - 1], ["loss", diverged ? "∞" : loss.toFixed(4)], ["status", diverged ? "diverged: learning rate too high" : "descending"]]);
    }
    run();
    return draw;
  };

  registry.playground = function (fig) {
    var c = canvas(fig, "tall");
    var data, net, epoch = 0, running = false, dataset = "circle", hidden = 6, layers = 1, lr = 0.6, act = "tanh", seed = 3, loss = 0, acc = 0;
    function makeData() {
      var r = rng(seed * 13 + dataset.length), out = [];
      for (var i = 0; i < 200; i++) {
        var x, y, label;
        if (dataset === "circle") {
          var rad = i % 2 ? r() * 0.45 : 0.65 + r() * 0.3, ang = r() * 2 * Math.PI;
          x = rad * Math.cos(ang); y = rad * Math.sin(ang); label = i % 2;
        } else if (dataset === "xor") {
          x = r() * 2 - 1; y = r() * 2 - 1;
          x += x > 0 ? 0.05 : -0.05; y += y > 0 ? 0.05 : -0.05; label = x * y > 0 ? 1 : 0;
        } else if (dataset === "spiral") {
          var t = (i >> 1) / 100 * 2.6 * Math.PI, side = i % 2;
          var rr = 0.1 + (i >> 1) / 100 * 0.85;
          x = rr * Math.cos(t + side * Math.PI) + 0.03 * gauss(r); y = rr * Math.sin(t + side * Math.PI) + 0.03 * gauss(r); label = side;
        } else {
          label = i % 2; x = (label ? 0.4 : -0.4) + 0.25 * gauss(r); y = (label ? 0.3 : -0.3) + 0.25 * gauss(r);
        }
        out.push([x, y, label]);
      }
      data = out;
    }
    function makeNet() {
      var r = rng(seed * 7 + hidden + layers), sizes = [2];
      for (var i = 0; i < layers; i++) sizes.push(hidden);
      sizes.push(1);
      net = [];
      for (i = 1; i < sizes.length; i++) {
        var n = sizes[i], m = sizes[i - 1], scale = Math.sqrt(2 / (n + m)), W = [];
        for (var j = 0; j < n; j++) { var row = []; for (var k = 0; k < m; k++) row.push(gauss(r) * scale); W.push(row); }
        net.push({ W: W, b: new Array(n).fill(0) });
      }
      epoch = 0;
    }
    function a(z) { return act === "tanh" ? Math.tanh(z) : Math.max(0, z); }
    function da(out) { return act === "tanh" ? 1 - out * out : out > 0 ? 1 : 0; }
    function forward(x) {
      var outs = [x];
      net.forEach(function (L, li) {
        var prev = outs[outs.length - 1], cur = [];
        for (var j = 0; j < L.W.length; j++) {
          var z = L.b[j];
          for (var k = 0; k < prev.length; k++) z += L.W[j][k] * prev[k];
          cur.push(li === net.length - 1 ? 1 / (1 + Math.exp(-z)) : a(z));
        }
        outs.push(cur);
      });
      return outs;
    }
    function train() {
      var grads = net.map(function (L) { return { W: L.W.map(function (row) { return row.map(function () { return 0; }); }), b: L.b.map(function () { return 0; }) }; });
      var total = 0, correct = 0;
      data.forEach(function (d) {
        var outs = forward([d[0], d[1]]), p = outs[outs.length - 1][0];
        total += -(d[2] * Math.log(p + 1e-9) + (1 - d[2]) * Math.log(1 - p + 1e-9));
        if ((p >= 0.5 ? 1 : 0) === d[2]) correct++;
        var delta = [p - d[2]];
        for (var li = net.length - 1; li >= 0; li--) {
          var prev = outs[li], L = net[li], next = new Array(prev.length).fill(0);
          for (var j = 0; j < L.W.length; j++) {
            grads[li].b[j] += delta[j];
            for (var k = 0; k < prev.length; k++) { grads[li].W[j][k] += delta[j] * prev[k]; next[k] += delta[j] * L.W[j][k]; }
          }
          if (li > 0) delta = next.map(function (v, k) { return v * da(prev[k]); });
        }
      });
      net.forEach(function (L, li) {
        for (var j = 0; j < L.W.length; j++) {
          L.b[j] -= (lr * grads[li].b[j]) / data.length;
          for (var k = 0; k < L.W[j].length; k++) L.W[j][k] -= (lr * grads[li].W[j][k]) / data.length;
        }
      });
      loss = total / data.length; acc = correct / data.length; epoch++;
    }
    function loop() {
      if (!running) return;
      for (var i = 0; i < 20; i++) train();
      draw();
      if (epoch >= 3000) { running = false; playBtn.textContent = "Train"; }
      requestAnimationFrame(loop);
    }
    var show = readout(fig);
    var ctl = controls(fig);
    var playBtn = button(ctl, "Train", function () {
      running = !running; playBtn.textContent = running ? "Pause" : "Train";
      if (running) requestAnimationFrame(loop);
    }, true);
    button(ctl, "Reset weights", function () { seed++; makeNet(); train(); draw(); });
    select(ctl, "Data", [["circle", "Circle"], ["xor", "XOR"], ["spiral", "Spiral (hard)"], ["blobs", "Two blobs"]], function (v) { dataset = v; makeData(); makeNet(); train(); draw(); });
    slider(ctl, "Hidden layers", 1, 3, 1, layers, function (v) { layers = v; makeNet(); train(); draw(); });
    slider(ctl, "Neurons per layer", 1, 12, 1, hidden, function (v) { hidden = v; makeNet(); train(); draw(); });
    slider(ctl, "Learning rate", 0.03, 1.5, 0.03, lr, function (v) { lr = v; }, function (v) { return v.toFixed(2); });
    select(ctl, "Activation", [["tanh", "Tanh"], ["relu", "ReLU"]], function (v) { act = v; makeNet(); train(); draw(); });
    function draw() {
      var g = fit(c), P = palette(), ctx = g.ctx;
      var span = 1.25, aspect = g.w / g.h;
      var f = frame(g.w, g.h, [-span * aspect, span * aspect], [-span, span], { l: 0, r: 0, t: 0, b: 0 });
      var cell = 6;
      for (var px = 0; px < g.w; px += cell) for (var py = 0; py < g.h; py += cell) {
        var outs = forward([f.ix(px + cell / 2), f.iy(py + cell / 2)]), p = outs[outs.length - 1][0];
        ctx.fillStyle = alpha(p >= 0.5 ? P.c[1] : P.c[0], 0.06 + Math.abs(p - 0.5) * 0.5);
        ctx.fillRect(px, py, cell, cell);
      }
      data.forEach(function (d) { dot(ctx, f.sx(d[0]), f.sy(d[1]), 3.8, d[2] ? P.c[1] : P.c[0], P.surface); });
      var params = net.reduce(function (s, L) { return s + L.W.length * (L.W[0].length + 1); }, 0);
      show([["epoch", epoch], ["loss", loss.toFixed(3)], ["accuracy", Math.round(acc * 100) + "%"], ["parameters", params]]);
    }
    makeData(); makeNet(); train();
    return draw;
  };

  registry.conv = function (fig) {
    var c = canvas(fig);
    var N = 16, img = [];
    for (var y = 0; y < N; y++) for (var x = 0; x < N; x++) {
      var d = Math.hypot(x - 7.5, y - 7.5);
      img.push((d > 4.2 && d < 6.4) || (x >= 6 && x <= 9 && y >= 3 && y <= 12 && x - 6 === Math.floor((y - 3) / 3)) ? 1 : 0);
    }
    var kernels = {
      vertical: ["Vertical edges", [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]]],
      horizontal: ["Horizontal edges", [[-1, -2, -1], [0, 0, 0], [1, 2, 1]]],
      blur: ["Blur", [[1 / 9, 1 / 9, 1 / 9], [1 / 9, 1 / 9, 1 / 9], [1 / 9, 1 / 9, 1 / 9]]],
      sharpen: ["Sharpen", [[0, -1, 0], [-1, 5, -1], [0, -1, 0]]],
      outline: ["Outline", [[-1, -1, -1], [-1, 8, -1], [-1, -1, -1]]],
    };
    var kernel = kernels.vertical[1], hover = null, painting = null, layout;
    var ctl = controls(fig);
    select(ctl, "Filter", Object.keys(kernels).map(function (k) { return [k, kernels[k][0]]; }), function (v) { kernel = kernels[v][1]; draw(); });
    button(ctl, "Clear image", function () { img = img.map(function () { return 0; }); draw(); });
    var show = readout(fig);
    function at(i, j) { return i < 0 || j < 0 || i >= N || j >= N ? 0 : img[j * N + i]; }
    function out(i, j) {
      var s = 0;
      for (var dy = -1; dy <= 1; dy++) for (var dx = -1; dx <= 1; dx++) s += kernel[dy + 1][dx + 1] * at(i + dx, j + dy);
      return s;
    }
    function cellAt(x, y) {
      if (!layout) return null;
      for (var k = 0; k < 2; k++) {
        var L = layout[k], i = Math.floor((x - L.x) / L.s), j = Math.floor((y - L.y) / L.s);
        if (i >= 0 && j >= 0 && i < N && j < N) return { grid: k, i: i, j: j };
      }
      return null;
    }
    c.addEventListener("pointerdown", function (e) {
      var r = c.getBoundingClientRect(), hit = cellAt(e.clientX - r.left, e.clientY - r.top);
      if (hit && hit.grid === 0) { painting = img[hit.j * N + hit.i] ? 0 : 1; img[hit.j * N + hit.i] = painting; draw(); }
    });
    c.addEventListener("pointermove", function (e) {
      var r = c.getBoundingClientRect(), hit = cellAt(e.clientX - r.left, e.clientY - r.top);
      hover = hit && hit.grid === 1 ? hit : null;
      if (hit && hit.grid === 0 && painting !== null) img[hit.j * N + hit.i] = painting;
      draw();
    });
    window.addEventListener("pointerup", function () { painting = null; });
    c.addEventListener("pointerleave", function () { hover = null; draw(); });
    function draw() {
      var g = fit(c), P = palette(), ctx = g.ctx;
      var size = Math.min((g.w - 150) / 2, g.h - 40), s = Math.floor(size / N);
      var gridW = s * N, left = (g.w - (gridW * 2 + 120)) / 2, top = (g.h - gridW) / 2 + 8;
      layout = [{ x: left, y: top, s: s }, { x: left + gridW + 120, y: top, s: s }];
      ctx.font = "12px " + css("--sans");
      ctx.fillStyle = P.muted;
      ctx.fillText("input (click to draw)", layout[0].x, top - 8);
      ctx.fillText("feature map", layout[1].x, top - 8);
      var vals = [], maxAbs = 1e-6;
      for (var j = 0; j < N; j++) for (var i = 0; i < N; i++) { var v = out(i, j); vals.push(v); maxAbs = Math.max(maxAbs, Math.abs(v)); }
      for (j = 0; j < N; j++) for (i = 0; i < N; i++) {
        ctx.fillStyle = img[j * N + i] ? P.text : P.surface;
        ctx.fillRect(layout[0].x + i * s, top + j * s, s - 1, s - 1);
        v = vals[j * N + i] / maxAbs;
        ctx.fillStyle = P.surface;
        ctx.fillRect(layout[1].x + i * s, top + j * s, s - 1, s - 1);
        ctx.fillStyle = alpha(v >= 0 ? P.c[0] : P.c[1], Math.abs(v));
        ctx.fillRect(layout[1].x + i * s, top + j * s, s - 1, s - 1);
      }
      var kx = left + gridW + 24, ks = 24;
      ctx.fillStyle = P.muted;
      ctx.fillText("3×3 filter", kx, top + gridW / 2 - 46);
      for (j = 0; j < 3; j++) for (i = 0; i < 3; i++) {
        var w = kernel[j][i];
        ctx.fillStyle = P.surface; ctx.fillRect(kx + i * ks, top + gridW / 2 - 36 + j * ks, ks - 2, ks - 2);
        ctx.fillStyle = P.text; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(Math.abs(w - 1 / 9) < 1e-9 ? "⅑" : String(w), kx + i * ks + ks / 2 - 1, top + gridW / 2 - 36 + j * ks + ks / 2 - 1);
      }
      ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
      if (hover) {
        ctx.strokeStyle = P.accent; ctx.lineWidth = 2;
        ctx.strokeRect(layout[1].x + hover.i * s - 1, top + hover.j * s - 1, s + 1, s + 1);
        ctx.strokeRect(layout[0].x + (hover.i - 1) * s - 1, top + (hover.j - 1) * s - 1, 3 * s + 1, 3 * s + 1);
      }
      show(hover ? [["output at", "(" + hover.i + ", " + hover.j + ")"], ["value", out(hover.i, hover.j).toFixed(2)], ["computed from", "the 3×3 input patch outlined on the left"]]
        : [["tip", "hover the feature map to see which input pixels produce each value"]]);
    }
    legend(fig, [[css("--c1"), "positive response"], [css("--c2"), "negative response"]]);
    return draw;
  };

  // LLMs -----------------------------------------------------------------------------

  var VOCAB = (" the a an and of to in is it that for on with as was are be this by at from or not have " +
    "you your we they he she his her i my me our will can do does did has had more most new model models " +
    "language large learn learning train training data token tokens text word words neural network networks " +
    "transformer attention context window prompt answer question machine deep layer layers weight weights " +
    "cat sat mat dog pattern patterns letter letters hello world how what why when who which where there their about into than then " +
    "use used using make made time people one two three first good great very just like").split(" ").filter(Boolean);
  var PIECES = ["ing", "tion", "ation", "ed", "er", "ers", "est", "ly", "ness", "ment", "able", "ize", "ise", "ous", "ful",
    "less", "un", "re", "pre", "dis", "in", "token", "ization", "iz", "s", "es", "al", "ic", "ity", "ive", "ent", "ant",
    "con", "pro", "com", "trans", "form", "ate", "ar", "or", "an", "en", "on", "at", "th", "ch", "sh", "st", "qu",
    "be", "lie", "liev", "ev", "el", "ter", "tern", "pat", "ab", "ow", "ou", "ight", "ake", "ook", "all", "ell", "ill"];

  function tokenize(text) {
    var words = {}, pieces = {}, out = [];
    VOCAB.forEach(function (w) { words[" " + w] = 1; words[w] = 1; });
    VOCAB.concat(PIECES).forEach(function (w) { pieces[w] = 1; });
    var re = /( ?[A-Za-z]+| ?[0-9]{1,3}| ?[^\sA-Za-z0-9]|\s+)/g, m;
    while ((m = re.exec(text))) {
      var chunk = m[0];
      if (words[chunk.toLowerCase()] || /^ ?[0-9]+$/.test(chunk) || !/[A-Za-z]{2}/.test(chunk)) { out.push(chunk); continue; }
      var lead = chunk[0] === " " ? " " : "", rest = chunk.slice(lead.length), first = true;
      while (rest.length) {
        var take = 1;
        for (var len = Math.min(rest.length, 8); len > 1; len--) {
          var cand = rest.slice(0, len).toLowerCase();
          if (pieces[cand]) { take = len; break; }
        }
        out.push((first ? lead : "") + rest.slice(0, take));
        rest = rest.slice(take); first = false;
      }
    }
    return out;
  }

  function hashId(s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return (h >>> 0) % 100000;
  }

  registry.tokenizer = function (fig) {
    var ctl = controls(fig);
    var input = el("input", { type: "text", value: "Transformers learn patterns from tokenization, not letters. Unbelievable!", "aria-label": "Text to tokenize" });
    ctl.appendChild(input);
    var showIds = false;
    checkbox(ctl, "Show token IDs", false, function (v) { showIds = v; draw(); });
    var box = el("div", { class: "tokens", "aria-live": "polite" });
    fig.appendChild(box);
    var show = readout(fig);
    input.addEventListener("input", draw);
    function draw() {
      var P = palette(), toks = tokenize(input.value);
      box.textContent = "";
      toks.forEach(function (t, i) {
        var s = el("span", { text: showIds ? String(hashId(t)) : t.replace(/ /g, "·") });
        s.style.background = alpha(P.c[i % 6], 0.22);
        s.title = JSON.stringify(t);
        box.appendChild(s);
      });
      var words = input.value.trim() ? input.value.trim().split(/\s+/).length : 0;
      show([["characters", input.value.length], ["words", words], ["tokens", toks.length], ["characters per token", toks.length ? (input.value.length / toks.length).toFixed(1) : "—"]]);
    }
    return draw;
  };

  registry.sampling = function (fig) {
    var c = canvas(fig);
    var cands = [["mat", 3.2], ["floor", 2.6], ["sofa", 2.3], ["chair", 1.9], ["bed", 1.7], ["windowsill", 1.2], ["roof", 0.8], ["keyboard", 0.6], ["moon", -0.5], ["piano", -0.8]];
    var T = 1, topK = 10, topP = 1, picks = [], r = rng(99);
    var sentence = el("p", { class: "tokens" });
    fig.insertBefore(sentence, c);
    var ctl = controls(fig);
    slider(ctl, "Temperature", 0.1, 2, 0.05, T, function (v) { T = v; draw(); }, function (v) { return v.toFixed(2); });
    slider(ctl, "Top-k", 1, 10, 1, topK, function (v) { topK = v; draw(); });
    slider(ctl, "Top-p", 0.1, 1, 0.05, topP, function (v) { topP = v; draw(); }, function (v) { return v.toFixed(2); });
    button(ctl, "Sample next token", function () { sampleOne(); draw(); }, true);
    button(ctl, "Sample 20", function () { for (var i = 0; i < 20; i++) sampleOne(); draw(); });
    var show = readout(fig);
    function dist() {
      var z = cands.map(function (x) { return x[1] / T; }), mx = Math.max.apply(null, z);
      var e = z.map(function (v) { return Math.exp(v - mx); }), sum = e.reduce(function (a, b) { return a + b; }, 0);
      var p = e.map(function (v) { return v / sum; });
      var order = p.map(function (v, i) { return i; }).sort(function (a, b) { return p[b] - p[a]; });
      var keep = {}, cum = 0;
      for (var n = 0; n < order.length && n < topK; n++) {
        keep[order[n]] = true; cum += p[order[n]];
        if (cum >= topP) break;
      }
      var kept = p.map(function (v, i) { return keep[i] ? v : 0; }), ks = kept.reduce(function (a, b) { return a + b; }, 0);
      return { raw: p, final: kept.map(function (v) { return v / ks; }) };
    }
    function sampleOne() {
      var d = dist().final, u = r(), acc = 0;
      for (var i = 0; i < d.length; i++) { acc += d[i]; if (u <= acc) { picks.push(cands[i][0]); break; } }
      if (picks.length > 40) picks = picks.slice(-40);
    }
    function draw() {
      var g = fit(c), P = palette(), ctx = g.ctx, d = dist();
      var left = 96, right = 60, rowH = (g.h - 16) / cands.length, max = Math.max.apply(null, d.final);
      ctx.font = "13px " + css("--sans");
      ctx.textBaseline = "middle";
      cands.forEach(function (cand, i) {
        var y = 8 + i * rowH, w = ((g.w - left - right) * d.final[i]) / Math.max(max, 1e-9);
        var removed = d.final[i] === 0;
        ctx.fillStyle = removed ? P.muted : P.text; ctx.textAlign = "right";
        ctx.fillText(cand[0], left - 10, y + rowH / 2);
        ctx.fillStyle = P.grid;
        ctx.fillRect(left, y + rowH * 0.2, g.w - left - right, rowH * 0.6);
        if (!removed) { ctx.fillStyle = P.accent; ctx.fillRect(left, y + rowH * 0.2, Math.max(1, w), rowH * 0.6); }
        ctx.fillStyle = removed ? P.muted : P.text; ctx.textAlign = "left";
        ctx.fillText(removed ? "cut" : Math.round(d.final[i] * 100) + "%", g.w - right + 8, y + rowH / 2);
      });
      sentence.textContent = "";
      sentence.appendChild(el("span", { text: "The cat sat on the" }));
      var last = picks[picks.length - 1];
      var token = el("span", { text: last || "___" });
      token.style.background = alpha(P.c[0], 0.25);
      sentence.appendChild(token);
      var counts = {};
      picks.forEach(function (p) { counts[p] = (counts[p] || 0) + 1; });
      var kept = d.final.filter(function (v) { return v > 0; }).length;
      show([["candidates kept", kept + " of " + cands.length], ["recent samples", picks.length ? Object.keys(counts).map(function (k) { return k + "×" + counts[k]; }).join(", ") : "none yet"]]);
    }
    return draw;
  };

  registry.attention = function (fig) {
    var words = ["The", "animal", "didn't", "cross", "the", "street", "because", "it", "was", "too", "tired", "."];
    var ending = "tired", head = "meaning", focus = 7;
    var ctl = controls(fig);
    select(ctl, "Sentence ends with", [["tired", "…too tired."], ["wide", "…too wide."]], function (v) { ending = v; words[10] = v; draw(); });
    select(ctl, "Attention head", [["meaning", "Head A: what words refer to"], ["previous", "Head B: the previous word"]], function (v) { head = v; draw(); });
    var row = el("div", { class: "attn", role: "group", "aria-label": "Sentence tokens" });
    fig.appendChild(row);
    var c = canvas(fig, "tall");
    var show = readout(fig);
    function weights() {
      var n = words.length, M = [];
      var target = ending === "tired" ? 1 : 5;
      var links = { 1: { 0: 2 }, 2: { 1: 3 }, 3: { 1: 2, 2: 2 }, 5: { 4: 3, 3: 2 }, 6: { 3: 2, 2: 1 }, 7: {}, 8: { 7: 3 }, 9: { 8: 2 }, 10: { 7: 2, 9: 1 }, 11: { 10: 2, 7: 1 } };
      links[7][target] = 6;
      links[10][target] = 4;
      for (var q = 0; q < n; q++) {
        var rowW = [];
        for (var k = 0; k < n; k++) {
          var s = k > q ? 0 : head === "previous" ? (k === q - 1 ? 8 : k === q ? 1.5 : 0.25) : 0.3 + (k === q ? 1 : 0) + ((links[q] || {})[k] || 0);
          rowW.push(s);
        }
        var sum = rowW.reduce(function (a, b) { return a + b; }, 0);
        M.push(rowW.map(function (v) { return v / sum; }));
      }
      return M;
    }
    function draw() {
      var P = palette(), M = weights();
      row.textContent = "";
      words.forEach(function (w, i) {
        var b = el("button", { type: "button", text: w, "aria-pressed": String(i === focus) });
        var wt = i <= focus ? M[focus][i] : 0;
        b.style.background = alpha(P.c[0], Math.min(0.85, wt * 1.6));
        if (i > focus) b.style.opacity = 0.4;
        b.addEventListener("click", function () { focus = i; draw(); });
        b.addEventListener("mouseenter", function () { focus = i; draw(); });
        row.appendChild(b);
      });
      var g = fit(c), ctx = g.ctx, n = words.length;
      var left = 70, top = 64, size = Math.min((g.w - left - 10) / n, (g.h - top - 8) / n);
      ctx.font = "11px " + css("--sans");
      for (var q = 0; q < n; q++) {
        ctx.fillStyle = q === focus ? P.text : P.muted; ctx.textAlign = "right"; ctx.textBaseline = "middle";
        ctx.fillText(words[q], left - 6, top + q * size + size / 2);
        for (var k = 0; k < n; k++) {
          ctx.fillStyle = k > q ? P.surface : alpha(P.c[0], 0.06 + M[q][k] * 0.94);
          ctx.fillRect(left + k * size, top + q * size, size - 2, size - 2);
        }
      }
      for (k = 0; k < n; k++) {
        ctx.save(); ctx.translate(left + k * size + size / 2, top - 6); ctx.rotate(-Math.PI / 4);
        ctx.fillStyle = P.muted; ctx.textAlign = "left"; ctx.fillText(words[k], 0, 0); ctx.restore();
      }
      ctx.strokeStyle = P.accent; ctx.lineWidth = 2;
      ctx.strokeRect(left - 1, top + focus * size - 1, n * size, size);
      var best = 0;
      for (k = 0; k <= focus; k++) if (k !== focus && M[focus][k] > (best === focus ? -1 : M[focus][best])) best = k;
      show([["selected", "“" + words[focus] + "”"], ["looks most at", focus ? "“" + words[best] + "” (" + Math.round(M[focus][best] * 100) + "%)" : "only itself"], ["future words", "masked (a decoder only sees earlier tokens)"]]);
    }
    return draw;
  };

  registry.embeddings = function (fig) {
    var c = canvas(fig, "tall");
    var words = [
      ["king", -1.9, 0.8, 0], ["queen", -1.9, 2.1, 0], ["man", -3.1, 0.6, 0], ["woman", -3.1, 1.9, 0], ["prince", -2.3, 0.2, 0], ["princess", -2.3, 1.5, 0],
      ["cat", 1.2, 2.3, 1], ["dog", 1.7, 2.0, 1], ["lion", 0.7, 2.8, 1], ["tiger", 0.9, 3.1, 1], ["horse", 2.2, 2.6, 1],
      ["apple", 2.4, -1.6, 2], ["banana", 2.9, -1.2, 2], ["bread", 1.8, -2.2, 2], ["cheese", 2.5, -2.5, 2],
      ["python", -0.6, -2.1, 3], ["java", -1.3, -2.6, 3], ["compiler", -1.0, -1.5, 3], ["function", -1.8, -1.9, 3], ["code", -0.4, -2.8, 3],
    ];
    var analogy = true, hover = null, f;
    var ctl = controls(fig);
    checkbox(ctl, "Show king − man + woman", true, function (v) { analogy = v; draw(); });
    var show = readout(fig);
    c.addEventListener("pointermove", function (e) {
      if (!f) return;
      var r = c.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, best = null, bd = 900;
      words.forEach(function (w, i) { var d = Math.pow(f.sx(w[1]) - x, 2) + Math.pow(f.sy(w[2]) - y, 2); if (d < bd) { bd = d; best = i; } });
      if (best !== hover) { hover = best; draw(); }
    });
    function arrow(ctx, a, b, color) {
      var x1 = f.sx(a[1]), y1 = f.sy(a[2]), x2 = f.sx(b[1]), y2 = f.sy(b[2]), ang = Math.atan2(y2 - y1, x2 - x1);
      ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2 - 9 * Math.cos(ang), y2 - 9 * Math.sin(ang)); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x2 - 2 * Math.cos(ang), y2 - 2 * Math.sin(ang));
      ctx.lineTo(x2 - 12 * Math.cos(ang - 0.4), y2 - 12 * Math.sin(ang - 0.4));
      ctx.lineTo(x2 - 12 * Math.cos(ang + 0.4), y2 - 12 * Math.sin(ang + 0.4)); ctx.fill();
    }
    function draw() {
      var g = fit(c), P = palette(), ctx = g.ctx;
      var aspect = g.w / g.h;
      f = frame(g.w, g.h, [-4 * aspect * 0.9, 4 * aspect * 0.9], [-3.6, 3.9], { l: 10, r: 10, t: 10, b: 10 });
      var colors = [P.c[0], P.c[1], P.c[2], P.c[3]];
      if (analogy) {
        arrow(ctx, words[2], words[3], P.muted);
        arrow(ctx, words[0], words[1], P.accent);
      }
      ctx.font = "13px " + css("--sans");
      words.forEach(function (w, i) {
        var X = f.sx(w[1]), Y = f.sy(w[2]);
        dot(ctx, X, Y, i === hover ? 7 : 5, colors[w[3]], P.surface);
        ctx.fillStyle = P.text; ctx.textBaseline = "middle"; ctx.textAlign = "left";
        ctx.fillText(w[0], X + 9, Y);
      });
      if (hover === null) { show([["tip", "hover a word to see its nearest neighbours"]]); return; }
      var h = words[hover];
      var near = words.filter(function (w) { return w !== h; }).map(function (w) { return [w[0], Math.hypot(w[1] - h[1], w[2] - h[2])]; })
        .sort(function (a, b) { return a[1] - b[1]; }).slice(0, 3).map(function (n) { return n[0]; });
      show([["word", h[0]], ["nearest", near.join(", ")]]);
    }
    legend(fig, [[css("--c1"), "people & royalty"], [css("--c2"), "animals"], [css("--c3"), "food"], [css("--c4"), "programming"]]);
    return draw;
  };

  // Boot -----------------------------------------------------------------------------

  document.querySelectorAll("[data-viz]").forEach(function (fig) {
    var make = registry[fig.dataset.viz];
    if (!make) return;
    try {
      var draw = make(fig);
      redraws.push(draw);
      draw();
    } catch (e) {
      if (window.console) console.error("figure failed:", fig.dataset.viz, e);
    }
  });

  var pending = false;
  function redrawAll() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () {
      pending = false;
      redraws.forEach(function (d) { try { d(); } catch (e) {} });
    });
  }
  window.addEventListener("resize", redrawAll);
  document.addEventListener("themechange", redrawAll);
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    if (mq.addEventListener) mq.addEventListener("change", redrawAll);
  }
})();
