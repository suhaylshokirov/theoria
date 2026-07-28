// Theoria — site-wide behaviour. Loaded on every page from base.html.
//
// Two jobs, both progressive enhancement: with JS off, the meters are plain
// numbers and the counters already show their final value.
//
//   1. initMeters()   — draws a lime bar behind a numeric column, scaled to
//                       that column's max. The bar sits UNDER the printed
//                       number by design: lime on white is 1.5:1, well below
//                       the 3:1 mark floor, so the number is what makes the
//                       cell readable. Never ship the bar without it.
//   2. initCounters() — ticks [data-count] values up from zero on load.
//
// Both were previously stuck inside analytics.js; they live here so any page
// (genre lists, genre detail, the dashboard) can use the same components.
(function () {
  "use strict";

  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function parseCell(el) {
    var n = parseFloat(el.textContent.replace(/[,$\s★]/g, ""));
    return isNaN(n) ? null : n;
  }

  /* --- Meter bars ---------------------------------------------------------- */

  function initMeters() {
    document.querySelectorAll("table").forEach(function (table) {
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
        if (Number.isInteger(v)) td.textContent = v.toLocaleString("en-US");

        // A track wrapper reserves the strip the number sits in, so the
        // fill's percentage is relative to the bar's own space rather than
        // the whole cell.
        var track = document.createElement("span");
        track.className = "meter-track";
        track.setAttribute("aria-hidden", "true");
        var fill = document.createElement("span");
        fill.className = "meter-fill";
        track.appendChild(fill);
        td.insertBefore(track, td.firstChild);

        var pct = ((v / max) * 100).toFixed(1) + "%";
        if (reduce) {
          fill.style.width = pct;
        } else {
          // Let the 0-width style paint first so the bar animates outward.
          requestAnimationFrame(function () {
            requestAnimationFrame(function () {
              fill.style.width = pct;
            });
          });
        }
      });
    });

    // Plain numeric cells still get readable separators.
    document.querySelectorAll("td.num:not([data-meter])").forEach(function (td) {
      var v = parseCell(td);
      if (v !== null && Number.isInteger(v) && Math.abs(v) > 999) {
        td.textContent = v.toLocaleString("en-US");
      }
    });
  }

  /* --- Count-up ------------------------------------------------------------- */

  function format(value, decimals) {
    if (decimals > 0) return value.toFixed(decimals);
    return Math.round(value).toLocaleString("en-US");
  }

  function runCounter(el) {
    var target = parseFloat(el.getAttribute("data-count"));
    if (isNaN(target)) return;
    var decimals = parseInt(el.getAttribute("data-decimals") || "0", 10);

    if (reduce) {
      el.textContent = format(target, decimals);
      return;
    }

    var duration = 1100;
    var start = null;

    function step(now) {
      if (start === null) start = now;
      var t = Math.min((now - start) / duration, 1);
      var eased = 1 - Math.pow(1 - t, 3); // ease-out cubic, settling to rest
      el.textContent = format(target * eased, decimals);
      if (t < 1) requestAnimationFrame(step);
      else el.textContent = format(target, decimals);
    }

    requestAnimationFrame(step);
  }

  function initCounters() {
    document.querySelectorAll("[data-count]").forEach(runCounter);
  }

  function init() {
    initMeters();
    initCounters();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
