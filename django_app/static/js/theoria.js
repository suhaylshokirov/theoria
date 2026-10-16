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

  /* --- Language ------------------------------------------------------------
     base.html sets <html lang> to the active language and renders every
     string this script displays into the #js-strings JSON block, so the
     script never owns a phrase -- see core.context_processors.js_strings. */

  var STRINGS = (function () {
    var el = document.getElementById("js-strings");
    try {
      return el ? JSON.parse(el.textContent) : {};
    } catch (e) {
      return {};
    }
  })();

  // The English key doubles as the fallback, so a missing block degrades to
  // the old English behaviour rather than to "undefined".
  function t(key, vars) {
    var out = Object.prototype.hasOwnProperty.call(STRINGS, key) ? STRINGS[key] : key;
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        out = out.split("{" + k + "}").join(vars[k]);
      });
    }
    return out;
  }

  // Uzbek numbers format as Russian ones: Django's own Uzbek format uses a
  // non-breaking space for thousands and a comma for decimals, and browser
  // Intl data for "uz" is unreliable (some builds claim support and still
  // print 1,234.5). Using "ru" keeps the count-up in agreement with the
  // number the server already printed.
  function numberLocale() {
    var lang = document.documentElement.lang || "en";
    return lang === "uz" ? "ru" : lang;
  }

  // English writes 1,234.5; Russian and Uzbek write 1 234,5, so the decimal
  // mark has to be read back the way the server printed it.
  function parseCell(el) {
    var raw = el.textContent.replace(/[$\s★]/g, "");
    raw = numberLocale() === "en" ? raw.replace(/,/g, "") : raw.replace(",", ".");
    var n = parseFloat(raw);
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
        if (Number.isInteger(v)) td.textContent = v.toLocaleString(numberLocale());

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
          td.textContent = v.toLocaleString(numberLocale());
        }
      });
  }

  /* --- Count-up ------------------------------------------------------------- */

  function format(value, decimals) {
    if (decimals > 0) {
      // Fractional values are ratings, which keep a dot in every language
      // (the server prints them with floatformat "u").
      return value.toLocaleString("en", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      });
    }
    return Math.round(value).toLocaleString(numberLocale());
  }

  function runCounter(el) {
    var target = parseFloat(el.getAttribute("data-count"));
    if (isNaN(target)) return;
    var decimals = parseInt(el.getAttribute("data-decimals") || "0", 10);
    // An optional trailing string kept through the animation, e.g. the "+" on
    // the homepage's approximate counts ("1,200+").
    var suffix = el.getAttribute("data-suffix") || "";

    if (reduce) {
      el.textContent = format(target, decimals) + suffix;
      return;
    }

    var duration = 1100;
    var start = null;

    function step(now) {
      if (start === null) start = now;
      var t = Math.min((now - start) / duration, 1);
      var eased = 1 - Math.pow(1 - t, 3); // ease-out cubic, settling to rest
      el.textContent = format(target * eased, decimals) + suffix;
      if (t < 1) requestAnimationFrame(step);
      else el.textContent = format(target, decimals) + suffix;
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

  /* The two <meta name="theme-color"> tags in base.html are keyed on
     prefers-color-scheme, which is right until the reader overrides the OS from
     the header toggle — at which point a dark page would still be reporting
     white to the browser chrome. Both tags are rewritten with the resolved
     colour so it no longer matters which one the engine matched.

     The value is read back out of --paper rather than hardcoded, for the same
     reason analytics.js reads its palette from CSS: the tokens in theoria.css
     stay the one place a colour is defined. Only Safari and Chrome for Android
     act on this; everywhere else it is inert, not wrong. */
  function syncThemeColor() {
    var tags = document.querySelectorAll('meta[name="theme-color"]');
    if (!tags.length) return;
    var paper = getComputedStyle(document.documentElement)
      .getPropertyValue("--paper");
    paper = paper ? paper.trim() : "";
    if (!paper) return;
    tags.forEach(function (tag) {
      tag.setAttribute("content", paper);
    });
  }

  function initThemeToggle() {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;

    function syncLabel() {
      var next = currentTheme() === "dark" ? "light" : "dark";
      btn.setAttribute("aria-label", t(next === "dark" ? "Switch to dark theme" : "Switch to light theme"));
    }

    syncLabel();

    // A back/forward-cache restore re-shows this page without re-running the
    // script, but the inline <head> handler re-applies the saved theme on the
    // same pageshow — so the button's label has to be re-synced to match.
    window.addEventListener("pageshow", syncLabel);

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
      btn.setAttribute("aria-label", t(open ? "Close menu" : "Open menu"));
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

    // A season picker switching which table is on show (initSeasonPicker)
    // sends the table it reveals back to its first page.
    root.addEventListener("pager:reset", function () {
      if (current === 1) return;
      current = 1;
      render();
    });

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
          // Safari caps history.replaceState at ~100 calls per 30s and throws
          // a SecurityError past it. Fast typing against a 300ms debounce can
          // reach that, and the throw would otherwise land in the .catch below
          // — which reads any failure as "fetch died" and does a full
          // form.submit(), reloading the page mid-keystroke. The URL bar going
          // stale is the acceptable outcome here; the reload is not.
          try {
            history.replaceState(null, "", url);
          } catch (e) {}
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

    btn.addEventListener("click", function () {
      var collapsed = body.classList.toggle("is-collapsed");
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
      btn.textContent = t(collapsed ? "See more" : "See less");
    });

    // scrollHeight is the full text height, clientHeight the clamped box; a
    // few px of tolerance absorbs sub-pixel rounding so a bio that exactly
    // fills the clamp doesn't get a pointless toggle.
    function measure() {
      // Only meaningful while the clamp is actually applied — once the reader
      // has expanded the bio, scrollHeight and clientHeight agree and the test
      // would hide the button they are using.
      if (!body.classList.contains("is-collapsed")) return;
      btn.hidden = body.scrollHeight - body.clientHeight < 4;
    }
    measure();

    // ...but the first measurement runs at DOMContentLoaded, when the body face
    // is still the fallback: the webfonts load from Google with display=swap, so
    // Instrument Sans arrives later and reflows the paragraph to a different
    // line count. Whether that reflow crosses the six-line clamp depends on the
    // network, so the same bio could get a "See more" button in one browser and
    // not in another, or on the same browser twice running. Re-measure once the
    // real face is in. document.fonts is Safari 10+/Firefox 41+; where it is
    // missing the single measurement above still stands.
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(measure).catch(function () {});
    }
  }

  /* --- Click-to-play video embeds ---------------------------------------
     The movie page ships the trailer thumbnail as a link to YouTube, not a
     live iframe — see movies/_video_embed.html. One delegated listener
     upgrades a click into an inline youtube-nocookie iframe, so no player
     loads until it is actually asked for, and a no-JS reader just follows
     the link. */

  /* The <img> ships as hqdefault.jpg (480x360) — the one still YouTube always
     has, so it is the safe no-JS default. But the frame renders ~1200px wide
     on desktop, where 480px is visibly soft. Try the sharper stills in turn
     and swap one in only once it has actually loaded, so a video without a
     maxres/sd still (YouTube 404s those) just keeps the hqdefault it already
     showed. Playback resolution itself is YouTube's call — bandwidth-adaptive,
     and not settable from an embed — so this is only about the preview. */
  function upgradeVideoThumb(img) {
    var play = img.closest("[data-video-play]");
    var key = play && play.getAttribute("data-video-key");
    if (!key) return;
    var stills = ["maxresdefault", "sddefault"];

    (function tryStill(i) {
      if (i >= stills.length) return;
      var url =
        "https://img.youtube.com/vi/" +
        encodeURIComponent(key) +
        "/" +
        stills[i] +
        ".jpg";
      var probe = new Image();
      probe.onload = function () {
        // A missing still sometimes resolves to a tiny grey placeholder rather
        // than a 404 — anything that small is not a real thumbnail.
        if (probe.naturalWidth > 320) img.src = url;
        else tryStill(i + 1);
      };
      probe.onerror = function () {
        tryStill(i + 1);
      };
      probe.src = url;
    })(0);
  }

  function initVideoEmbeds() {
    document
      .querySelectorAll("[data-video-embed] [data-video-play] img")
      .forEach(upgradeVideoThumb);

    document.addEventListener("click", function (e) {
      var play = e.target.closest("[data-video-play]");
      if (!play) return;
      var frame = play.closest("[data-video-embed]");
      var key = play.getAttribute("data-video-key");
      if (!frame || !key) return; // no key -> let the link navigate

      e.preventDefault();
      var iframe = document.createElement("iframe");
      iframe.className = "video-iframe";
      iframe.src =
        "https://www.youtube-nocookie.com/embed/" +
        encodeURIComponent(key) +
        "?autoplay=1";
      iframe.title = play.getAttribute("data-video-name") || t("Video");
      // The site sends Referrer-Policy: same-origin (Django's SecurityMiddleware
      // default), which strips the Referer on this cross-origin load and makes
      // YouTube reject the embed with "player configuration error" (153). Send
      // the origin for just this iframe so YouTube can validate the domain.
      iframe.referrerPolicy = "strict-origin-when-cross-origin";
      iframe.allow =
        "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture";
      iframe.setAttribute("allowfullscreen", "");
      frame.innerHTML = "";
      frame.appendChild(iframe);
    });
  }

  /* --- Season picker (show page episodes) --------------------------------
     Every season's episode table is already in the document (see
     movies/_episode_table.html) — this only shows the one whose radio is
     checked, by toggling .is-active (theoria.css hides every .season-panel
     that lacks it, but only once html.has-js is set, which is also what
     reveals .season-picker itself). No live-filter round trip: the whole
     point, per the task brief, is that switching seasons is a repaint, the
     same posture initPagedSection() takes for cast/crew paging.

     Task 132: "All seasons" is a panel like any season -- the show-wide KPI
     cards and one row per season -- and the rating chart under the tables
     (series_ratings.js) listens to the same radios. Switching sends the
     revealed table back to page 1. A season named in the overview (its
     table row, or the best/weakest season card) is a link to that season's
     panel; with JS it selects the season through its radio instead and
     brings the picker back into view, so the reader lands at the top of the
     season they chose rather than mid-page. */

  function initSeasonPicker() {
    var picker = document.querySelector("[data-season-picker]");
    if (!picker) return;
    var panels = Array.prototype.slice.call(
      document.querySelectorAll("[data-season-panel]")
    );
    if (!panels.length) return;

    function show(value) {
      panels.forEach(function (panel) {
        var on = panel.getAttribute("data-season-panel") === value;
        panel.classList.toggle("is-active", on);
        if (on) {
          panel.querySelectorAll("[data-paged]").forEach(function (paged) {
            paged.dispatchEvent(new CustomEvent("pager:reset"));
          });
        }
      });
    }

    picker.addEventListener("change", function (e) {
      if (e.target.name !== "season") return;
      show(e.target.value);
    });

    picker.parentNode.addEventListener("click", function (e) {
      var link = e.target.closest("[data-season-select]");
      if (!link) return;
      var input = picker.querySelector(
        'input[name=season][value="' + link.getAttribute("data-season-select") + '"]'
      );
      if (!input) return;
      e.preventDefault();
      input.checked = true;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      picker.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
      input.focus({ preventScroll: true });
    });

    var checked = picker.querySelector("input[name=season]:checked");
    if (checked) show(checked.value);
  }

  /* --- Filter menu -----------------------------------------------------
     A <select data-menu> in a toolbar becomes a compact in-page dropdown.
     The native <select> picker on a phone opens a full-screen OS list, and
     the genre filter has 18 options — the whole viewport, as reported.

     Progressive enhancement: the <select> stays in the DOM as the value
     store (hidden, so it still submits with the form) and as the no-JS
     control. This builds a trigger button + a short, scrollable listbox
     beside it, and mirrors every change back onto the <select> — including
     a bubbling `change` event, so initLiveFilter() re-fetches exactly as it
     does for the native control. */

  function initFilterMenu() {
    document.querySelectorAll("select[data-menu]").forEach(buildFilterMenu);
  }

  function buildFilterMenu(select) {
    var options = Array.prototype.slice.call(select.options);
    if (!options.length) return;

    var labelText =
      select.getAttribute("aria-label") ||
      (select.labels && select.labels[0] && select.labels[0].textContent) ||
      t("Filter");

    var wrap = document.createElement("div");
    wrap.className = "menu";

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "menu-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-label", labelText.trim());
    var labelEl = document.createElement("span");
    labelEl.className = "menu-label";
    trigger.appendChild(labelEl);

    var panel = document.createElement("div");
    panel.className = "menu-panel";
    panel.setAttribute("role", "listbox");
    panel.hidden = true;

    var optionEls = options.map(function (opt, i) {
      var el = document.createElement("button");
      el.type = "button";
      el.className = "menu-option";
      el.setAttribute("role", "option");
      el.dataset.value = opt.value;
      el.textContent = opt.textContent;
      el.tabIndex = -1;
      el.addEventListener("click", function () {
        choose(i);
        close(true);
      });
      panel.appendChild(el);
      return el;
    });

    function syncFromSelect() {
      var i = select.selectedIndex < 0 ? 0 : select.selectedIndex;
      labelEl.textContent = options[i].textContent;
      optionEls.forEach(function (el, j) {
        el.setAttribute("aria-selected", j === i ? "true" : "false");
      });
    }

    function choose(i) {
      if (select.selectedIndex !== i) {
        select.selectedIndex = i;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      syncFromSelect();
    }

    var open = false;

    function setOpen(next) {
      open = next;
      panel.hidden = !next;
      trigger.setAttribute("aria-expanded", next ? "true" : "false");
    }

    function currentOption() {
      var i = select.selectedIndex < 0 ? 0 : select.selectedIndex;
      return optionEls[i];
    }

    function openMenu() {
      if (open) return;
      setOpen(true);
      var current = currentOption();
      // Focus on the next frame: the panel has just been un-hidden, and
      // focusing an element in the same tick it stops being display:none is
      // unreliable across engines. Jump straight to the current choice rather
      // than scrolling the list from the top.
      requestAnimationFrame(function () {
        current.scrollIntoView({ block: "nearest" });
        current.focus();
      });
      document.addEventListener("pointerdown", onOutside, true);
    }

    function close(focusTrigger) {
      if (!open) return;
      setOpen(false);
      document.removeEventListener("pointerdown", onOutside, true);
      if (focusTrigger) trigger.focus();
    }

    function onOutside(e) {
      if (!wrap.contains(e.target)) close(false);
    }

    trigger.addEventListener("click", function () {
      if (open) close(true);
      else openMenu();
    });

    trigger.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openMenu();
      }
    });

    panel.addEventListener("keydown", function (e) {
      var els = optionEls;
      // Fall back to the current choice when focus hasn't landed on an option
      // yet (e.g. the very first arrow key right after opening).
      var idx = els.indexOf(document.activeElement);
      if (idx < 0) idx = select.selectedIndex < 0 ? 0 : select.selectedIndex;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        (els[idx + 1] || els[0]).focus();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        (els[idx - 1] || els[els.length - 1]).focus();
      } else if (e.key === "Home") {
        e.preventDefault();
        els[0].focus();
      } else if (e.key === "End") {
        e.preventDefault();
        els[els.length - 1].focus();
      } else if (e.key === "Escape") {
        e.preventDefault();
        close(true);
      } else if (e.key === "Tab") {
        close(false);
      }
    });

    // A back/forward-cache restore can bring the page back with the menu open.
    window.addEventListener("pageshow", function () {
      close(false);
    });

    select.hidden = true;
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(trigger);
    wrap.appendChild(panel);
    wrap.appendChild(select);
    syncFromSelect();
  }

  /* --- Film guide ---------------------------------------------------------- */

  function initAssistant() {
    var root = document.querySelector("[data-ai-assistant]");
    if (!root) return;

    var trigger = root.querySelector("[data-ai-trigger]");
    var panel = root.querySelector("#ai-assistant-panel");
    var close = root.querySelector("[data-ai-close]");
    var newChat = root.querySelector("[data-ai-new]");
    var historyToggle = root.querySelector("[data-ai-history-toggle]");
    var historyClose = root.querySelector("[data-ai-history-close]");
    var history = root.querySelector("[data-ai-history]");
    var historyList = root.querySelector("[data-ai-history-list]");
    var resize = root.querySelector("[data-ai-resize]");
    var welcome = root.querySelector("[data-ai-welcome]");
    var welcomeOpen = root.querySelector("[data-ai-welcome-open]");
    var welcomeClose = root.querySelector("[data-ai-welcome-close]");
    var messages = root.querySelector("[data-ai-messages]");
    var form = root.querySelector("[data-ai-form]");
    var input = root.querySelector("[data-ai-input]");
    var send = form.querySelector("button[type='submit']");
    var actions = root.querySelectorAll("[data-ai-prompt]");
    var chatEndpoint = root.getAttribute("data-ai-chat-endpoint");
    var feedbackEndpoint = root.getAttribute("data-ai-feedback-endpoint");
    var conversationsEndpoint = root.getAttribute("data-ai-conversations-endpoint");
    var newConversationEndpoint = root.getAttribute("data-ai-new-conversation-endpoint");
    var authenticated = root.getAttribute("data-ai-authenticated") === "true";
    var csrf = form.querySelector("[name='csrfmiddlewaretoken']");
    var pending = false;
    var sessionStorageKey = "theoria-assistant-session";
    var welcomeStorageKey = "theoria-assistant-welcome-dismissed";
    var sessionId = null;
    var welcomeDismissed = false;
    var closeTimer = null;
    var closeDuration = reduce ? 0 : 220;
    var openingMarkup = messages.innerHTML;
    var promptRequests = {
      tonight: "Pick a movie for tonight",
      mood: "Find a movie for my mood",
      genre: "Help me browse movies by genre",
      surprise: "Surprise me with a movie"
    };

    try {
      sessionId = window.sessionStorage.getItem(sessionStorageKey);
      welcomeDismissed = window.sessionStorage.getItem(welcomeStorageKey) === "true";
    } catch (error) {
      // Private browsing can deny storage; the server will begin a new chat
      // session for each request and the assistant remains usable.
    }

    function saveSessionId(nextSessionId) {
      if (!nextSessionId) return;
      sessionId = nextSessionId;
      try {
        window.sessionStorage.setItem(sessionStorageKey, sessionId);
      } catch (error) {
        // The response still works when the browser declines storage.
      }
    }

    function closeHistory() {
      if (!history) return;
      history.hidden = true;
      historyToggle.setAttribute("aria-expanded", "false");
    }

    function resetConversation() {
      messages.innerHTML = openingMarkup;
      actions = root.querySelectorAll("[data-ai-prompt]");
      bindActions();
      messages.scrollTop = 0;
      input.value = "";
      send.disabled = true;
    }

    function dismissWelcome() {
      if (!welcome) return;
      welcome.hidden = true;
      try {
        window.sessionStorage.setItem(welcomeStorageKey, "true");
      } catch (error) {
        // The greeting simply returns after a reload when storage is unavailable.
      }
    }

    function setOpen(open) {
      if (closeTimer) {
        window.clearTimeout(closeTimer);
        closeTimer = null;
      }

      trigger.setAttribute("aria-expanded", open ? "true" : "false");
      trigger.setAttribute("aria-label", t(open ? "Close film guide" : "Open film guide"));
      if (open) {
        panel.hidden = false;
        panel.classList.remove("is-closing");
        window.setTimeout(function () { input.focus(); }, 0);
      } else {
        closeHistory();
        if (panel.hidden) return;
        panel.classList.add("is-closing");
        closeTimer = window.setTimeout(function () {
          panel.hidden = true;
          panel.classList.remove("is-closing");
          closeTimer = null;
        }, closeDuration);
        trigger.focus();
      }
    }

    function setExpanded(expanded) {
      if (!resize) return;
      panel.classList.toggle("is-expanded", expanded);
      resize.setAttribute("aria-pressed", expanded ? "true" : "false");
      var label = resize.getAttribute(expanded ? "data-shrink-label" : "data-expand-label");
      resize.setAttribute("aria-label", label);
      resize.setAttribute("title", label);
    }

    // The model writes film titles as **Title**. Render those as <strong>
    // by building text nodes, never innerHTML, so a reply cannot inject markup.
    function appendEmphasis(parent, text) {
      String(text).split(/\*\*(.+?)\*\*/g).forEach(function (part, index) {
        if (!part) return;
        if (index % 2) {
          var strong = document.createElement("strong");
          strong.textContent = part;
          parent.appendChild(strong);
        } else {
          parent.appendChild(document.createTextNode(part));
        }
      });
    }

    function addMessage(text, role, extraClass) {
      var message = document.createElement("div");
      message.className = "ai-message ai-message-" + role;
      if (extraClass) message.classList.add(extraClass);
      var paragraph = document.createElement("p");
      if (role === "assistant") appendEmphasis(paragraph, text);
      else paragraph.textContent = text;
      message.appendChild(paragraph);
      messages.appendChild(message);
      messages.scrollTop = messages.scrollHeight;
      return message;
    }

    function setPending(nextPending) {
      pending = nextPending;
      input.disabled = nextPending;
      actions.forEach(function (action) { action.disabled = nextPending; });
      send.disabled = nextPending || !input.value.trim();
      messages.setAttribute("aria-busy", nextPending ? "true" : "false");
    }

    function requestJson(endpoint, payload) {
      return fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-CSRFToken": csrf ? csrf.value : ""
        },
        body: JSON.stringify(payload)
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          return { ok: response.ok, data: data };
        });
      });
    }

    function getJson(endpoint) {
      return fetch(endpoint, {
        credentials: "same-origin",
        headers: { "Accept": "application/json" }
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          return { ok: response.ok, data: data };
        });
      });
    }

    function renderHistory(conversations) {
      historyList.textContent = "";
      if (!conversations.length) {
        var empty = document.createElement("p");
        empty.className = "ai-assistant-history-empty";
        empty.textContent = t("Your last three chats will appear here.");
        historyList.appendChild(empty);
        return;
      }
      conversations.forEach(function (conversation) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "ai-assistant-history-item";
        if (conversation.id === sessionId) button.classList.add("is-current");
        button.textContent = conversation.title;
        button.addEventListener("click", function () {
          loadConversation(conversation.id);
        });
        historyList.appendChild(button);
      });
    }

    function showHistory() {
      if (!authenticated) {
        signInMessage();
        return;
      }
      history.hidden = false;
      historyToggle.setAttribute("aria-expanded", "true");
      historyList.textContent = "";
      var loading = document.createElement("p");
      loading.className = "ai-assistant-history-empty";
      loading.textContent = t("Loading chats…");
      historyList.appendChild(loading);
      getJson(conversationsEndpoint).then(function (result) {
        if (!result.ok) throw new Error("could not load chats");
        renderHistory(result.data.conversations || []);
      }).catch(function () {
        historyList.textContent = "";
        var error = document.createElement("p");
        error.className = "ai-assistant-history-error";
        error.textContent = t("Your chats could not be loaded just now.");
        historyList.appendChild(error);
      });
    }

    function loadConversation(nextSessionId) {
      if (pending) return;
      setPending(true);
      getJson(conversationsEndpoint + encodeURIComponent(nextSessionId) + "/").then(function (result) {
        if (!result.ok) throw new Error("could not load chat");
        saveSessionId(result.data.conversation.id);
        messages.textContent = "";
        actions = [];
        (result.data.turns || []).forEach(function (turn) {
          addMessage(turn.message, turn.role);
        });
        if (!(result.data.turns || []).length) resetConversation();
        closeHistory();
      }).catch(function () {
        addMessage(t("That chat could not be opened just now. Please try again."), "assistant");
      }).finally(function () {
        setPending(false);
      });
    }

    function startNewConversation() {
      if (pending) return;
      if (!authenticated) {
        signInMessage();
        return;
      }
      setPending(true);
      requestJson(newConversationEndpoint, {}).then(function (result) {
        if (!result.ok) throw new Error("could not start chat");
        saveSessionId(result.data.conversation.id);
        resetConversation();
        closeHistory();
      }).catch(function () {
        addMessage(t("A new chat could not be started just now. Please try again."), "assistant");
      }).finally(function () {
        setPending(false);
      });
    }

    function addRecommendations(recommendations) {
      if (!recommendations.length) return;
      var group = document.createElement("div");
      group.className = "ai-picks";

      recommendations.forEach(function (recommendation, index) {
        var card = document.createElement("article");
        card.className = "ai-recommendation";
        card.style.setProperty("--pick-index", index);

        // The poster repeats the title link, so it is hidden from assistive
        // tech and the keyboard; the title stays the one real link.
        var poster = document.createElement(recommendation.url ? "a" : "span");
        poster.className = "ai-recommendation-poster";
        poster.setAttribute("aria-hidden", "true");
        if (recommendation.url) {
          poster.href = recommendation.url;
          poster.tabIndex = -1;
        }
        if (recommendation.poster) {
          var image = document.createElement("img");
          image.src = recommendation.poster;
          image.alt = "";
          image.loading = "lazy";
          image.width = 64;
          image.height = 96;
          poster.appendChild(image);
        }
        card.appendChild(poster);

        var body = document.createElement("div");
        body.className = "ai-recommendation-body";

        var title = document.createElement(recommendation.url ? "a" : "span");
        title.className = "ai-recommendation-title";
        title.textContent = recommendation.title;
        if (recommendation.url) title.href = recommendation.url;
        body.appendChild(title);

        var meta = document.createElement("p");
        meta.className = "ai-recommendation-meta";
        if (recommendation.year) {
          var year = document.createElement("span");
          year.textContent = recommendation.year;
          meta.appendChild(year);
        }
        if (recommendation.runtime) {
          var runtime = document.createElement("span");
          runtime.textContent = t("{minutes} min", { minutes: recommendation.runtime });
          meta.appendChild(runtime);
        }
        if (recommendation.rating) {
          var rating = document.createElement("span");
          rating.className = "ai-recommendation-rating";
          rating.textContent = "IMDb " + Number(recommendation.rating).toLocaleString(numberLocale(), {
            minimumFractionDigits: 1,
            maximumFractionDigits: 1
          });
          meta.appendChild(rating);
        }
        if (recommendation.status === "on_list") {
          var status = document.createElement("span");
          status.className = "ai-recommendation-status";
          status.textContent = t("On your list");
          meta.appendChild(status);
        }
        if (meta.childNodes.length) body.appendChild(meta);

        var reason = document.createElement("p");
        reason.className = "ai-recommendation-reason";
        reason.textContent = recommendation.reason;
        body.appendChild(reason);

        var feedback = document.createElement("div");
        feedback.className = "ai-recommendation-feedback";
        [
          { action: "watched", label: t("Already watched") },
          { action: "not_interested", label: t("Not for me") }
        ].forEach(function (item) {
          var button = document.createElement("button");
          button.type = "button";
          button.textContent = item.label;
          button.addEventListener("click", function () {
            feedback.querySelectorAll("button").forEach(function (control) { control.disabled = true; });
            requestJson(feedbackEndpoint, {
              action: item.action,
              content_id: recommendation.content_id,
              content_type: recommendation.content_type
            }).then(function (result) {
              if (result.ok) {
                feedback.textContent = result.data.message;
              } else {
                feedback.textContent = result.data.error || "That feedback could not be saved.";
              }
            }).catch(function () {
              feedback.textContent = "That feedback could not be saved.";
            });
          });
          feedback.appendChild(button);
        });
        body.appendChild(feedback);
        card.appendChild(body);
        group.appendChild(card);
      });
      messages.appendChild(group);
      messages.scrollTop = messages.scrollHeight;
    }

    function signInMessage() {
      addMessage("Sign in first and I can use your lists to make a personal recommendation.", "assistant");
    }

    async function respond(prompt, shown) {
      if (pending) return;
      if (!authenticated) {
        signInMessage();
        return;
      }
      addMessage(shown || prompt, "user");
      input.value = "";
      setPending(true);
      var thinking = addMessage("Thinking…", "assistant", "ai-message-thinking");

      try {
        var result = await requestJson(chatEndpoint, {
          message: prompt,
          session_id: sessionId
        });
        thinking.remove();
        if (!result.ok) {
          addMessage(result.data.error || "The guide could not answer just now. Please try again.", "assistant");
          return;
        }
        saveSessionId(result.data.session_id);
        addMessage(result.data.reply, "assistant");
        addRecommendations(result.data.recommendations || []);
      } catch (error) {
        thinking.remove();
        addMessage("The guide could not answer just now. Please try again.", "assistant");
      } finally {
        setPending(false);
    }
    }

    trigger.addEventListener("click", function () {
      dismissWelcome();
      setOpen(panel.hidden || panel.classList.contains("is-closing"));
    });

    close.addEventListener("click", function () {
      setOpen(false);
    });

    if (resize) {
      resize.addEventListener("click", function () {
        setExpanded(!panel.classList.contains("is-expanded"));
      });
    }

    if (newChat) {
      newChat.addEventListener("click", startNewConversation);
    }

    if (historyToggle) {
      historyToggle.addEventListener("click", function () {
        if (history.hidden) showHistory();
        else closeHistory();
      });
    }

    if (historyClose) {
      historyClose.addEventListener("click", closeHistory);
    }

    if (welcomeOpen) {
      welcomeOpen.addEventListener("click", function () {
        dismissWelcome();
        setOpen(true);
      });
    }

    if (welcomeClose) {
      welcomeClose.addEventListener("click", dismissWelcome);
    }

    function bindActions() {
      actions.forEach(function (action) {
        action.addEventListener("click", function () {
          var promptKey = action.getAttribute("data-ai-prompt");
          respond(promptRequests[promptKey] || promptKey, action.textContent.trim());
        });
      });
    }

    bindActions();

    input.addEventListener("input", function () {
      send.disabled = !input.value.trim();
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var prompt = input.value.trim();
      if (prompt) respond(prompt);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !panel.hidden) setOpen(false);
    });

    window.addEventListener("pageshow", function () {
      if (closeTimer) window.clearTimeout(closeTimer);
      closeTimer = null;
      panel.hidden = true;
      panel.classList.remove("is-closing");
      setExpanded(false);
      trigger.setAttribute("aria-expanded", "false");
      trigger.setAttribute("aria-label", t("Open film guide"));
    });

    if (welcome && !welcomeDismissed) {
      window.setTimeout(function () {
        if (panel.hidden) welcome.hidden = false;
      }, 700);
    }
  }

  /* --- Verify-code auto-advance --------------------------------------------
     The code field (accounts/verify.html) is one real input, not six, so
     autocomplete="one-time-code" and paste both work, and it renders its own
     digits -- see the auth design notes. On top of a control that already
     works with JS off, this: strips anything typed that isn't a digit and
     submits automatically once six are in, so the reader never has to find
     the button; and marks whichever one of the six decorative `.code-cell`
     boxes drawn behind the input (auth.css) holds the most recently typed
     digit with .is-active, so each box's border turns lime the instant its
     digit lands -- a highlight only, never what makes a typed digit visible
     in the first place. The resend button gets a live
     countdown mirroring the server's 60-second cooldown (accounts/codes.py's
     RESEND_COOLDOWN); with JS off the button is simply always enabled, and
     the server-side cooldown still refuses an early click with its own
     message. */

  function initCodeInput() {
    var card = document.querySelector("[data-verify-form]");
    if (!card) return;

    var input = card.querySelector(".code-input");
    var cells = card.querySelectorAll(".code-cell");
    if (input && cells.length) {
      // Re-run on every event that can move the caret without changing the
      // value (arrow keys, a click) as well as on "input" itself. Highlights
      // the cell just *behind* the caret -- the one holding the digit most
      // recently typed -- not the empty one still waiting for it, so typing
      // a digit is what lights its own box up (an empty value has nothing
      // behind the caret, so that case falls back to cell 0, ready to type).
      var syncActiveCell = function () {
        var pos = input.selectionStart == null ? input.value.length : input.selectionStart;
        var caretIndex = Math.min(Math.max(pos - 1, 0), cells.length - 1);
        cells.forEach(function (cell, i) {
          cell.classList.toggle("is-active", document.activeElement === input && i === caretIndex);
        });
      };

      input.addEventListener("input", function () {
        var digits = input.value.replace(/\D/g, "").slice(0, 6);
        input.value = digits;
        syncActiveCell();
        if (digits.length === 6 && input.form) {
          // requestSubmit() (not submit()) deliberately: submit() bypasses
          // the form's "submit" event entirely, which would silently skip
          // initAuthFormSubmitState()'s loading state on exactly the path
          // most verify attempts actually take. Falls back for the rare
          // browser old enough not to have it.
          if (input.form.requestSubmit) input.form.requestSubmit();
          else input.form.submit();
        }
      });

      ["keyup", "click", "focus"].forEach(function (evt) {
        input.addEventListener(evt, syncActiveCell);
      });
      input.addEventListener("blur", function () {
        cells.forEach(function (cell) {
          cell.classList.remove("is-active");
        });
      });

      syncActiveCell();
    }

    var resendBtn = card.querySelector("[data-resend-button]");
    if (resendBtn) {
      var seconds = 60;
      var label = resendBtn.textContent;
      resendBtn.disabled = true;
      var timer = setInterval(function () {
        seconds -= 1;
        if (seconds <= 0) {
          clearInterval(timer);
          resendBtn.disabled = false;
          resendBtn.textContent = label;
        } else {
          resendBtn.textContent = t("Resend wait", { label: label, seconds: seconds });
        }
      }, 1000);
    }
  }

  /* --- Auth form submit state ----------------------------------------------
     Every .auth-form (signup, login, verify) is a plain HTML POST -- there's
     no fetch() here to know a request finished, only that one started. So
     rather than a real progress indicator, this swaps the submit button's
     label for a spinner the instant the browser accepts the submission,
     which is what keeps a real network round-trip from reading as a dead
     click. Never blocks the submission itself: disabling the button in a
     "submit" handler doesn't cancel a navigation already under way.

     Reads its busy label from a data attribute (defaulting to "Sending…")
     rather than hardcoding one, since "Verifying…" reads better on the code
     form than the generic label the other two forms want. */
  function initAuthFormSubmitState() {
    document.querySelectorAll(".auth-form").forEach(function (form) {
      var btn = form.querySelector('button[type="submit"]');
      var idleLabel = btn ? btn.innerHTML : "";
      var submitting = false;

      form.addEventListener("submit", function (event) {
        // One POST per page load. On the verify form the auto-submit at six
        // digits and an Enter/click can both fire: the first signs the reader
        // in and rotates the CSRF token, so the second would land as a 403.
        if (submitting) {
          event.preventDefault();
          return;
        }
        submitting = true;
        if (!btn) return;
        var busyLabel = btn.getAttribute("data-busy-label") || t("Sending…");
        btn.disabled = true;
        btn.innerHTML =
          '<span class="btn-spinner" aria-hidden="true"></span><span>' + busyLabel + "</span>";
      });

      // A back/forward-cache restore brings the page back mid-submit; unlock
      // it so the form isn't stuck on a spinner that will never resolve.
      window.addEventListener("pageshow", function (event) {
        if (!event.persisted || !submitting) return;
        submitting = false;
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = idleLabel;
        }
      });
    });
  }

  /* --- Inline email validation ----------------------------------------------
     A malformed address is worth catching before the round trip a server
     validation error costs -- particularly on signup, where that round trip
     also means waiting on an email that was never going to arrive. Reuses
     the same .form-error markup/styling a server-rendered error already
     uses (see accounts/signup.html etc.), so a reader can't tell which kind
     they're looking at; is-live only changes how it enters, not how it
     looks. Scoped to email fields -- the one place a format check catches
     something a `required` attribute alone doesn't. */
  function initInlineValidation() {
    document.querySelectorAll(".auth-form input[type=email]").forEach(function (input) {
      var wrap = input.closest("div");
      if (!wrap) return;

      function clearErrors() {
        wrap.querySelectorAll(".form-error").forEach(function (el) {
          el.remove();
        });
      }

      input.addEventListener("blur", function () {
        if (input.value && !input.checkValidity()) {
          clearErrors();
          var p = document.createElement("p");
          p.className = "form-error is-live";
          p.textContent = t("Enter a valid email address.");
          wrap.appendChild(p);
        }
      });

      // Clears on the next keystroke, not just the next blur -- including a
      // stale server-rendered error from before this field was touched, so
      // fixing the address is what makes the message go away, not
      // resubmitting the form.
      input.addEventListener("input", function () {
        if (!input.value || input.checkValidity()) clearErrors();
      });
    });
  }

  /* --- Account menu -------------------------------------------------------
     The signed-in header link (base.html's [data-account-menu]) is a real
     <a href="/me/"> with the dropdown panel already in the DOM, hidden --
     so with no JS it is simply a link to the reader page, no enhancement
     required. With JS on, the trigger's click is redirected into opening the
     panel instead of navigating. Deliberately not built on initFilterMenu's
     buildFilterMenu(): that one *constructs* its trigger/panel from a
     <select> it is replacing; this one only wires up markup base.html has
     already rendered. */
  function initAccountMenu() {
    document.querySelectorAll("[data-account-menu]").forEach(function (wrap) {
      var trigger = wrap.querySelector("[data-account-trigger]");
      var panel = wrap.querySelector("[data-account-panel]");
      if (!trigger || !panel) return;

      var open = false;
      // Hover opens are provisional (leaving closes them); a click pins the
      // panel open until an outside click, Escape, or a second click.
      var hoverOpened = false;
      var closeTimer = null;
      // Hover only where there is a real hovering pointer and the desktop
      // dropdown layout -- inside the phone menu the panel is an inline list.
      var canHover = window.matchMedia("(hover: hover) and (pointer: fine) and (min-width: 1101px)");

      function setOpen(next) {
        open = next;
        panel.hidden = !next;
        trigger.setAttribute("aria-expanded", next ? "true" : "false");
      }

      function onOutside(e) {
        if (!wrap.contains(e.target)) close();
      }

      // On the document, not the wrap: a hover-opened panel never received
      // focus, so a keydown on the wrap would never see this Escape.
      function onKeydown(e) {
        if (e.key !== "Escape") return;
        var hadFocus = wrap.contains(document.activeElement);
        close();
        if (hadFocus) trigger.focus();
      }

      function openMenu() {
        if (open) return;
        setOpen(true);
        document.addEventListener("pointerdown", onOutside, true);
        document.addEventListener("keydown", onKeydown);
      }

      function close() {
        clearTimeout(closeTimer);
        hoverOpened = false;
        if (!open) return;
        setOpen(false);
        document.removeEventListener("pointerdown", onOutside, true);
        document.removeEventListener("keydown", onKeydown);
      }

      trigger.addEventListener("click", function (e) {
        e.preventDefault();
        if (open && !hoverOpened) {
          close();
        } else {
          openMenu();
          hoverOpened = false;
        }
      });

      wrap.addEventListener("pointerenter", function (e) {
        if (e.pointerType !== "mouse" || !canHover.matches) return;
        clearTimeout(closeTimer);
        if (!open) {
          openMenu();
          hoverOpened = true;
        }
      });

      // A short grace period so the pointer can cross the gap between the
      // trigger and the panel without the panel snapping shut.
      wrap.addEventListener("pointerleave", function (e) {
        if (e.pointerType !== "mouse" || !hoverOpened) return;
        closeTimer = setTimeout(close, 200);
      });

      wrap.addEventListener("focusout", function (e) {
        if (open && !wrap.contains(e.relatedTarget)) close();
      });

      // A back/forward-cache restore can bring the page back with the panel
      // open, same reasoning as initFilterMenu's pageshow listener.
      window.addEventListener("pageshow", function () {
        close();
      });
    });
  }

  /* --- Language menu ------------------------------------------------------
     base.html's [data-lang-menu] is a native <details>, so it already opens
     and closes on a click with no JS. This only adds what <details> lacks:
     closing on an outside click, on Escape (returning focus to the trigger),
     and when focus tabs out -- plus on a back/forward-cache restore, which
     can bring the page back with it open. */
  function initLangMenu() {
    document.querySelectorAll("[data-lang-menu]").forEach(function (menu) {
      var trigger = menu.querySelector("summary");

      document.addEventListener("pointerdown", function (e) {
        if (menu.open && !menu.contains(e.target)) menu.open = false;
      }, true);

      menu.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && menu.open) {
          menu.open = false;
          if (trigger) trigger.focus();
        }
      });

      menu.addEventListener("focusout", function (e) {
        if (menu.open && e.relatedTarget && !menu.contains(e.relatedTarget)) {
          menu.open = false;
        }
      });

      window.addEventListener("pageshow", function () {
        menu.open = false;
      });
    });
  }

  /* --- Language switch ----------------------------------------------------
     A page has one URL in every language; the choice is the language cookie.
     With JS the switcher posts without `next` -- Django's set_language then
     answers 204 and sets the cookie, with no redirect-and-render -- and the
     page reloads in place: same history entry, so switching adds nothing to
     Back. Any failure falls back to the plain form submit (the no-JS path). */
  function initLangSwitch() {
    document.querySelectorAll("form[data-lang-switch]").forEach(function (form) {
      function onSubmit(event) {
        var submitter = event.submitter;
        if (!submitter || !submitter.value) return;
        event.preventDefault();

        var menu = form.querySelector("[data-lang-menu]");
        if (menu) menu.open = false;
        if (submitter.value === document.documentElement.lang) return;

        var body = new FormData(form);
        body.delete("next");
        body.set("language", submitter.value);

        function fallback() {
          form.removeEventListener("submit", onSubmit);
          form.requestSubmit(submitter);
        }

        fetch(form.action, {
          method: "POST",
          body: body,
          headers: { Accept: "application/json" },
          credentials: "same-origin"
        }).then(function (response) {
          if (response.status === 204) window.location.reload();
          else fallback();
        }).catch(fallback);
      }
      form.addEventListener("submit", onSubmit);
    });
  }

  /* --- Language freshness -------------------------------------------------
     The back/forward cache (and the plain HTTP cache) can bring back a page
     rendered in a language the reader has since left. Compare the language
     this page was rendered in with the cookie on every pageshow, and when
     the tab becomes visible again (a switch made in another tab); on a
     mismatch, hide the stale text and reload. A sessionStorage key stops a
     browser that blocks the cookie, or a server that ignores it, from
     reloading forever. */
  function initLanguageFreshness() {
    var form = document.querySelector("form[data-lang-switch]");
    if (!form) return;
    var cookieName = form.getAttribute("data-lang-cookie");
    var known = {};
    form.querySelectorAll("button[name=language]").forEach(function (b) {
      known[b.value] = true;
    });
    var rendered = document.documentElement.lang;
    var GUARD = "theoria-lang-reload";

    function wanted() {
      var match = document.cookie.match(
        new RegExp("(?:^|;\\s*)" + cookieName + "=([^;]*)")
      );
      var value = match ? decodeURIComponent(match[1]) : "";
      return known[value] ? value : "en";
    }

    function check() {
      var want = wanted();
      var key = want + window.location.pathname;
      try {
        if (want === rendered) {
          sessionStorage.removeItem(GUARD);
          return;
        }
        if (sessionStorage.getItem(GUARD) === key) return;
        sessionStorage.setItem(GUARD, key);
      } catch (e) {
        return;
      }
      document.documentElement.style.visibility = "hidden";
      window.location.reload();
    }

    window.addEventListener("pageshow", check);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") check();
    });
    check();
  }

  /* --- Collection actions (Like / Watch later / Add to top) ---------------
     Optimistic: the pill flips (and blooms) the instant it's pressed, and
     the request catches up behind it. Waiting on the round-trip first --
     Vercel function, then Neon -- made every press feel laggy, and worse
     after either had gone idle.

     Presses are sent one at a time, in order, against the toggle endpoint,
     so however fast someone taps, the server lands on the same state as the
     pill. Once the queue drains, the last reply is taken as the truth and
     the pill snaps to it (without replaying the bloom) if another tab
     changed things in the meantime.

     Failure paths: HTML back instead of JSON (the anonymous-user login
     redirect) replays as a real form.submit(), same as with JS off. A
     network error or non-OK status reloads the page instead -- after
     optimistic flips, the page itself is the only honest record of which
     presses actually landed. */
  function initCollectionActions() {
    if (!window.fetch) return;

    document.querySelectorAll(".collection-actions form").forEach(function (form) {
      var btn = form.querySelector(".collection-action");
      if (!btn) return;

      var queue = Promise.resolve();
      var pending = 0;
      var failed = false;

      function show(on, animate) {
        btn.classList.toggle("is-selected", on);
        btn.setAttribute("aria-pressed", String(on));
        btn.classList.remove("just-toggled-on");
        if (on && animate) {
          // Force a reflow between remove and re-add so the swell replays.
          void btn.offsetWidth;
          btn.classList.add("just-toggled-on");
        }
      }

      function send() {
        if (failed) return null;
        return fetch(form.getAttribute("action"), {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        }).then(function (response) {
          var contentType = response.headers.get("Content-Type") || "";
          if (response.ok && contentType.indexOf("application/json") !== -1) {
            return response.json();
          }
          failed = true;
          if (response.ok) {
            form.submit();
          } else {
            window.location.reload();
          }
          return null;
        });
      }

      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (failed) return;

        show(!btn.classList.contains("is-selected"), true);
        pending += 1;

        queue = queue
          .then(send)
          .then(function (data) {
            pending -= 1;
            if (data && pending === 0) {
              var truth = !!data.selected;
              if (truth !== btn.classList.contains("is-selected")) show(truth, false);
            }
          })
          .catch(function () {
            if (failed) return;
            failed = true;
            window.location.reload();
          });
      });
    });
  }

  /* --- Account page collections (/me/) -------------------------------------
     Progressive enhancement over plain links and forms: every pager link and
     remove form in a [data-account-section] already works as a full page
     load. With JS, each one re-fetches just its own section
     (?_section=<slug>, answered by core.views.account) and swaps it in, so
     the other two collections and the scroll position stay where they are.

     Each section owns only its own page param (liked_page, ...). A link's
     URL was built when its section was rendered and may carry stale state
     for the OTHER sections, so the next address is always the current one
     with just this section's param taken from the link.

     Any failure falls back to the plain navigation or submit. */
  function initAccountSections() {
    if (!document.querySelector("[data-account-section]")) return;
    if (!window.fetch || !window.URL || !window.DOMParser) return;

    var controllers = {};

    function addressFor(slug, href) {
      var target = new URL(href, location.href);
      var next = new URL(location.href);
      next.hash = "";
      next.searchParams.delete("_section");
      var name = slug + "_page";
      var value = target.searchParams.get(name);
      if (value === null) next.searchParams.delete(name);
      else next.searchParams.set(name, value);
      return next;
    }

    // focusSelectors: candidates, in order, for what to put focus on inside
    // the new section, since the element the reader just used was replaced
    // along with everything else. One that isn't rendered (the pager's page
    // numbers are hidden on a phone) or isn't there (the arrow went disabled
    // on the last page) is skipped; the heading is the last resort.
    //
    // opts (optional): quiet -- don't dim the shelf (a reorder has already
    // drawn the new order); noFocus -- leave focus alone (a pointer drag);
    // done(fresh) -- called with the swapped-in section, or null on failure.
    function load(slug, href, focusSelectors, opts) {
      opts = opts || {};
      var section = document.getElementById(slug);
      if (!section) return;
      var address = addressFor(slug, href);
      var request = new URL(address.href);
      request.searchParams.set("_section", slug);

      var grid = section.querySelector("[data-account-grid]");
      if (grid && !opts.quiet) grid.setAttribute("aria-busy", "true");

      if (controllers[slug]) controllers[slug].abort();
      var controller = window.AbortController ? new AbortController() : null;
      controllers[slug] = controller;

      fetch(request.href, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
        signal: controller ? controller.signal : undefined,
      })
        .then(function (response) {
          if (!response.ok) throw new Error("section fetch failed");
          return response.text();
        })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, "text/html");
          var fresh = doc.getElementById(slug);
          var current = document.getElementById(slug);
          if (!fresh || !current) throw new Error("section missing");
          current.replaceWith(fresh);

          // Same Safari replaceState cap as initLiveFilter: a stale address
          // bar is fine, a thrown error falling into the reload below is not.
          try {
            history.replaceState(null, "", address.pathname + address.search + "#" + slug);
          } catch (e) {}

          var heading = fresh.querySelector("h2");
          if (heading && heading.getBoundingClientRect().top < 0) {
            fresh.scrollIntoView({ block: "start" });
          }

          if (!opts.noFocus) {
            var focusTarget = null;
            for (var i = 0; focusSelectors && i < focusSelectors.length && !focusTarget; i++) {
              var candidate = fresh.querySelector(focusSelectors[i]);
              if (candidate && candidate.getClientRects().length) focusTarget = candidate;
            }
            if (!focusTarget && heading) {
              heading.setAttribute("tabindex", "-1");
              focusTarget = heading;
            }
            if (focusTarget) focusTarget.focus({ preventScroll: true });
          }
          if (opts.done) opts.done(fresh);
        })
        .catch(function (err) {
          if (err && err.name === "AbortError") return;
          if (opts.done) opts.done(null);
          location.href = address.pathname + address.search + "#" + slug;
        });
    }

    function slugOf(el) {
      var section = el.closest("[data-account-section]");
      return section ? section.getAttribute("data-account-section") : null;
    }

    document.addEventListener("click", function (event) {
      var link = event.target.closest && event.target.closest("a[data-account-nav]");
      if (!link) return;
      // Leave new-tab and new-window clicks to the browser.
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      var slug = slugOf(link);
      if (!slug) return;
      event.preventDefault();
      var focusSelectors = [];
      var dir = link.getAttribute("data-account-dir");
      if (dir) focusSelectors.push('a[data-account-dir="' + dir + '"]');
      focusSelectors.push('.account-pager__btn[aria-current="page"]');
      load(slug, link.href, focusSelectors);
    });

    document.addEventListener("submit", function (event) {
      var form = event.target;
      if (!form.matches("form[data-account-remove]")) return;

      var slug = slugOf(form);
      if (!slug) return;
      event.preventDefault();

      // Optimistic: the card goes now; the re-fetch then pulls the next
      // title up into its place, or — when this emptied the page — the
      // server clamps to the page before it.
      var card = form.closest("li");
      if (card) card.hidden = true;

      fetch(form.getAttribute("action"), {
        method: "POST",
        body: new FormData(form),
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      })
        .then(function (response) {
          var type = response.headers.get("Content-Type") || "";
          if (!response.ok || type.indexOf("application/json") === -1) throw new Error("remove failed");
          load(slug, location.href, null);
        })
        .catch(function () {
          if (card) card.hidden = false;
          form.submit();
        });
    });

    /* --- Top: reordering ---------------------------------------------------
       Two ways in, one way out. The rail's arrows (plain forms, which work
       with JS off) step a card one rank; the grip between them -- or, with a
       mouse, the poster itself -- lifts the card to carry it anywhere along
       the row, or onto the pager's arrows to drop it over the page edge.
       Either way the change is posted, then the section is re-fetched like
       any other, and a move that crosses a page edge follows the card there. */
    var status = document.querySelector("[data-account-status]");
    var reordering = false; // a drop is saving; the next lift waits for it

    function announce(title, rank) {
      if (!status) return;
      status.textContent = "";
      status.textContent = t("Moved {title} to rank {rank}", { title: title, rank: rank });
    }

    function pageNumber(slug) {
      var value = parseInt(new URL(location.href).searchParams.get(slug + "_page"), 10);
      return value > 1 ? value : 1;
    }

    function pageHref(slug, number) {
      var next = new URL(location.href);
      if (number > 1) next.searchParams.set(slug + "_page", number);
      else next.searchParams.delete(slug + "_page");
      return next.href;
    }

    function postForm(url, body) {
      return fetch(url, {
        method: "POST",
        body: body,
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      }).then(function (response) {
        var type = response.headers.get("Content-Type") || "";
        if (!response.ok || type.indexOf("application/json") === -1) throw new Error("move failed");
      });
    }

    // After a move: re-fetch the page the card is now on and let its rank
    // figure settle, so the eye finds where it went.
    function settle(slug, page, itemId, focusSelectors, pointer) {
      load(slug, pageHref(slug, page), focusSelectors, {
        quiet: true,
        noFocus: pointer,
        done: function (fresh) {
          reordering = false;
          var card = fresh && fresh.querySelector('[data-item-id="' + itemId + '"]');
          if (card && !reduce) card.classList.add("is-just-moved");
        },
      });
    }

    function shelfCards(grid) {
      return Array.prototype.filter.call(grid.children, function (li) {
        return li.hasAttribute("data-item-id");
      });
    }

    document.addEventListener("submit", function (event) {
      var form = event.target;
      if (!form.matches("form[data-account-move]")) return;
      var card = form.closest("[data-item-id]");
      var grid = card && card.parentElement;
      var slug = slugOf(form);
      if (!grid || !slug || reordering) return;
      event.preventDefault();
      reordering = true;

      var up = form.getAttribute("data-account-move") === "up";
      var cards = shelfCards(grid);
      var page = pageNumber(slug);
      if (up && card === cards[0] && grid.hasAttribute("data-prev-anchor")) page -= 1;
      if (!up && card === cards[cards.length - 1] && grid.hasAttribute("data-next-anchor")) page += 1;
      var itemId = card.getAttribute("data-item-id");
      var rank = parseInt(card.getAttribute("data-rank"), 10) + (up ? -1 : 1);

      postForm(form.getAttribute("action"), new FormData(form))
        .then(function () {
          announce(card.getAttribute("data-title"), rank);
          var mine = '[data-item-id="' + itemId + '"] ';
          settle(slug, page, itemId, [
            mine + '[data-account-move="' + (up ? "up" : "down") + '"] button:not(:disabled)',
            mine + ".account-card__move:not(:disabled)",
          ], false);
        })
        .catch(function () {
          reordering = false;
          form.submit();
        });
    });

    // Carrying a card. Slots are the row's columns, measured once at lift
    // in the shelf's own scroll coordinates: they never move while a card
    // is carried, only which card sits in which, so the hole's place comes
    // from the nearest slot rather than from neighbours mid-slide.
    var drag = null;
    var LIFT_AFTER = 4; // px of travel before a press becomes a drag

    function pad(n) {
      return n < 10 ? "0" + n : String(n);
    }

    function gridLeft(grid) {
      return grid.getBoundingClientRect().left - grid.scrollLeft;
    }

    function renumber() {
      shelfCards(drag.grid).forEach(function (li, i) {
        var figure = li.querySelector(".account-card__rank");
        if (figure) figure.textContent = pad(drag.firstRank + i);
        if (li === drag.card) drag.rank = drag.firstRank + i;
      });
      var lifted = drag.ghost.querySelector(".account-card__rank");
      if (lifted) lifted.textContent = pad(drag.rank);
    }

    // Puts the hole at `index` by moving its neighbours around it -- the
    // hole itself is never detached, or the browser would drop the pointer
    // capture on the press that is carrying it -- and slides each moved
    // neighbour from where it was drawn to its new slot.
    function moveHole(index) {
      var cards = shelfCards(drag.grid);
      var from = cards.indexOf(drag.card);
      if (index === from) return;
      var before = cards.map(function (li) { return li.getBoundingClientRect().left; });
      if (index > from) {
        cards.slice(from + 1, index + 1).forEach(function (li) {
          drag.grid.insertBefore(li, drag.card);
        });
      } else {
        drag.card.after.apply(drag.card, cards.slice(index, from));
      }
      if (!reduce) {
        cards.forEach(function (li, i) {
          if (li === drag.card) return;
          li.style.transition = "none";
          li.style.transform = "";
          var dx = before[i] - li.getBoundingClientRect().left;
          if (!dx) return;
          li.style.transform = "translateX(" + dx + "px)";
          li.getBoundingClientRect(); // commit the start position
          li.style.transition = "transform var(--dur) var(--ease)";
          li.style.transform = "";
        });
      }
      renumber();
    }

    function setOver(target) {
      drag.targets.forEach(function (tgt) {
        tgt.el.classList.toggle("is-drop-over", tgt === target);
      });
      drag.over = target;
      drag.ghost.classList.toggle("is-over-target", !!target);
      var lifted = drag.ghost.querySelector(".account-card__rank");
      if (lifted) lifted.textContent = pad(target ? target.rank : drag.rank);
    }

    function track() {
      var ghostLeft = drag.x - drag.offsetX;
      drag.ghost.style.left = ghostLeft + "px";
      drag.ghost.style.top = drag.y - drag.offsetY + "px";

      var over = null;
      drag.targets.forEach(function (tgt) {
        var r = tgt.el.getBoundingClientRect();
        if (drag.x >= r.left - 8 && drag.x <= r.right + 8 && drag.y >= r.top - 8 && drag.y <= r.bottom + 8) over = tgt;
      });
      setOver(over);
      if (over) return;

      var center = ghostLeft + drag.width / 2 - gridLeft(drag.grid);
      var nearest = 0;
      drag.slots.forEach(function (slot, i) {
        if (Math.abs(slot - center) < Math.abs(drag.slots[nearest] - center)) nearest = i;
      });
      moveHole(nearest);
    }

    // A phone's shelf is wider than the screen: hold a card near either
    // end and the shelf scrolls under it.
    function autoScroll() {
      if (!drag) return;
      var grid = drag.grid;
      if (grid.scrollWidth > grid.clientWidth) {
        var r = grid.getBoundingClientRect();
        var edge = Math.min(64, r.width / 5);
        var step = 0;
        if (drag.x < r.left + edge) step = -Math.ceil((r.left + edge - drag.x) / 4);
        else if (drag.x > r.right - edge) step = Math.ceil((drag.x - (r.right - edge)) / 4);
        if (step) {
          grid.scrollLeft += step;
          track();
        }
      }
      drag.frame = requestAnimationFrame(autoScroll);
    }

    function pagerTargets(section, grid, cards) {
      var targets = [];
      var first = parseInt(cards[0].getAttribute("data-rank"), 10);
      var last = parseInt(cards[cards.length - 1].getAttribute("data-rank"), 10);
      [["prev", "data-prev-anchor", "before", first - 1], ["next", "data-next-anchor", "after", last + 1]].forEach(function (spec) {
        var anchor = grid.getAttribute(spec[1]);
        var el = section.querySelector('.account-pager a[data-account-dir="' + spec[0] + '"]');
        if (!anchor || !el) return;
        var figure = document.createElement("span");
        figure.className = "account-pager__drop mono";
        figure.textContent = pad(spec[3]);
        if (spec[0] === "prev") el.appendChild(figure);
        else el.insertBefore(figure, el.firstChild);
        el.classList.add("is-drop-target");
        targets.push({ el: el, figure: figure, key: spec[2], anchor: anchor, rank: spec[3], dir: spec[0] === "prev" ? -1 : 1 });
      });
      return targets;
    }

    function lift() {
      var card = drag.card;
      var grid = drag.grid;
      var cards = shelfCards(grid);
      var rect = card.getBoundingClientRect();
      var left = gridLeft(grid);

      drag.lifted = true;
      drag.width = rect.width;
      drag.offsetX = drag.startX - rect.left;
      drag.offsetY = drag.startY - rect.top;
      drag.from = cards.indexOf(card);
      drag.firstRank = parseInt(cards[0].getAttribute("data-rank"), 10);
      drag.rank = parseInt(card.getAttribute("data-rank"), 10);
      drag.slots = cards.map(function (li) {
        var r = li.getBoundingClientRect();
        return r.left - left + r.width / 2;
      });

      var ghost = card.cloneNode(true);
      ghost.removeAttribute("data-item-id");
      ghost.setAttribute("aria-hidden", "true");
      ghost.setAttribute("inert", "");
      ghost.classList.add("is-lifted");
      ghost.style.width = rect.width + "px";
      ghost.style.left = rect.left + "px";
      ghost.style.top = rect.top + "px";
      ghost.style.setProperty("--grab-x", drag.offsetX + "px");
      ghost.style.setProperty("--grab-y", drag.offsetY + "px");
      drag.section.appendChild(ghost);
      drag.ghost = ghost;
      ghost.getBoundingClientRect(); // start flat, then tilt up off the row
      ghost.classList.add("is-up");
      if (window.getSelection) window.getSelection().removeAllRanges();

      card.classList.add("is-hole");
      grid.classList.add("is-reordering");
      document.documentElement.classList.add("is-dragging");
      drag.targets = pagerTargets(drag.section, grid, cards);
      drag.frame = requestAnimationFrame(autoScroll);
    }

    function clearDrag() {
      cancelAnimationFrame(drag.frame);
      drag.targets.forEach(function (tgt) {
        tgt.el.classList.remove("is-drop-target", "is-drop-over");
        tgt.figure.remove();
      });
      shelfCards(drag.grid).forEach(function (li) {
        li.style.transition = "";
        li.style.transform = "";
      });
      drag.card.classList.remove("is-hole");
      drag.grid.classList.remove("is-reordering");
      document.documentElement.classList.remove("is-dragging");
      drag.ghost.remove();
    }

    // The lifted card settles into the hole, then everything is put back.
    function land(then) {
      var current = drag;
      var ghost = current.ghost;
      var finish = function () {
        drag = current;
        clearDrag();
        drag = null;
        if (then) then();
      };
      if (reduce || current.over) {
        finish();
        return;
      }
      var r = current.card.getBoundingClientRect();
      ghost.classList.remove("is-up");
      ghost.classList.add("is-landing");
      ghost.style.left = r.left + "px";
      ghost.style.top = r.top + "px";
      var done = false;
      var once = function () {
        if (done) return;
        done = true;
        finish();
      };
      ghost.addEventListener("transitionend", once);
      setTimeout(once, 320);
    }

    function drop(cancelled) {
      var d = drag;
      var title = d.card.getAttribute("data-title");
      var url = d.card.getAttribute("data-place-url");
      var itemId = d.card.getAttribute("data-item-id");
      var csrf = d.card.querySelector("[name='csrfmiddlewaretoken']");
      var body = new FormData();
      if (csrf) body.append("csrfmiddlewaretoken", csrf.value);

      var page = pageNumber(d.slug);
      var rank = d.rank;
      if (cancelled) {
        if (!d.over) moveHole(d.from);
        land(null);
        return;
      }
      if (d.over) {
        body.append(d.over.key, d.over.anchor);
        page += d.over.dir;
        rank = d.over.rank;
        d.card.hidden = true;
      } else {
        var cards = shelfCards(d.grid);
        var at = cards.indexOf(d.card);
        if (at === d.from) {
          land(null);
          return;
        }
        if (at < cards.length - 1) body.append("before", cards[at + 1].getAttribute("data-item-id"));
        else body.append("after", cards[at - 1].getAttribute("data-item-id"));
      }

      reordering = true;
      var slug = d.slug;
      land(null);
      postForm(url, body)
        .then(function () {
          announce(title, rank);
          settle(slug, page, itemId, null, true);
        })
        .catch(function () {
          // Nothing was saved: draw the shelf as the server has it.
          settle(slug, pageNumber(slug), null, null, true);
        });
    }

    // A mouse drag that started on the poster ends in a click on its link;
    // that click is the drop, not a visit.
    function swallowClick() {
      var swallow = function (e) {
        e.preventDefault();
        e.stopPropagation();
      };
      window.addEventListener("click", swallow, true);
      setTimeout(function () {
        window.removeEventListener("click", swallow, true);
      }, 0);
    }

    function onMove(event) {
      if (!drag || event.pointerId !== drag.pointerId) return;
      drag.x = event.clientX;
      drag.y = event.clientY;
      if (!drag.lifted) {
        if (Math.abs(drag.x - drag.startX) < LIFT_AFTER && Math.abs(drag.y - drag.startY) < LIFT_AFTER) return;
        lift();
      }
      event.preventDefault();
      track();
    }

    function onEnd(event) {
      if (!drag || event.pointerId !== drag.pointerId) return;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onEnd);
      window.removeEventListener("pointercancel", onEnd);
      if (!drag.lifted) {
        drag = null;
        return;
      }
      swallowClick();
      drop(event.type === "pointercancel");
    }

    document.addEventListener("pointerdown", function (event) {
      if (drag || reordering || event.button !== 0) return;
      var grip = event.target.closest && event.target.closest("[data-account-grip]");
      var poster = !grip && event.pointerType === "mouse" &&
        event.target.closest && event.target.closest(".account-card.is-ranked .poster-card");
      var handle = grip || poster;
      if (!handle) return;
      var card = handle.closest("[data-item-id]");
      var grid = card && card.parentElement;
      if (!grid || !grid.hasAttribute("data-account-reorder")) return;
      var section = grid.closest("[data-account-section]");

      drag = {
        card: card,
        grid: grid,
        section: section,
        slug: section.getAttribute("data-account-section"),
        pointerId: event.pointerId,
        fromPoster: !!poster,
        startX: event.clientX,
        startY: event.clientY,
        x: event.clientX,
        y: event.clientY,
        lifted: false,
        targets: [],
        over: null,
      };
      if (grip) event.preventDefault(); // no text selection, no scroll start
      try {
        handle.setPointerCapture(event.pointerId);
      } catch (e) {}
      window.addEventListener("pointermove", onMove, { passive: false });
      window.addEventListener("pointerup", onEnd);
      window.addEventListener("pointercancel", onEnd);
    });

    // Escape puts a carried card back where it was picked up.
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !drag || !drag.lifted) return;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onEnd);
      window.removeEventListener("pointercancel", onEnd);
      // The button is still down: the release that follows is no click.
      window.addEventListener("pointerup", swallowClick, { once: true });
      drop(true);
    });

    // A poster is a link and an image: without this the browser starts its
    // own drag of the URL the moment the mouse moves.
    document.addEventListener("dragstart", function (event) {
      if (event.target.closest && event.target.closest("[data-account-reorder] .account-card")) {
        event.preventDefault();
      }
    });
  }

  /* --- Signed-in state freshness ------------------------------------------
     Every page is rendered for one sign-in state (the nav's Sign in chip vs
     the account pill), but a browser can show a page long after rendering
     it: the back/forward cache restores a snapshot on Back, and a phone
     resumes a backgrounded tab as it was. Sign in on one page and return
     to one of those, and it still offers Sign in.

     Each page load records the state it was rendered for; a page coming
     back into view -- restored from that cache, re-focused, or told by
     another tab -- reloads itself if the latest recorded state disagrees.
     A normal page load just records, so this can't loop. */
  function initSignedInFreshness() {
    var KEY = "theoria-signed-in";
    var mine = document.body.getAttribute("data-signed-in");
    if (mine === null) return;

    try {
      localStorage.setItem(KEY, mine);
    } catch (e) {
      return;
    }

    function check() {
      var latest;
      try {
        latest = localStorage.getItem(KEY);
      } catch (e) {
        return;
      }
      if (latest !== null && latest !== mine) window.location.reload();
    }

    window.addEventListener("pageshow", function (event) {
      if (event.persisted) check();
    });
    window.addEventListener("storage", function (event) {
      if (event.key === KEY) check();
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") check();
    });
  }

  /* --- Sign-out confirmation ---------------------------------------------
     Every [data-signout-form] asks first. Yes submits the form that asked
     (form.submit() skips this listener, so it can't loop); No, Escape or a
     click on the backdrop closes the dialog and focus returns to the
     button that opened it. A browser without <dialog> just signs out. */
  function initSignOutConfirm() {
    var dialog = document.getElementById("signout-dialog");
    if (!dialog || typeof dialog.showModal !== "function") return;

    var pending = null;
    var yes = dialog.querySelector("[data-signout-confirm]");

    document.querySelectorAll("form[data-signout-form]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        pending = form;
        yes.disabled = false;
        dialog.showModal();
      });
    });

    dialog.querySelector("[data-signout-cancel]").addEventListener("click", function () {
      dialog.close();
    });

    yes.addEventListener("click", function () {
      if (!pending) return;
      yes.disabled = true;
      pending.submit();
    });

    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
  }

  function init() {
    syncThemeColor();
    // Covers all three ways the theme moves — the header toggle, an OS change
    // with no explicit choice saved, and a bfcache restore under a theme picked
    // on a later page — because each of them already dispatches this event.
    document.addEventListener("themechange", syncThemeColor);
    initMeters();
    initCounters();
    initThemeToggle();
    initNavToggle();
    initAssistant();
    initPagedSections();
    initSeasonPicker();
    initFilterMenu();
    initLiveFilter();
    initBioToggle();
    initVideoEmbeds();
    initCodeInput();
    initAuthFormSubmitState();
    initInlineValidation();
    initAccountMenu();
    initLangMenu();
    initLangSwitch();
    initLanguageFreshness();
    initSignOutConfirm();
    initCollectionActions();
    initAccountSections();
    initSignedInFreshness();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
