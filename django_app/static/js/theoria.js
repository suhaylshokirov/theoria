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
     same posture initPagedSection() takes for cast/crew paging. */

  function initSeasonPicker() {
    var picker = document.querySelector("[data-season-picker]");
    if (!picker) return;
    var panels = Array.prototype.slice.call(
      document.querySelectorAll("[data-season-panel]")
    );
    if (!panels.length) return;

    function show(value) {
      panels.forEach(function (panel) {
        panel.classList.toggle(
          "is-active",
          panel.getAttribute("data-season-panel") === value
        );
      });
    }

    picker.addEventListener("change", function (e) {
      if (e.target.name !== "season") return;
      show(e.target.value);
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
    var messages = root.querySelector("[data-ai-messages]");
    var form = root.querySelector("[data-ai-form]");
    var input = root.querySelector("[data-ai-input]");
    var send = form.querySelector("button[type='submit']");
    var actions = root.querySelectorAll("[data-ai-prompt]");
    var chatEndpoint = root.getAttribute("data-ai-chat-endpoint");
    var feedbackEndpoint = root.getAttribute("data-ai-feedback-endpoint");
    var authenticated = root.getAttribute("data-ai-authenticated") === "true";
    var csrf = form.querySelector("[name='csrfmiddlewaretoken']");
    var pending = false;
    var promptRequests = {
      tonight: "Pick a movie for tonight",
      mood: "Find a movie for my mood",
      surprise: "Surprise me with a movie"
    };

    function setOpen(open) {
      panel.hidden = !open;
      trigger.setAttribute("aria-expanded", open ? "true" : "false");
      trigger.setAttribute("aria-label", t(open ? "Close film guide" : "Open film guide"));
      if (open) {
        window.setTimeout(function () { input.focus(); }, 0);
      } else {
        trigger.focus();
      }
    }

    function addMessage(text, role, extraClass) {
      var message = document.createElement("div");
      message.className = "ai-message ai-message-" + role;
      if (extraClass) message.classList.add(extraClass);
      var paragraph = document.createElement("p");
      paragraph.textContent = text;
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

    function addRecommendations(recommendations) {
      recommendations.forEach(function (recommendation) {
        var card = document.createElement("article");
        card.className = "ai-recommendation";
        var title = document.createElement(recommendation.url ? "a" : "span");
        title.className = "ai-recommendation-title";
        title.textContent = recommendation.title;
        if (recommendation.url) title.href = recommendation.url;
        card.appendChild(title);

        var status = document.createElement("p");
        status.className = "ai-recommendation-status";
        status.textContent = recommendation.status === "on_list" ? "On your Watch later list" : "New pick";
        card.appendChild(status);

        var reason = document.createElement("p");
        reason.className = "ai-recommendation-reason";
        reason.textContent = recommendation.reason;
        card.appendChild(reason);

        var feedback = document.createElement("div");
        feedback.className = "ai-recommendation-feedback";
        [
          { action: "watched", label: "Already watched" },
          { action: "not_interested", label: "Not for me" }
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
        card.appendChild(feedback);
        messages.appendChild(card);
      });
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
      var thinking = addMessage("Finding a few good picks...", "assistant", "ai-message-thinking");

      try {
        var result = await requestJson(chatEndpoint, { message: prompt });
        thinking.remove();
        if (!result.ok) {
          addMessage(result.data.error || "The guide could not answer just now. Please try again.", "assistant");
          return;
        }
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
      setOpen(panel.hidden);
    });

    close.addEventListener("click", function () {
      setOpen(false);
    });

    actions.forEach(function (action) {
      action.addEventListener("click", function () {
        var promptKey = action.getAttribute("data-ai-prompt");
        respond(promptRequests[promptKey] || promptKey, action.textContent.trim());
      });
    });

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
      panel.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      trigger.setAttribute("aria-label", t("Open film guide"));
    });
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
      var canHover = window.matchMedia("(hover: hover) and (pointer: fine) and (min-width: 681px)");

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

    // focusSelector: what to put focus on inside the new section, since the
    // element the reader just used was replaced along with everything else.
    function load(slug, href, focusSelector) {
      var section = document.getElementById(slug);
      if (!section) return;
      var address = addressFor(slug, href);
      var request = new URL(address.href);
      request.searchParams.set("_section", slug);

      var grid = section.querySelector("[data-account-grid]");
      if (grid) grid.setAttribute("aria-busy", "true");

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

          var focusTarget = focusSelector && fresh.querySelector(focusSelector);
          if (!focusTarget && heading) {
            heading.setAttribute("tabindex", "-1");
            focusTarget = heading;
          }
          if (focusTarget) focusTarget.focus({ preventScroll: true });
        })
        .catch(function (err) {
          if (err && err.name === "AbortError") return;
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
      load(slug, link.href, '.account-pager__btn[aria-current="page"]');
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
