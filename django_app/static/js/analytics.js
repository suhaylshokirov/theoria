// Analytics dashboard (Task 133).
//
// Three jobs, all progressive enhancement over server-rendered content (with
// JS off the page still reads: findings, tables, KPI figures):
//   1. charts   - built the first time their canvas scrolls into view, and
//                 rebuilt on a theme change (Chart.js bakes colours in);
//   2. toggle   - the Movies/Shows switch on the rating histogram;
//   3. chapter nav - the sticky row highlights the chapter being read.
// (The headline figures' count-up is theoria.js's shared one, not repeated here.)
//
// The palette is READ FROM CSS rather than hardcoded here, so the design
// tokens in theoria.css stay the single source of truth. Lime (--lime-mark)
// is the measurement mark: only the measured series takes it. Everything else
// is ink and rule, so a chart with several series (return bands) reads as one
// ordered scale from grey to lime instead of a rainbow.
(function () {
  "use strict";

  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // getPropertyValue returns "" for an unset property (no throw) and keeps
  // the leading space from `--x: #fff`, so trim + fallback are both needed.
  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    v = v ? v.trim() : "";
    return v || fallback;
  }

  // Re-read on every (re)build so a live theme switch picks up the new tokens.
  var MARK, WASH, RULE, RULE_STRONG, INK, INK_FAINT, PAPER, SHEET;

  function readPalette() {
    MARK = cssVar("--lime-mark", "#65a30d");
    WASH = cssVar("--chart-wash", "rgba(163, 230, 53, 0.18)");
    RULE = cssVar("--rule", "#e3e2dd");
    RULE_STRONG = cssVar("--rule-strong", "#c9c7c0");
    INK = cssVar("--ink", "#0b0b0b");
    INK_FAINT = cssVar("--ink-faint", "#78716c");
    PAPER = cssVar("--paper", "#ffffff");
    SHEET = cssVar("--sheet-2", "#efeeea");
  }

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

  // Words come from base.html's #js-strings block (see
  // core.context_processors.js_strings); the English key is the fallback.
  var STRINGS = readJSON("js-strings") || {};
  function t(key) {
    return Object.prototype.hasOwnProperty.call(STRINGS, key) ? STRINGS[key] : key;
  }

  // "Movies: {count}" -> "Movies: 41". Placeholders, not plural forms: a count
  // in a tooltip is a label, and a label needs no grammar in ru/uz.
  function fill(key, values) {
    var s = t(key);
    Object.keys(values).forEach(function (name) {
      s = s.replace("{" + name + "}", values[name]);
    });
    return s;
  }

  // Uzbek formats as Russian, same as theoria.js's numberLocale(): browser
  // Intl data for "uz" is unreliable, and Django's Uzbek separators are
  // Russian's.
  var LANG = (function () {
    var lang = document.documentElement.lang || "en";
    return lang === "uz" ? "ru" : lang;
  })();

  // Ratings, shares and multiples keep a dot in every language, like the server's
  // floatformat "u" -- so a chart label and the table beside it never disagree.
  // Only money (compact) follows the language's own decimal mark.
  function dot(n, digits) {
    return n.toFixed(digits);
  }

  function fixed(n, digits) {
    return n.toLocaleString(LANG, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  // Magnitude suffixes are translated ("B" is "млрд" in Russian) and the
  // digits follow the language's own decimal mark. The "$" that callers
  // prepend is not localized: TMDB reports money in USD, so it is a unit of
  // the data, not a formatting preference.
  function compact(value) {
    if (Math.abs(value) >= 1e9) return fixed(value / 1e9, 1) + t("compact B");
    if (Math.abs(value) >= 1e6) return fixed(value / 1e6, 1) + t("compact M");
    if (Math.abs(value) >= 1e3) return fixed(value / 1e3, 0) + t("compact K");
    return value.toLocaleString(LANG);
  }

  // A real minus sign, and a plus for gains, so a change reads at a glance.
  function signed(v, digits) {
    var s = dot(Math.abs(v), digits);
    var eps = 0.5 / Math.pow(10, digits);
    if (v >= eps) return "+" + s;
    if (v <= -eps) return "−" + s;
    return s;
  }

  function withAlpha(color, alpha) {
    var m = /^#([0-9a-f]{6})$/i.exec(color);
    if (!m) return color;
    var n = parseInt(m[1], 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alpha + ")";
  }

  function decadeLabel(decade) {
    return fill("{decade}s", { decade: decade });
  }

  // --- Shared chart chrome ---------------------------------------------------

  function tooltipBase() {
    return {
      // Sheet + ink rather than ink + paper: an inverted tooltip reads
      // correctly on white but becomes a glaring pale box on dark.
      backgroundColor: SHEET,
      titleColor: INK,
      bodyColor: INK,
      borderColor: RULE,
      borderWidth: 1,
      titleFont: { family: FONT_BODY, weight: "600", size: 12 },
      bodyFont: { family: FONT_MONO, size: 12 },
      padding: 10,
      displayColors: false,
    };
  }

  function tickFont() {
    return { color: INK_FAINT, font: { family: FONT_MONO, size: 11 } };
  }

  function axisTitle(text) {
    return {
      display: true,
      text: text,
      color: INK_FAINT,
      font: { family: FONT_MONO, size: 11 },
    };
  }

  function xAxis(extra) {
    var axis = {
      grid: { display: false },
      border: { color: RULE },
      ticks: tickFont(),
    };
    return Object.assign(axis, extra || {});
  }

  function yAxis(extra) {
    var axis = {
      grid: { color: RULE },
      border: { display: false },
      ticks: tickFont(),
    };
    return Object.assign(axis, extra || {});
  }

  function baseOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: reduce ? false : { duration: 600 },
      plugins: { legend: { display: false } }, // single series: the heading names it
    };
  }

  // Straight segments (tension 0): a smoothed curve overshoots between points
  // and invents peaks the data never reached.
  function lineDataset(label, data) {
    return {
      label: label,
      data: data,
      borderColor: MARK,
      borderWidth: 2,
      pointRadius: 4,
      pointHoverRadius: 7,
      pointHitRadius: 16,
      pointBackgroundColor: MARK,
      pointBorderColor: PAPER,
      pointBorderWidth: 2,
      backgroundColor: WASH,
      fill: true,
      tension: 0,
    };
  }

  // --- Chart builders --------------------------------------------------------
  // Each takes the canvas and returns a Chart (or null when its data is
  // missing). A builder reads the palette variables, so it must only run after
  // readPalette().

  var ratingKind = "movie";

  // A dashed vertical rule at the median, drawn after the bars. The bars are
  // categories (half-point bands), so a median of 7.3 is placed by converting
  // it to a fractional category index: a band's bar is centred on its middle.
  var medianLine = {
    id: "medianLine",
    afterDatasetsDraw: function (chart, args, opts) {
      if (!opts || opts.value == null || opts.first == null) return;
      var x = chart.scales.x;
      var area = chart.chartArea;
      var index = (opts.value - opts.first) / 0.5 - 0.5;
      var px = x.getPixelForValue(index);
      var ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = INK;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(px, area.top);
      ctx.lineTo(px, area.bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = INK;
      ctx.font = "600 11px " + FONT_MONO;
      var label = t("Median") + " " + dot(opts.value, 1);
      var width = ctx.measureText(label).width;
      // Keep the label inside the plot: flip it to the left of the rule near
      // the right edge.
      var left = px + 6 + width > area.right ? px - 6 - width : px + 6;
      ctx.fillText(label, left, area.top + 12);
      ctx.restore();
    },
  };

  function buildRating(canvas) {
    var data = readJSON("rating-data");
    if (!data || !data.bands || !data.bands.length) return null;
    var medians = {
      movie: readJSON("rating-median-movie"),
      series: readJSON("rating-median-series"),
    };
    var options = baseOptions();
    options.plugins.tooltip = Object.assign(tooltipBase(), {
      callbacks: {
        title: function (items) {
          var band = data.bands[items[0].dataIndex];
          return dot(band, 1) + "–" + dot(band + 0.4, 1);
        },
        label: function (ctx) {
          return fill(ratingKind === "movie" ? "Movies: {count}" : "Shows: {count}", {
            count: ctx.parsed.y.toLocaleString(LANG),
          });
        },
      },
    });
    options.plugins.medianLine = { value: medians[ratingKind], first: data.bands[0] };
    options.scales = {
      x: xAxis({ title: axisTitle(t("IMDb rating")) }),
      y: yAxis({
        beginAtZero: true,
        ticks: Object.assign(tickFont(), { precision: 0 }),
      }),
    };
    return new Chart(canvas, {
      type: "bar",
      data: {
        labels: data.bands.map(function (b) { return dot(b, 1); }),
        datasets: [{
          label: t(ratingKind === "movie" ? "Movies" : "Shows"),
          data: data[ratingKind],
          backgroundColor: MARK,
          borderRadius: { topLeft: 3, topRight: 3 },
          borderSkipped: "bottom",
          categoryPercentage: 0.94,
          barPercentage: 0.92,
        }],
      },
      options: options,
      plugins: [medianLine],
    });
  }

  function buildScatter(canvas) {
    var rows = readJSON("scatter-data");
    if (!rows || !rows.length) return null;
    var urlTemplate = canvas.getAttribute("data-movie-url") || "";
    var points = rows.map(function (r) {
      return { x: r[0], y: r[1], title: r[2], year: r[3], slug: r[4] };
    });
    var minX = Infinity, maxX = 0;
    points.forEach(function (p) {
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
    });

    function isPowerOfTen(v) {
      return v > 0 && Math.abs(Math.log10(v) - Math.round(Math.log10(v))) < 1e-9;
    }

    // Log axes label and rule only the powers of ten ($1M, $10M, $100M, $1B
    // ...); the in-between ticks Chart.js generates would crowd the axis. The
    // trailing ".0" is dropped ("$1M", not "$1.0M"): an axis label is a round
    // number, not a measurement.
    function moneyTicks(v) {
      return isPowerOfTen(v) ? "$" + compact(v).replace(/[.,]0(?=\D|$)/, "") : "";
    }

    function powerGrid(ctx) {
      return ctx.tick && isPowerOfTen(ctx.tick.value) ? RULE : "transparent";
    }

    var options = baseOptions();
    options.interaction = { mode: "nearest", intersect: true };
    options.plugins.legend = {
      display: true,
      position: "bottom",
      labels: {
        color: INK_FAINT,
        font: { family: FONT_MONO, size: 11 },
        boxWidth: 28,
        boxHeight: 1,
        // The points need no legend entry; only the two reference lines do.
        filter: function (item) { return item.datasetIndex !== 0; },
      },
    };
    options.plugins.tooltip = Object.assign(tooltipBase(), {
      filter: function (item) { return item.datasetIndex === 0; },
      callbacks: {
        title: function (items) {
          var p = items[0].raw;
          return p.title + " (" + p.year + ")";
        },
        label: function (ctx) {
          var p = ctx.raw;
          return "$" + compact(p.x) + " → $" + compact(p.y) + " · " + dot(p.y / p.x, 1) + "×";
        },
      },
    });
    options.onClick = function (evt, elements, chart) {
      if (!elements.length || elements[0].datasetIndex !== 0) return;
      var p = chart.data.datasets[0].data[elements[0].index];
      if (urlTemplate && p && p.slug) {
        window.location.href = urlTemplate.replace("__slug__", encodeURIComponent(p.slug));
      }
    };
    options.onHover = function (evt, elements) {
      var target = evt.native && evt.native.target;
      if (target) {
        target.style.cursor = elements.length && elements[0].datasetIndex === 0 ? "pointer" : "default";
      }
    };
    options.scales = {
      x: xAxis({
        type: "logarithmic",
        title: axisTitle(t("Budget")),
        ticks: Object.assign(tickFont(), { callback: moneyTicks, maxRotation: 0 }),
        grid: { color: powerGrid },
      }),
      y: yAxis({
        type: "logarithmic",
        title: axisTitle(t("Revenue")),
        ticks: Object.assign(tickFont(), { callback: moneyTicks }),
        grid: { color: powerGrid },
      }),
    };
    return new Chart(canvas, {
      type: "scatter",
      data: {
        datasets: [
          {
            type: "scatter",
            label: t("Movies"),
            data: points,
            backgroundColor: withAlpha(MARK, 0.55),
            borderColor: withAlpha(MARK, 0.9),
            borderWidth: 1,
            pointRadius: 3.5,
            pointHoverRadius: 6,
            pointHitRadius: 6,
            order: 1,
          },
          {
            type: "line",
            label: t("Break-even"),
            data: [{ x: minX, y: minX }, { x: maxX, y: maxX }],
            borderColor: INK,
            borderWidth: 1,
            borderDash: [5, 4],
            pointRadius: 0,
            pointHitRadius: 0,
            fill: false,
            order: 2,
          },
          {
            type: "line",
            label: t("5× budget"),
            data: [{ x: minX, y: 5 * minX }, { x: maxX, y: 5 * maxX }],
            borderColor: INK_FAINT,
            borderWidth: 1,
            borderDash: [2, 4],
            pointRadius: 0,
            pointHitRadius: 0,
            fill: false,
            order: 3,
          },
        ],
      },
      options: options,
    });
  }

  function buildBands(canvas) {
    var d = readJSON("bands-data");
    if (!d || !d.labels || !d.labels.length) return null;
    // One ordered scale, grey to lime: how much of the budget came back.
    var series = [
      { key: "under_1x", label: t("Under 1×"), color: INK_FAINT },
      { key: "from_1x_to_2x", label: "1–2×", color: withAlpha(INK_FAINT, 0.5) },
      { key: "from_2x_to_5x", label: "2–5×", color: withAlpha(MARK, 0.45) },
      { key: "over_5x", label: t("5× or more"), color: MARK },
    ];
    var options = baseOptions();
    options.indexAxis = "y";
    options.plugins.legend = {
      display: true,
      position: "top",
      align: "start",
      labels: {
        color: INK_FAINT,
        font: { family: FONT_MONO, size: 11 },
        boxWidth: 12,
        boxHeight: 12,
      },
    };
    options.plugins.tooltip = Object.assign(tooltipBase(), {
      callbacks: {
        title: function (items) {
          var i = items[0].dataIndex;
          return d.labels[i] + " · " + t("Median") + " " + dot(d.median[i], 1) + "×";
        },
        label: function (ctx) {
          var i = ctx.dataIndex;
          var count = Math.round(ctx.parsed.x * d.count[i] / 100);
          return ctx.dataset.label + ": " + fixed(ctx.parsed.x, 0) + "% (" + count.toLocaleString(LANG) + ")";
        },
      },
    });
    options.scales = {
      x: xAxis({
        stacked: true,
        min: 0,
        max: 100,
        grid: { color: RULE },
        ticks: Object.assign(tickFont(), {
          stepSize: 25,
          callback: function (v) { return v + "%"; },
        }),
      }),
      y: yAxis({
        stacked: true,
        grid: { display: false },
        border: { color: RULE },
        ticks: Object.assign(tickFont(), { autoSkip: false }),
      }),
    };
    return new Chart(canvas, {
      type: "bar",
      data: {
        labels: d.labels,
        datasets: series.map(function (s) {
          return {
            label: s.label,
            data: d[s.key],
            backgroundColor: s.color,
            borderWidth: 0,
            barPercentage: 0.78,
            categoryPercentage: 0.9,
          };
        }),
      },
      options: options,
    });
  }

  function buildRuntime(canvas) {
    var d = readJSON("runtime-data");
    if (!d || !d.labels || !d.labels.length) return null;
    var options = baseOptions();
    options.plugins.tooltip = Object.assign(tooltipBase(), {
      callbacks: {
        title: function (items) { return decadeLabel(d.labels[items[0].dataIndex]); },
        label: function (ctx) {
          return [
            fill("{minutes} min", { minutes: fixed(ctx.parsed.y, 0) }),
            fill("Movies: {count}", { count: d.counts[ctx.dataIndex].toLocaleString(LANG) }),
          ];
        },
      },
    });
    options.scales = {
      x: xAxis(),
      y: yAxis({
        ticks: Object.assign(tickFont(), {
          callback: function (v) { return fixed(v, 0); },
        }),
      }),
    };
    return new Chart(canvas, {
      type: "line",
      data: {
        labels: d.labels.map(decadeLabel),
        datasets: [lineDataset(t("Median minutes"), d.values)],
      },
      options: options,
    });
  }

  function buildNonEnglish(canvas) {
    var d = readJSON("nonenglish-data");
    if (!d || !d.labels || !d.labels.length) return null;
    var options = baseOptions();
    options.plugins.tooltip = Object.assign(tooltipBase(), {
      callbacks: {
        title: function (items) { return decadeLabel(d.labels[items[0].dataIndex]); },
        label: function (ctx) {
          return [
            dot(ctx.parsed.y, 1) + "%",
            fill("Movies: {count}", { count: d.counts[ctx.dataIndex].toLocaleString(LANG) }),
          ];
        },
      },
    });
    options.scales = {
      x: xAxis(),
      y: yAxis({
        beginAtZero: true,
        ticks: Object.assign(tickFont(), {
          callback: function (v) { return fixed(v, 0) + "%"; },
        }),
      }),
    };
    return new Chart(canvas, {
      type: "bar",
      data: {
        labels: d.labels.map(decadeLabel),
        datasets: [{
          label: t("Not in English"),
          data: d.values,
          backgroundColor: MARK,
          borderRadius: { topLeft: 4, topRight: 4 },
          borderSkipped: "bottom",
          maxBarThickness: 44,
          categoryPercentage: 0.72,
        }],
      },
      options: options,
    });
  }

  function buildSeason(canvas) {
    var d = readJSON("season-data");
    if (!d || !d.labels || !d.labels.length) return null;
    var options = baseOptions();
    options.plugins.tooltip = Object.assign(tooltipBase(), {
      callbacks: {
        title: function (items) {
          return fill("Season {n}", { n: d.labels[items[0].dataIndex] });
        },
        label: function (ctx) {
          return [
            signed(ctx.parsed.y, 2) + " " + t("vs season 1"),
            fill("Shows: {count}", { count: d.shows[ctx.dataIndex].toLocaleString(LANG) }),
          ];
        },
      },
    });
    options.scales = {
      x: xAxis({ title: axisTitle(t("Season")) }),
      y: yAxis({
        // The zero line IS the first season; draw it as the baseline.
        grid: {
          color: function (ctx) { return ctx.tick && ctx.tick.value === 0 ? INK_FAINT : RULE; },
        },
        ticks: Object.assign(tickFont(), {
          callback: function (v) { return signed(v, 1); },
        }),
      }),
    };
    return new Chart(canvas, {
      type: "line",
      data: {
        labels: d.labels,
        datasets: [lineDataset(t("vs season 1"), d.change)],
      },
      options: options,
    });
  }

  var BUILDERS = {
    rating: buildRating,
    scatter: buildScatter,
    bands: buildBands,
    runtime: buildRuntime,
    nonenglish: buildNonEnglish,
    season: buildSeason,
  };

  // --- Chart lifecycle -------------------------------------------------------
  // `entries` keeps every canvas on the page; `chart` stays null until the
  // canvas has been near the viewport, so a page of seven charts doesn't spend
  // its first paint drawing the ones below the fold.

  var entries = [];

  function build(entry) {
    if (typeof Chart === "undefined") return;
    if (entry.chart) {
      entry.chart.destroy();
      entry.chart = null;
    }
    var builder = BUILDERS[entry.name];
    if (builder) entry.chart = builder(entry.canvas);
  }

  function initCharts() {
    if (typeof Chart === "undefined") return;
    readPalette();
    var canvases = document.querySelectorAll("canvas[data-chart]");
    var observer = null;
    if ("IntersectionObserver" in window) {
      observer = new IntersectionObserver(function (items) {
        items.forEach(function (item) {
          if (!item.isIntersecting) return;
          observer.unobserve(item.target);
          for (var i = 0; i < entries.length; i++) {
            if (entries[i].canvas === item.target) build(entries[i]);
          }
        });
      }, { rootMargin: "200px 0px" });
    }
    canvases.forEach(function (canvas) {
      var entry = { canvas: canvas, name: canvas.getAttribute("data-chart"), chart: null };
      entries.push(entry);
      if (observer) observer.observe(canvas);
      else build(entry);
    });
  }

  // Chart.js bakes colours into the instance, so a live theme switch has to
  // re-read the tokens and rebuild whatever is already on screen.
  document.addEventListener("themechange", function () {
    readPalette();
    entries.forEach(function (entry) {
      if (entry.chart) build(entry);
    });
  });

  // --- Movies / Shows switch -------------------------------------------------

  function initRatingToggle() {
    var buttons = document.querySelectorAll("[data-rating-kind]");
    if (!buttons.length) return;
    var data = readJSON("rating-data");
    var medians = {
      movie: readJSON("rating-median-movie"),
      series: readJSON("rating-median-series"),
    };
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        ratingKind = button.getAttribute("data-rating-kind");
        buttons.forEach(function (b) {
          b.setAttribute("aria-pressed", b === button ? "true" : "false");
        });
        entries.forEach(function (entry) {
          if (entry.name !== "rating" || !entry.chart || !data) return;
          var chart = entry.chart;
          chart.data.datasets[0].data = data[ratingKind];
          chart.data.datasets[0].label = t(ratingKind === "movie" ? "Movies" : "Shows");
          chart.options.plugins.medianLine.value = medians[ratingKind];
          chart.update();
        });
      });
    });
  }

  // --- Chapter nav: highlight the chapter being read -------------------------

  function initChapterNav() {
    var nav = document.querySelector(".chapters");
    if (!nav) return;
    var links = Array.prototype.slice.call(nav.querySelectorAll('a[href^="#"]'));
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });

    function setCurrent(id) {
      var current = byId[id];
      links.forEach(function (a) {
        if (a === current) a.setAttribute("aria-current", "true");
        else a.removeAttribute("aria-current");
      });
      // Keep the active link in view when the row scrolls sideways (phones).
      var list = current && current.closest("ul");
      if (list && list.scrollWidth > list.clientWidth) {
        var left = current.offsetLeft - (list.clientWidth - current.offsetWidth) / 2;
        list.scrollTo({ left: left, behavior: reduce ? "auto" : "smooth" });
      }
    }

    links.forEach(function (a) {
      a.addEventListener("click", function () {
        setCurrent(a.getAttribute("href").slice(1));
      });
    });

    if (!("IntersectionObserver" in window)) return;
    // A thin band a quarter of the way down the viewport: the chapter under it
    // is the one being read.
    var observer = new IntersectionObserver(function (items) {
      items.forEach(function (item) {
        if (item.isIntersecting) setCurrent(item.target.id);
      });
    }, { rootMargin: "-25% 0px -65% 0px" });
    links.forEach(function (a) {
      var section = document.getElementById(a.getAttribute("href").slice(1));
      if (section) observer.observe(section);
    });
  }

  function init() {
    initCharts();
    initRatingToggle();
    initChapterNav();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
