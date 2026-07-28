// Analytics charts. Two readouts, one series each — so per the chart rules
// they take a single hue, not a categorical palette, and neither needs a
// legend (the panel heading names the series).
//
// The palette is READ FROM CSS rather than hardcoded here, so the design
// tokens in theoria.css stay the single source of truth. Previously this
// file carried its own five hexes and they had to be changed in two places.
(function () {
  "use strict";

  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // getPropertyValue returns "" for an unset property (no throw) and keeps
  // the leading space from `--x: #fff`, so trim + fallback are both needed.
  // Safe to read at this point: the stylesheet is render-blocking in <head>,
  // so custom properties are resolved before any script runs.
  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    v = v ? v.trim() : "";
    return v || fallback;
  }

  var MARK = cssVar("--lime-mark", "#65a30d");
  // The area fill under the line. Read from --chart-wash so each mode can set
  // its own weight: a tint that reads as delicate on white is a heavy slab on
  // ink, because the eye judges it against the surface it sits on.
  var WASH = cssVar("--chart-wash", "rgba(163, 230, 53, 0.18)");
  var RULE = cssVar("--rule", "#e3e2dd");
  var INK = cssVar("--ink", "#0b0b0b");
  var INK_FAINT = cssVar("--ink-faint", "#78716c");
  var PAPER = cssVar("--paper", "#ffffff");

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

  function compact(value) {
    if (Math.abs(value) >= 1e9) return (value / 1e9).toFixed(1) + "B";
    if (Math.abs(value) >= 1e6) return (value / 1e6).toFixed(1) + "M";
    if (Math.abs(value) >= 1e3) return (value / 1e3).toFixed(0) + "K";
    return String(value);
  }

  function baseOptions(formatValue) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: reduce ? false : { duration: 600 },
      plugins: {
        legend: { display: false }, // single series — the heading names it
        tooltip: {
          // Sheet + ink rather than ink + paper: an inverted tooltip reads
          // correctly on white but becomes a glaring pale box on dark.
          backgroundColor: cssVar("--sheet-2", "#efeeea"),
          titleColor: INK,
          bodyColor: INK,
          borderColor: RULE,
          borderWidth: 1,
          titleFont: { family: FONT_BODY, weight: "600", size: 12 },
          bodyFont: { family: FONT_MONO, size: 12 },
          padding: 10,
          displayColors: false,
          callbacks: {
            label: function (ctx) {
              return formatValue
                ? formatValue(ctx.parsed.y)
                : String(ctx.parsed.y);
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { color: RULE },
          ticks: {
            color: INK_FAINT,
            font: { family: FONT_MONO, size: 11 },
          },
        },
        y: {
          grid: { color: RULE },
          border: { display: false },
          ticks: {
            color: INK_FAINT,
            font: { family: FONT_MONO, size: 11 },
            callback: function (v) {
              return formatValue ? formatValue(v) : v;
            },
          },
        },
      },
    };
  }

  function initCharts() {
    if (typeof Chart === "undefined") return;

    var decadeLabels = readJSON("decade-labels");
    var decadeRatings = readJSON("decade-avg-ratings");
    var decadeCanvas = document.getElementById("decade-chart");
    if (decadeCanvas && decadeLabels && decadeLabels.length) {
      new Chart(decadeCanvas, {
        type: "line",
        data: {
          labels: decadeLabels,
          datasets: [
            {
              label: "Avg rating",
              data: decadeRatings,
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
              tension: 0.25,
            },
          ],
        },
        options: baseOptions(function (v) {
          return "★ " + Number(v).toFixed(2);
        }),
      });
    }

    var genreLabels = readJSON("genre-labels");
    var genreRevenue = readJSON("genre-revenue");
    var genreCanvas = document.getElementById("revenue-chart");
    if (genreCanvas && genreLabels && genreLabels.length) {
      new Chart(genreCanvas, {
        type: "bar",
        data: {
          labels: genreLabels,
          datasets: [
            {
              label: "Total revenue",
              data: genreRevenue,
              backgroundColor: MARK,
              borderRadius: { topLeft: 4, topRight: 4 },
              borderSkipped: "bottom",
              maxBarThickness: 34,
              categoryPercentage: 0.72,
            },
          ],
        },
        options: baseOptions(function (v) {
          return "$" + compact(Number(v));
        }),
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCharts);
  } else {
    initCharts();
  }
})();
