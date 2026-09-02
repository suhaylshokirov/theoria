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

    // Plain numeric cells still get readable separators — except cells
    // marked data-no-comma, e.g. years, where "2,026" is not a real
    // thousands quantity and just reads as wrong.
    document
      .querySelectorAll("td.num:not([data-meter]):not([data-no-comma])")
      .forEach(function (td) {
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

  /* --- Client-side paging ----------------------------------------------------
     Used by the movie page for cast and crew. Everything is already in the
     document; this only shows a window of it, so "Next" is a repaint rather
     than a round-trip.

     Why here and not on the server: one film's credits max out around 1,200
     rows, which is a payload a browser can hold. The /movies/ and /people/
     list pages stay server-paged because their result sets are 1,215 and
     122,685 rows — see _pager.html.

     Progressive enhancement: the nav ships with `hidden` and is only revealed
     when there's more than one page of items, so with JS off the reader gets
     the whole list and no dead buttons.

     Contract on the container:
       [data-paged]      the root
       data-page-size    items per page (default 10)
       data-page-items   CSS selector for the items themselves
       [data-page-group] optional wrapper (a crew department) that hides
                         itself when none of its items are on this page
       [data-page-nav]   the pager, holding [data-page-prev/next/state] */

  function initPagedSection(root) {
    var size = parseInt(root.getAttribute("data-page-size") || "10", 10);
    var selector = root.getAttribute("data-page-items") || "[data-page-item]";
    var nav = root.querySelector("[data-page-nav]");
    if (!nav || size < 1) return;

    var items = Array.prototype.slice.call(root.querySelectorAll(selector));
    var pages = Math.ceil(items.length / size);
    if (pages <= 1) return; // Nav stays hidden; nothing to page.

    var groups = Array.prototype.slice.call(
      root.querySelectorAll("[data-page-group]")
    );
    var prev = nav.querySelector("[data-page-prev]");
    var next = nav.querySelector("[data-page-next]");
    var state = nav.querySelector("[data-page-state]");
    var current = 1;

    function render() {
      var start = (current - 1) * size;
      var end = start + size;
      items.forEach(function (el, i) {
        el.hidden = i < start || i >= end;
      });
      // A department whose people are all on another page shouldn't leave its
      // heading and an empty ruled list behind.
      groups.forEach(function (group) {
        group.hidden = !group.querySelector(selector + ":not([hidden])");
      });
      state.textContent = current + " / " + pages;
      prev.disabled = current === 1;
      next.disabled = current === pages;
    }

    function go(delta) {
      var target = Math.min(Math.max(current + delta, 1), pages);
      if (target === current) return;
      current = target;
      render();
      // Keep the reader at the top of the section they're paging rather than
      // wherever the shorter/taller new page happens to leave the scroll.
      var section = root.closest("section");
      if (section) {
        section.scrollIntoView({
          behavior: reduce ? "auto" : "smooth",
          block: "start",
        });
      }
    }

    prev.addEventListener("click", function () {
      go(-1);
    });
    next.addEventListener("click", function () {
      go(1);
    });

    nav.hidden = false;
    render();
  }

  function initPagedSections() {
    document.querySelectorAll("[data-paged]").forEach(initPagedSection);
  }

  /* --- Live filtering (People index) -----------------------------------------
     Progressive enhancement over a plain GET form: as any field in it
     changes, re-fetch just the results and swap them in, so tweaking a
     filter shows the new results without an Apply click or a full page
     reload. Falls straight through to a normal submit if fetch/AbortController
     aren't available, or if the request itself fails.

     The server tells the two paths apart by the X-Requested-With header this
     sets (see _is_ajax() in views.py) — a plain browser submit never sends
     it, so a no-JS visitor gets the exact same full page either way.

     Contract on the form:
       [data-live-filter]   the form itself; a plain GET form underneath.
       data-live-targets    comma-separated CSS selectors, each an id also
                             present in the AJAX response, swapped by
                             innerHTML. Two rather than one because the
                             People page's scope nav and result count sit
                             inside .toolbar while the grid and pager sit
                             below it — see movies/_person_results.html. */

  function initLiveFilter() {
    var form = document.querySelector("[data-live-filter]");
    if (!form || !window.fetch || !window.AbortController) return;

    var targets = (form.getAttribute("data-live-targets") || "")
      .split(",")
      .map(function (s) {
        return s.trim();
      })
      .filter(Boolean);
    if (!targets.length) return;

    var controller = null;
    var debounceTimer = null;

    function apply() {
      if (controller) controller.abort();
      controller = new AbortController();

      var params = new URLSearchParams(new FormData(form));
      params.delete("page"); // a filter change always starts back at page 1
      var qs = params.toString();
      var url = (form.getAttribute("action") || location.pathname) +
        (qs ? "?" + qs : "");

      fetch(url, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: controller.signal,
      })
        .then(function (r) {
          return r.text();
        })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, "text/html");
          targets.forEach(function (sel) {
            var next = doc.querySelector(sel);
            var current = document.querySelector(sel);
            if (next && current) current.innerHTML = next.innerHTML;
          });
          history.replaceState(null, "", url);
        })
        .catch(function (err) {
          if (err.name !== "AbortError") form.submit(); // fetch itself failed
        });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      clearTimeout(debounceTimer);
      apply();
    });

    // Radios and the craft <select> fire immediately; the search box (type
    // search/text) debounces so it doesn't re-fetch on every keystroke.
    form.addEventListener("change", function (e) {
      var type = e.target.type;
      if (type === "search" || type === "text") return;
      apply();
    });

    form.addEventListener("input", function (e) {
      var type = e.target.type;
      if (type !== "search" && type !== "text") return;
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(apply, 300);
    });
  }

  /* --- Expandable bio ------------------------------------------------------
     The person page bio is clamped by CSS (.bio-body.is-collapsed, gated on
     html.has-js). This reveals the See more / See less button — but only
     when the bio is actually taller than the clamp, since a two-line bio
     needs no control, the same "no dead buttons" rule initPagedSection
     follows. Toggling .is-collapsed is all the button does; the height
     change is left to the browser's default repaint. */

  function initBioToggle() {
    var root = document.querySelector("[data-bio]");
    if (!root) return;
    var body = root.querySelector(".bio-body");
    var btn = root.querySelector("[data-bio-toggle]");
    if (!body || !btn) return;

    // scrollHeight is the full text height, clientHeight the clamped box; a
    // few px of tolerance absorbs sub-pixel rounding so a bio that exactly
    // fills the clamp doesn't get a pointless toggle.
    if (body.scrollHeight - body.clientHeight < 4) return;

    btn.hidden = false;
    btn.addEventListener("click", function () {
      var collapsed = body.classList.toggle("is-collapsed");
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
      btn.textContent = collapsed ? "See more" : "See less";
    });
  }

  function init() {
    initMeters();
    initCounters();
    initThemeToggle();
    initNavToggle();
    initPagedSections();
    initLiveFilter();
    initBioToggle();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
