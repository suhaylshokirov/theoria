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
      btn.setAttribute("aria-label", "Switch to " + next + " theme");
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
      btn.textContent = collapsed ? "See more" : "See less";
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
      iframe.title = play.getAttribute("data-video-name") || "Video";
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
      "Filter";

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

  /* --- Verify-code auto-advance --------------------------------------------
     The code field (accounts/verify.html) is one input, not six boxes, so
     autocomplete="one-time-code" and paste both work -- see the auth design
     notes. This only adds the JS-only conveniences on top of a control that
     already works without it: strip anything typed that isn't a digit, and
     submit automatically once six are in, so the reader never has to find
     the button. The resend button gets a live countdown mirroring the
     server's 60-second cooldown (accounts/codes.py's RESEND_COOLDOWN); with
     JS off the button is simply always enabled, and the server-side cooldown
     still refuses an early click with its own message. */

  function initCodeInput() {
    var card = document.querySelector("[data-verify-form]");
    if (!card) return;

    var input = card.querySelector(".code-input");
    if (input) {
      input.addEventListener("input", function () {
        var digits = input.value.replace(/\D/g, "").slice(0, 6);
        input.value = digits;
        if (digits.length === 6 && input.form) {
          input.form.submit();
        }
      });
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
          resendBtn.textContent = label + " (" + seconds + "s)";
        }
      }, 1000);
    }
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
    initPagedSections();
    initFilterMenu();
    initLiveFilter();
    initBioToggle();
    initVideoEmbeds();
    initCodeInput();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
