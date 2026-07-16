// Analytics booth instrumentation: themes the two Chart.js readouts to the
// projection-room palette, and turns each cue sheet's marked column into an
// exposure meter (a tungsten needle scaled to the column max). Progressive
// enhancement — without JS the tables are plain numbers and the canvases
// simply stay empty behind their tables.
(function () {
  "use strict";

  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var INK_LINE = "rgba(38, 43, 56, 0.9)";
  var LUMEN = "#f5e9d0";
  var LUMEN_DIM = "#948d7e";
  var AMBER = "#c67e15"; // data-mark step, validated against the panel surface
  var AMBER_WASH = "rgba(198, 126, 21, 0.12)";

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

  /* --- Charts ------------------------------------------------------------- */

  function baseOptions(yTickFormat) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: reduce ? false : { duration: 700 },
      plugins: {
        legend: { display: false }, // single series — the panel label names it
        tooltip: {
          backgroundColor: "#08090c",
          borderColor: AMBER,
          borderWidth: 1,
          titleColor: LUMEN,
          bodyColor: LUMEN,
          titleFont: { family: '"Space Grotesk", sans-serif', weight: "700" },
          bodyFont: { family: '"Space Grotesk", sans-serif' },
          padding: 10,
          displayColors: false,
          callbacks: {
            label: function (ctx) {
              return yTickFormat
                ? yTickFormat(ctx.parsed.y)
                : String(ctx.parsed.y);
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { color: INK_LINE },
          ticks: {
            color: LUMEN_DIM,
            font: { family: '"Space Grotesk", sans-serif', size: 11 },
          },
        },
        y: {
          grid: { color: INK_LINE },
          border: { display: false },
          ticks: {
            color: LUMEN_DIM,
            font: { family: '"Space Grotesk", sans-serif', size: 11 },
            callback: function (v) {
              return yTickFormat ? yTickFormat(v) : v;
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
              borderColor: AMBER,
              borderWidth: 2,
              pointRadius: 3,
              pointHoverRadius: 6,
              pointHitRadius: 14,
              pointBackgroundColor: AMBER,
              backgroundColor: AMBER_WASH,
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
              backgroundColor: AMBER,
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

  /* --- Exposure meters ------------------------------------------------------ */

  function parseCell(td) {
    var n = parseFloat(td.textContent.replace(/[,$\s]/g, ""));
    return isNaN(n) ? null : n;
  }

  function initMeters() {
    var tables = document.querySelectorAll("table");
    tables.forEach(function (table) {
      var cells = table.querySelectorAll("td[data-meter]");
      if (!cells.length) return;

      var max = 0;
      cells.forEach(function (td) {
        var v = parseCell(td);
        if (v !== null && v > max) max = v;
      });
      if (max <= 0) return;

      cells.forEach(function (td) {
        var v = parseCell(td);
        if (v === null) return;
        // Readout formatting: thousands separators, like the home counter.
        if (Number.isInteger(v)) td.textContent = v.toLocaleString("en-US");
        var fill = document.createElement("span");
        fill.className = "meter-fill";
        fill.setAttribute("aria-hidden", "true");
        td.insertBefore(fill, td.firstChild);
        var pct = ((v / max) * 100).toFixed(1) + "%";
        if (reduce) {
          fill.style.width = pct;
        } else {
          // Let the 0-width style land first so the needle sweeps to rest.
          requestAnimationFrame(function () {
            requestAnimationFrame(function () {
              fill.style.width = pct;
            });
          });
        }
      });
    });

    // Plain numeric cells (no meter) still get readable separators.
    document.querySelectorAll("td.num:not([data-meter])").forEach(function (td) {
      var v = parseCell(td);
      if (v !== null && Number.isInteger(v) && Math.abs(v) > 999) {
        td.textContent = v.toLocaleString("en-US");
      }
    });
  }

  function init() {
    initCharts();
    initMeters();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
