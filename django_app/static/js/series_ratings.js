// Season rating chart on a show page (Tasks 130-131).
//
// One area chart under the Episodes section's table, following the season
// picker: a season draws its episodes one by one against the season
// average; "All seasons" draws each season's average episode rating against
// the show-wide one. The picker's "All seasons" option drives only this
// chart -- the table above keeps the season last picked (theoria.js). Every
// number comes from the #season-chart-data block movies/season_stats.py
// builds; this file only draws it.
//
// Drawn in the site's own instrument language rather than a stock chart
// look: one lime line (the measurement mark) over a lime gradient that fades
// into the page, hairline gridlines, mono figures, no axis boxes, no dots at
// rest. Points appear only on hover, with a hairline guide down the chart
// and a tooltip that is an HTML card styled by the same CSS tokens as the
// rest of the page. The best and lowest points carry a small labelled
// marker, so the reader sees where the show peaks without hovering.
//
// The figure stays hidden until a chart is actually drawn, so no JS, or
// Chart.js failing to load, leaves only the table -- which already carries
// every number.
(function () {
  "use strict";

  var figure = document.querySelector("[data-season-chart]");
  var canvas = document.getElementById("season-rating-chart");
  var data = readJSON("season-chart-data");
  if (!figure || !canvas || !data || typeof Chart === "undefined") return;

  var picker = document.querySelector("[data-season-picker]");
  var stage = figure.querySelector("[data-chart-stage]");
  var tipEl = figure.querySelector("[data-chart-tip]");
  var refValue = figure.querySelector("[data-ref-value]");
  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Words come from the figure's data-t-* attributes, rendered by the
  // template with the same msgids as the rest of the page.
  var T = figure.dataset;

  // Uzbek formats as Russian, same as theoria.js and analytics.js: browser
  // Intl data for "uz" is unreliable.
  var LANG = (function () {
    var lang = document.documentElement.lang || "en";
    return lang === "uz" ? "ru" : lang;
  })();

  var FONT_BODY = '"Instrument Sans", system-ui, sans-serif';
  var FONT_MONO = '"Spline Sans Mono", ui-monospace, monospace';

  function readJSON(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return null;
    }
  }

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    v = v ? v.trim() : "";
    return v || fallback;
  }

  // "#a3e635" / "#abc" / "rgb(...)" -> "rgba(r, g, b, a)", so the gradient
  // is built from the theme's own lime rather than a second hardcoded one.
  function withAlpha(color, alpha) {
    var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color);
    var r, g, b;
    if (m) {
      var hex = m[1];
      if (hex.length === 3) hex = hex.replace(/./g, "$&$&");
      r = parseInt(hex.slice(0, 2), 16);
      g = parseInt(hex.slice(2, 4), 16);
      b = parseInt(hex.slice(4, 6), 16);
    } else {
      var parts = color.match(/[\d.]+/g);
      if (!parts || parts.length < 3) return color;
      r = parts[0]; g = parts[1]; b = parts[2];
    }
    return "rgba(" + r + ", " + g + ", " + b + ", " + alpha + ")";
  }

  var MARK, RULE, RULE_STRONG, INK, INK_MUTED, INK_FAINT, PAPER, DARK;
  function readPalette() {
    MARK = cssVar("--lime-mark", "#65a30d");
    RULE = cssVar("--rule", "#e3e2dd");
    RULE_STRONG = cssVar("--rule-strong", "#c9c7c0");
    INK = cssVar("--ink", "#0b0b0b");
    INK_MUTED = cssVar("--ink-muted", "#57534e");
    INK_FAINT = cssVar("--ink-faint", "#78716c");
    PAPER = cssVar("--paper", "#ffffff");
    DARK = document.documentElement.getAttribute("data-theme") === "dark";
  }

  // Ratings print unlocalized ("8.2"), like every rating badge on the site;
  // averages carry a second decimal (a mean earns it); vote counts take the
  // language's digit grouping.
  function one(n) {
    return Number(n).toFixed(1);
  }
  function two(n) {
    return Number(n).toFixed(2);
  }
  function grouped(n) {
    return Number(n).toLocaleString(LANG);
  }

  // The axis is zoomed to the plotted values with half a point either side,
  // so the gap between an 8.26 and an 8.54 season is visible -- on a 0-10
  // axis every show flattens into the same line near 8. Capped at the
  // scale's real bounds. A span wider than 2.5 points snaps to whole
  // numbers, so the gridlines step evenly (5, 6, 7 ... not 5.5, 6, 7).
  function boundsFor(values) {
    var min = Math.max(0, Math.floor((Math.min.apply(null, values) - 0.5) * 2) / 2);
    var max = Math.min(10, Math.ceil((Math.max.apply(null, values) + 0.5) * 2) / 2);
    if (max - min > 2.5) {
      min = Math.floor(min);
      max = Math.ceil(max);
    }
    return { min: min, max: max, step: max - min > 2.5 ? 1 : 0.5 };
  }

  // Index of the highest and lowest plotted value. For the season trend a
  // season resting on fewer than three ratings can't be crowned best or
  // lowest while seasons with a real sample exist.
  function extremes(values, thin) {
    var idx = [];
    values.forEach(function (v, i) {
      if (v != null && !(thin && thin[i])) idx.push(i);
    });
    if (idx.length < 2 && thin) {
      idx = [];
      values.forEach(function (v, i) { if (v != null) idx.push(i); });
    }
    if (idx.length < 2) return null;
    var best = idx[0], low = idx[0];
    idx.forEach(function (i) {
      if (values[i] > values[best]) best = i;
      if (values[i] < values[low]) low = i;
    });
    if (values[best] === values[low]) return null;
    return { best: best, low: low };
  }

  function easeOut(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  // Everything drawn on the canvas that isn't the line itself: the hover
  // guide and the dashed average under the data, then the line revealed left
  // to right (a clip that widens with chart.$reveal), then the best/lowest
  // markers over it. The average's value is printed in the key, not on the
  // canvas: on a steady show both ends of the line run along it, so any
  // in-plot label landed on a point.
  var overlay = {
    id: "seasonOverlay",
    beforeDatasetsDraw: function (chart, args, opts) {
      var ctx = chart.ctx;
      var area = chart.chartArea;
      var y = chart.scales.y;
      ctx.save();

      var active = chart.getActiveElements();
      if (active.length) {
        var gx = Math.round(active[0].element.x) + 0.5;
        ctx.strokeStyle = RULE_STRONG;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(gx, area.top);
        ctx.lineTo(gx, area.bottom);
        ctx.stroke();
      }

      if (opts.reference != null) {
        var ry = Math.round(y.getPixelForValue(opts.reference)) + 0.5;
        ctx.strokeStyle = INK_FAINT;
        ctx.globalAlpha = 0.8;
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 4]);
        ctx.beginPath();
        ctx.moveTo(area.left, ry);
        ctx.lineTo(area.right, ry);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;

      }

      ctx.restore();

      var p = chart.$reveal == null ? 1 : chart.$reveal;
      ctx.save();
      ctx.beginPath();
      ctx.rect(area.left - 8, 0, (area.width + 16) * p, chart.height);
      ctx.clip();
    },
    afterDatasetsDraw: function (chart, args, opts) {
      var ctx = chart.ctx;
      var area = chart.chartArea;
      ctx.restore();
      if ((chart.$reveal == null ? 1 : chart.$reveal) < 1 || !opts.extremes) return;

      var points = chart.getDatasetMeta(0).data;
      var values = chart.data.datasets[0].data;
      ctx.save();
      ctx.font = "500 11px " + FONT_MONO;
      ctx.textAlign = "center";
      [["best", -1], ["low", 1]].forEach(function (pair) {
        var i = opts.extremes[pair[0]];
        var pt = points[i];
        if (!pt) return;
        var isBest = pair[0] === "best";
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, 4.5, 0, Math.PI * 2);
        ctx.lineWidth = 2;
        ctx.fillStyle = isBest ? MARK : PAPER;
        ctx.strokeStyle = isBest ? PAPER : MARK;
        ctx.fill();
        ctx.stroke();
        var text = opts.format(values[i]);
        var half = ctx.measureText(text).width / 2 + 2;
        var tx = Math.min(Math.max(pt.x, area.left + half), area.right - half);
        ctx.fillStyle = isBest ? INK : INK_MUTED;
        ctx.textBaseline = isBest ? "bottom" : "top";
        ctx.fillText(text, tx, pt.y + pair[1] * 9);
      });
      ctx.restore();
    },
  };

  function seasonView() {
    var seasons = data.seasons || [];
    var values = seasons.map(function (s) { return s.avg; });
    var plotted = values.filter(function (v) { return v != null; });
    if (plotted.length < 2) return null;
    return {
      labels: seasons.map(function (s) { return String(s.number); }),
      values: values,
      extremes: extremes(values, seasons.map(function (s) { return !!s.few; })),
      format: two,
      reference: data.show_avg,
      bounds: boundsFor(plotted),
      axis: T.tSeason,
      aria: T.tAriaSeasons,
      title: function (i) {
        var s = seasons[i];
        return s.name || T.tSeason + " " + s.number;
      },
      sub: function () { return ""; },
      rows: function (i) {
        var s = seasons[i];
        if (s.avg == null) return null;
        var out = [
          [T.tAverage, two(s.avg)],
          [T.tRange, one(s.low) + "–" + one(s.high)],
          [T.tRated, s.rated + " / " + s.episodes],
          [T.tVotes, grouped(s.votes)],
        ];
        if (s.few) out.push([T.tFew, ""]);
        return out;
      },
    };
  }

  function episodeView(number) {
    var season = (data.episodes || {})[number];
    if (!season) return null;
    var points = season.points;
    var values = points.map(function (p) { return p.rating; });
    var plotted = values.filter(function (v) { return v != null; });
    if (!plotted.length) return null;
    return {
      labels: points.map(function (p) {
        return p.number != null ? String(p.number) : "";
      }),
      values: values,
      extremes: extremes(values, null),
      format: one,
      reference: season.avg,
      bounds: boundsFor(plotted),
      axis: T.tEpisode,
      aria: T.tAriaEpisodes,
      title: function (i) {
        var p = points[i];
        return T.tEpisode + " " + (p.number != null ? p.number : "—");
      },
      sub: function (i) { return points[i].name; },
      rows: function (i) {
        var p = points[i];
        if (p.rating == null) return null;
        return [[T.tRating, one(p.rating)], [T.tVotes, grouped(p.votes)]];
      },
    };
  }

  // The tooltip is an HTML card over the canvas rather than Chart.js's own
  // canvas box: it inherits the page's type, tokens and theme for free.
  // Built with textContent only -- episode names come from the data.
  function tooltip(view) {
    return function (context) {
      var tip = context.tooltip;
      var dp = tip && tip.dataPoints && tip.dataPoints[0];
      var rows = dp && tip.opacity !== 0 ? view.rows(dp.dataIndex) : null;
      if (!rows) {
        tipEl.classList.remove("is-on");
        return;
      }
      var i = dp.dataIndex;
      tipEl.textContent = "";
      var title = document.createElement("p");
      title.className = "season-chart__tip-title";
      title.textContent = view.title(i);
      tipEl.appendChild(title);
      var subText = view.sub(i);
      if (subText) {
        var sub = document.createElement("p");
        sub.className = "season-chart__tip-sub";
        sub.textContent = subText;
        tipEl.appendChild(sub);
      }
      var list = document.createElement("dl");
      rows.forEach(function (row) {
        var wrap = document.createElement("div");
        var dt = document.createElement("dt");
        var dd = document.createElement("dd");
        dt.textContent = row[0];
        dd.textContent = row[1];
        wrap.appendChild(dt);
        wrap.appendChild(dd);
        list.appendChild(wrap);
      });
      tipEl.appendChild(list);

      tipEl.classList.add("is-on");
      var w = tipEl.offsetWidth;
      var h = tipEl.offsetHeight;
      var x = tip.caretX + 16;
      if (x + w > stage.clientWidth) x = tip.caretX - 16 - w;
      x = Math.max(0, x);
      var y = Math.min(Math.max(0, tip.caretY - h / 2), Math.max(0, stage.clientHeight - h));
      tipEl.style.transform = "translate(" + Math.round(x) + "px, " + Math.round(y) + "px)";
    };
  }

  function selected() {
    var checked = picker && picker.querySelector("input[name=season]:checked");
    if (checked) return checked.value;
    var keys = Object.keys(data.episodes || {});
    return keys[0];
  }

  var chart = null;
  var frame = null;

  // The line draws in left to right once per view (~700 ms, ease-out); with
  // reduced motion it simply appears.
  function reveal() {
    if (frame) cancelAnimationFrame(frame);
    frame = null;
    if (reduce) {
      chart.$reveal = 1;
      chart.draw();
      return;
    }
    var start = null;
    var target = chart;
    target.$reveal = 0;
    function step(now) {
      if (chart !== target) return;
      if (start === null) start = now;
      var t = Math.min(1, (now - start) / 700);
      target.$reveal = easeOut(t);
      target.draw();
      frame = t < 1 ? requestAnimationFrame(step) : null;
    }
    frame = requestAnimationFrame(step);
  }

  function draw() {
    if (chart) {
      chart.destroy();
      chart = null;
    }
    tipEl.classList.remove("is-on");
    var value = selected();
    var view = value === "all" ? seasonView() : episodeView(value);
    figure.setAttribute("data-view", value === "all" ? "all" : "season");
    if (!view) {
      figure.hidden = true;
      return;
    }
    figure.hidden = false;
    figure.classList.toggle("has-extremes", !!view.extremes);
    readPalette();
    canvas.setAttribute("aria-label", view.aria);
    if (refValue) refValue.textContent = view.reference != null ? two(view.reference) : "";

    chart = new Chart(canvas, {
      type: "line",
      data: {
        labels: view.labels,
        datasets: [
          {
            data: view.values,
            borderColor: MARK,
            borderWidth: 2,
            borderJoinStyle: "round",
            borderCapStyle: "round",
            // The area: the theme's lime fading into the page, strongest
            // under the line and gone by the baseline.
            backgroundColor: function (ctx) {
              var area = ctx.chart.chartArea;
              if (!area) return withAlpha(MARK, 0.1);
              var g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
              g.addColorStop(0, withAlpha(MARK, DARK ? 0.28 : 0.3));
              g.addColorStop(0.55, withAlpha(MARK, DARK ? 0.09 : 0.1));
              g.addColorStop(1, withAlpha(MARK, 0));
              return g;
            },
            fill: "start",
            // Monotone: smooth, but never overshooting into a rating no
            // episode or season got.
            cubicInterpolationMode: "monotone",
            spanGaps: false,
            pointRadius: 0,
            pointHitRadius: 18,
            pointHoverRadius: 5,
            pointHoverBorderWidth: 2,
            pointHoverBackgroundColor: MARK,
            pointHoverBorderColor: PAPER,
          },
        ],
      },
      plugins: [overlay],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        layout: { padding: { top: 22, right: 4, bottom: 0, left: 0 } },
        plugins: {
          legend: { display: false },
          seasonOverlay: {
            reference: view.reference,
            extremes: view.extremes,
            format: view.format,
          },
          tooltip: { enabled: false, external: tooltip(view) },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            title: {
              display: true,
              text: view.axis,
              color: INK_FAINT,
              font: { family: FONT_BODY, size: 11 },
              padding: { top: 6 },
            },
            ticks: {
              color: INK_FAINT,
              font: { family: FONT_MONO, size: 11 },
              autoSkip: true,
              autoSkipPadding: 14,
              maxRotation: 0,
              padding: 6,
            },
          },
          y: {
            min: view.bounds.min,
            max: view.bounds.max,
            grid: { color: RULE, drawTicks: false, lineWidth: 1 },
            border: { display: false },
            ticks: {
              color: INK_FAINT,
              font: { family: FONT_MONO, size: 11 },
              stepSize: view.bounds.step,
              maxTicksLimit: 6,
              padding: 10,
              callback: function (v) { return one(v); },
            },
          },
        },
      },
    });
    reveal();
  }

  canvas.addEventListener("mouseleave", function () {
    tipEl.classList.remove("is-on");
  });

  if (picker) {
    picker.addEventListener("change", function (e) {
      if (e.target.name === "season") draw();
    });
  }
  // Chart.js bakes colours in at build time, so a theme switch rebuilds.
  document.addEventListener("themechange", draw);

  draw();
})();
