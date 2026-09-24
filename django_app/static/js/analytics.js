// Analytics charts. Three readouts, one series each (Task 90 added the
// season-rating line) — so per the chart rules they take a single hue, not a
// categorical palette, and none needs a legend (the panel heading names the
// series).
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

  // Palette values are re-read on every (re)build rather than cached once, so
  // a live theme switch picks up the new tokens. Declared here and filled by
  // readPalette() below.
  var MARK, WASH, RULE, INK, INK_FAINT, PAPER;

  function readPalette() {
    MARK = cssVar("--lime-mark", "#65a30d");
    // The area fill under the line. Read from --chart-wash so each mode can
    // set its own weight: a tint that reads as delicate on white is a heavy
    // slab on ink, because the eye judges it against its surface.
    WASH = cssVar("--chart-wash", "rgba(163, 230, 53, 0.18)");
    RULE = cssVar("--rule", "#e3e2dd");
    INK = cssVar("--ink", "#0b0b0b");
    INK_FAINT = cssVar("--ink-faint", "#78716c");
    PAPER = cssVar("--paper", "#ffffff");
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

  // Uzbek formats as Russian, same as theoria.js's numberLocale(): browser
  // Intl data for "uz" is unreliable, and Django's Uzbek separators are
  // Russian's.
  var LANG = (function () {
    var lang = document.documentElement.lang || "en";
    return lang === "uz" ? "ru" : lang;
  })();

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

  // Live chart instances, so a theme switch can destroy and rebuild them.
  var charts = [];

  function initCharts() {
    if (typeof Chart === "undefined") return;

    readPalette();
    while (charts.length) charts.pop().destroy();

    var decadeLabels = readJSON("decade-labels");
    var decadeRatings = readJSON("decade-avg-ratings");
    var decadeCanvas = document.getElementById("decade-chart");
    if (decadeCanvas && decadeLabels && decadeLabels.length) {
      charts.push(new Chart(decadeCanvas, {
        type: "line",
        data: {
          labels: decadeLabels,
          datasets: [
            {
              label: t("Avg rating"),
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
      }));
    }

    var seasonLabels = readJSON("season-labels");
    var seasonRatings = readJSON("season-avg-ratings");
    var seasonCanvas = document.getElementById("season-chart");
    if (seasonCanvas && seasonLabels && seasonLabels.length) {
      charts.push(new Chart(seasonCanvas, {
        type: "line",
        data: {
          labels: seasonLabels,
          datasets: [
            {
              label: t("Avg rating"),
              data: seasonRatings,
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
      }));
    }

    var genreLabels = readJSON("genre-labels");
    var genreRevenue = readJSON("genre-revenue");
    var genreCanvas = document.getElementById("revenue-chart");
    if (genreCanvas && genreLabels && genreLabels.length) {
      charts.push(new Chart(genreCanvas, {
        type: "bar",
        data: {
          labels: genreLabels,
          datasets: [
            {
              label: t("Total revenue"),
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
      }));
    }
  }

  // Rebuild on theme change: Chart.js bakes colours into the instance, so
  // re-reading the tokens means re-creating the charts.
  document.addEventListener("themechange", initCharts);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCharts);
  } else {
    initCharts();
  }
})();
