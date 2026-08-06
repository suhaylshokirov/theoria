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

  /* --- Theme toggle --------------------------------------------------------
     The saved theme is applied by an inline script in <head> so there's no
     flash of the wrong theme; this only wires up the button.

     Charts read their colours from CSS custom properties once, at build time,
     so a live theme switch has to rebuild them — hence the themechange event
     that analytics.js listens for. */

  var STORAGE_KEY = "theoria-theme";

  function currentTheme() {
    var explicit = document.documentElement.getAttribute("data-theme");
    if (explicit) return explicit;
    return window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function initThemeToggle() {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;

    function syncLabel() {
      var next = currentTheme() === "dark" ? "light" : "dark";
      btn.setAttribute("aria-label", "Switch to " + next + " theme");
    }

    syncLabel();

    btn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch (e) {
        // Private mode — the choice just won't persist across loads.
      }
      syncLabel();
      document.dispatchEvent(
        new CustomEvent("themechange", { detail: { theme: next } })
      );
    });

    // With no explicit choice saved, keep following the OS if it changes.
    if (window.matchMedia) {
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      var onChange = function () {
        if (!document.documentElement.getAttribute("data-theme")) {
          syncLabel();
          document.dispatchEvent(
            new CustomEvent("themechange", { detail: { theme: currentTheme() } })
          );
        }
      };
      if (mq.addEventListener) mq.addEventListener("change", onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
  }

  /* --- Mobile nav ------------------------------------------------------------
     Pure show/hide of the existing .nav-links panel; the collapse itself is
     CSS, gated on html.has-js (see base.html) so this only ever runs where
     the button is actually visible. Closes on Escape and on a route change
     via pageshow (back/forward cache can restore an open menu otherwise). */

  function initNavToggle() {
    var btn = document.getElementById("nav-toggle");
    var panel = document.getElementById("nav-links");
    if (!btn || !panel) return;

    function setOpen(open) {
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      panel.classList.toggle("is-open", open);
    }

    btn.addEventListener("click", function () {
      setOpen(btn.getAttribute("aria-expanded") !== "true");
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && btn.getAttribute("aria-expanded") === "true") {
        setOpen(false);
        btn.focus();
      }
    });

    window.addEventListener("pageshow", function () {
      setOpen(false);
    });
  }

  function init() {
    initMeters();
    initCounters();
    initThemeToggle();
    initNavToggle();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
