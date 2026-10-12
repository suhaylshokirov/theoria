// Theoria — the cinema layer. Loaded on every page from base.html, after
// theoria.js. Everything here is progressive enhancement: with JS off the
// home reel shows its first film, the poster wall and rails are plain rows of
// links, the decade chart is a table, and the header search is a plain form
// that submits to the Browse page.
//
//   1. initHeaderScroll() — firms up the floating header once the page scrolls,
//                           hides it on the way down, brings it back on the way up.
//   2. Room light         — samples the colour of the film on screen into
//                           --glow, which tints .ambient and the score card.
//   3. initReel()         — the home "Explore" reel.
//   4. initTilt()         — posters tilt toward the pointer and catch a light.
//   5. initRails()        — drag-to-scroll strips with arrow buttons.
//   6. initDecades()      — draws the home decade chart from its table.
//   7. initHeaderSearch() — the header search field and its results dropdown.
//   8. initPosterMorph()  — a clicked poster morphs into the film page's.
(function () {
  "use strict";

  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* --- 1. Header ------------------------------------------------------------- */

  /* Two states ride on the scroll position. `is-scrolled` firms the bar up
     once the page has moved at all. `is-hidden` slides it out of view while
     the reader scrolls down and brings it straight back on any scroll up, so
     it costs no reading space but is one flick away. It never hides near the
     top, and it stays put while something inside it is in use: the mobile
     menu or an account/language popover is open (aria-expanded), or a field
     or a keyboard-focused control holds focus (:focus-visible, so a mouse
     click on the theme toggle doesn't pin it forever). */

  function initHeaderScroll() {
    var header = document.querySelector(".site-header");
    if (!header) return;
    var HIDE_AFTER = 120; // px from the top before hiding is allowed
    var DELTA = 6; // px of travel that counts as a deliberate direction
    var scrolled = false;
    var hidden = false;
    var lastY = Math.max(0, window.scrollY);
    var queued = false;

    function setHidden(next) {
      if (next === hidden) return;
      hidden = next;
      header.classList.toggle("is-hidden", hidden);
    }

    function pinned() {
      return !!header.querySelector('[aria-expanded="true"], :focus-visible');
    }

    function update() {
      queued = false;
      // Clamp so iOS rubber-banding past either end reads as no movement
      // rather than a reversal that would flash the bar.
      var max = Math.max(
        0,
        document.documentElement.scrollHeight - window.innerHeight
      );
      var y = Math.min(Math.max(0, window.scrollY), max);

      var isScrolled = y > 40;
      if (isScrolled !== scrolled) {
        scrolled = isScrolled;
        header.classList.toggle("is-scrolled", scrolled);
      }

      if (y <= HIDE_AFTER) {
        setHidden(false);
        lastY = y;
        return;
      }
      var dy = y - lastY;
      if (Math.abs(dy) < DELTA) return;
      lastY = y;
      if (dy < 0) setHidden(false);
      else if (!pinned()) setHidden(true);
    }

    function request() {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(update);
    }

    update();
    window.addEventListener("scroll", request, { passive: true });
    // Tabbing into a hidden bar brings it back rather than leaving focus
    // on something off screen.
    header.addEventListener("focusin", function () {
      setHidden(false);
    });
    // A restored back/forward-cache page can come back mid-scroll.
    window.addEventListener("pageshow", update);
  }

  /* --- 2. Room light ------------------------------------------------------------
     Draws the image small onto a canvas and averages its pixels, weighting
     saturated, bright pixels most, so a mostly-black frame with a burst of
     orange reads as orange rather than as grey. The image must carry
     crossorigin="anonymous" (image.tmdb.org allows it); if the canvas is
     tainted anyway, getImageData throws and the light keeps its colour. */

  var glowCache = {};

  function sampleGlow(img) {
    var src = img.currentSrc || img.src;
    if (glowCache[src]) return glowCache[src];
    try {
      var canvas = document.createElement("canvas");
      canvas.width = 32;
      canvas.height = 18;
      var ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, 32, 18);
      var d = ctx.getImageData(0, 0, 32, 18).data;
      var r = 0, g = 0, b = 0, w = 0;
      for (var i = 0; i < d.length; i += 4) {
        var max = Math.max(d[i], d[i + 1], d[i + 2]);
        var min = Math.min(d[i], d[i + 1], d[i + 2]);
        var weight = ((max - min) / 255) * (max / 255) + 0.02;
        r += d[i] * weight;
        g += d[i + 1] * weight;
        b += d[i + 2] * weight;
        w += weight;
      }
      var boost = function (v) {
        return Math.min(255, Math.round((v / w) * 1.25));
      };
      glowCache[src] = boost(r) + " " + boost(g) + " " + boost(b);
      return glowCache[src];
    } catch (e) {
      return null;
    }
  }

  function lightRoomFrom(img) {
    if (!img) return;
    function apply() {
      var value = sampleGlow(img);
      if (value) document.documentElement.style.setProperty("--glow", value);
    }
    if (img.complete && img.naturalWidth) apply();
    else img.addEventListener("load", apply, { once: true });
  }

  function initRoomLight() {
    // The film page names its own source; the home reel lights the room
    // itself as it advances (see initReel).
    lightRoomFrom(document.querySelector("[data-glow-source]"));
  }

  /* --- 3. The reel ---------------------------------------------------------------- */

  var REEL_DURATION = 8000;

  // Wraps each word of a title in two spans so it can rise out of its own
  // mask (home.css .reel__title .w). Done once per title; the text itself is
  // unchanged, so screen readers and copy-paste see the plain title.
  function splitWords(el) {
    if (!el || el.getAttribute("data-split")) return;
    var words = el.textContent.trim().split(/\s+/);
    el.textContent = "";
    words.forEach(function (word, i) {
      var outer = document.createElement("span");
      outer.className = "w";
      var inner = document.createElement("span");
      inner.style.setProperty("--i", i);
      inner.textContent = word;
      outer.appendChild(inner);
      el.appendChild(outer);
      if (i < words.length - 1) el.appendChild(document.createTextNode(" "));
    });
    el.setAttribute("data-split", "1");
  }

  // Restarts a CSS animation on an element that already ran it.
  function replay(el) {
    if (!el) return;
    el.style.animation = "none";
    void el.offsetWidth;
    el.style.animation = "";
  }

  function initReel() {
    var reel = document.querySelector("[data-reel]");
    if (!reel) return;
    var images = reel.querySelectorAll("[data-reel-image]");
    var slides = reel.querySelectorAll("[data-reel-slide]");
    var tabs = reel.querySelectorAll("[data-reel-tab]");
    var current = -1;
    var timer = null;

    reel.style.setProperty("--reel-dur", REEL_DURATION + "ms");

    function show(index) {
      if (index === current) return;
      current = index;
      images.forEach(function (img, i) {
        img.classList.toggle("is-on", i === index);
        if (i === index) replay(img);
      });
      slides.forEach(function (slide, i) {
        slide.hidden = i !== index;
        if (i === index) {
          var title = slide.querySelector("[data-reel-title]");
          if (!reduce) splitWords(title);
          replay(slide.querySelector(".reel__tagline"));
        }
      });
      tabs.forEach(function (tab, i) {
        tab.classList.toggle("is-on", i === index);
        tab.classList.toggle("is-done", i < index);
        tab.setAttribute("aria-selected", i === index ? "true" : "false");
        replay(tab.querySelector(".reel__bar i"));
      });
      lightRoomFrom(images[index]);
      schedule();
    }

    function schedule() {
      clearTimeout(timer);
      if (reduce || document.hidden || images.length < 2) return;
      timer = setTimeout(function () {
        show((current + 1) % images.length);
      }, REEL_DURATION);
    }

    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () {
        show(i);
      });
    });

    // A backgrounded tab shouldn't keep advancing; pick up where it was.
    document.addEventListener("visibilitychange", schedule);

    // Swipe sideways on a touch screen for the next or previous film. The
    // reel is touch-action: pan-y (home.css), so a vertical drag still
    // scrolls the page and only a clearly horizontal one counts.
    var swipe = null;
    reel.addEventListener("pointerdown", function (e) {
      if (e.pointerType === "mouse" || images.length < 2) return;
      swipe = { x: e.clientX, y: e.clientY };
    });
    reel.addEventListener("pointerup", function (e) {
      if (!swipe) return;
      var dx = e.clientX - swipe.x;
      var dy = e.clientY - swipe.y;
      swipe = null;
      if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      var n = images.length;
      show((current + (dx < 0 ? 1 : -1) + n) % n);
    });
    reel.addEventListener("pointercancel", function () { swipe = null; });

    show(0);
  }

  /* --- 4. Tilt ---------------------------------------------------------------------- */

  function initTilt() {
    if (reduce || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;

    document.addEventListener("pointermove", function (e) {
      var el = e.target.closest && e.target.closest("[data-tilt]");
      if (!el) return;
      var r = el.getBoundingClientRect();
      var x = (e.clientX - r.left) / r.width;
      var y = (e.clientY - r.top) / r.height;
      el.style.transform =
        "perspective(700px) rotateY(" + (x - 0.5) * 14 + "deg) rotateX(" +
        (0.5 - y) * 14 + "deg) scale(1.06)";
      el.style.setProperty("--gx", x * 100 + "%");
      el.style.setProperty("--gy", y * 100 + "%");
    });

    document.addEventListener("pointerout", function (e) {
      var el = e.target.closest && e.target.closest("[data-tilt]");
      if (el && !el.contains(e.relatedTarget)) el.style.transform = "";
    });
  }

  /* --- 5. Rails ----------------------------------------------------------------------- */

  function initRails() {
    document.querySelectorAll("[data-rail]").forEach(function (rail) {
      var name = rail.getAttribute("data-rail");
      var controls = document.querySelector('[data-rail-controls="' + name + '"]');
      if (controls) {
        var by = function (dir) {
          rail.scrollBy({ left: dir * rail.clientWidth * 0.8, behavior: reduce ? "auto" : "smooth" });
        };
        controls.querySelector("[data-rail-prev]").addEventListener("click", function () { by(-1); });
        controls.querySelector("[data-rail-next]").addEventListener("click", function () { by(1); });
      }

      // Mouse drag. Touch already scrolls natively, so it is left alone.
      var drag = null;
      rail.addEventListener("pointerdown", function (e) {
        if (e.pointerType !== "mouse" || e.button !== 0) return;
        drag = { x: e.clientX, left: rail.scrollLeft, moved: false };
      });
      window.addEventListener("pointermove", function (e) {
        if (!drag) return;
        var dx = e.clientX - drag.x;
        if (Math.abs(dx) > 4) {
          drag.moved = true;
          rail.classList.add("is-dragging");
        }
        if (drag.moved) rail.scrollLeft = drag.left - dx;
      });
      window.addEventListener("pointerup", function () {
        if (!drag) return;
        var moved = drag.moved;
        drag = null;
        rail.classList.remove("is-dragging");
        // A drag that ends over a poster must not also open it.
        if (moved) {
          rail.addEventListener("click", function swallow(ev) {
            ev.preventDefault();
            ev.stopPropagation();
          }, { capture: true, once: true });
        }
      });
      // Stop the browser's own image drag from hijacking the gesture.
      rail.addEventListener("dragstart", function (e) { e.preventDefault(); });
    });
  }

  /* --- 6. Decades chart -------------------------------------------------------------------
     Reads the rows of the table it replaces, so the chart and the record can
     never disagree. Bars (film counts) and the line (average rating) share
     the x axis; each has its own y scale, labelled on its own side. */

  var SVG_NS = "http://www.w3.org/2000/svg";

  function svgEl(name, attrs, text) {
    var el = document.createElementNS(SVG_NS, name);
    Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    if (text != null) el.textContent = text;
    return el;
  }

  function fill(template, vars) {
    return Object.keys(vars).reduce(function (out, k) {
      return out.split("{" + k + "}").join(vars[k]);
    }, template);
  }

  function initDecades() {
    var box = document.querySelector("[data-decades]");
    if (!box) return;
    var svg = box.querySelector("[data-decades-chart]");
    var tip = box.querySelector("[data-decades-tip]");
    var rows = Array.prototype.map.call(box.querySelectorAll("tr[data-decade]"), function (tr) {
      var rating = tr.getAttribute("data-rating");
      return {
        decade: tr.getAttribute("data-decade"),
        films: parseInt(tr.getAttribute("data-films"), 10),
        rating: rating ? parseFloat(rating) : null,
        best: tr.hasAttribute("data-best"),
      };
    });
    if (!rows.length) return;

    var decadeLabel = box.getAttribute("data-label-decade") || "{decade}s";
    var tipLabel = box.getAttribute("data-label-tip") || "{decade}s · {films} · {rating}";
    var maxFilms = Math.ceil(Math.max.apply(null, rows.map(function (r) { return r.films; })) / 50) * 50 || 50;
    var ratings = rows.filter(function (r) { return r.rating != null; }).map(function (r) { return r.rating; });
    var rMin = Math.floor(Math.min.apply(null, ratings.concat([10])));
    var rMax = Math.ceil(Math.max.apply(null, ratings.concat([0])));
    if (rMax <= rMin) rMax = rMin + 1;
    var step = maxFilms > 200 ? 100 : 50;
    var active = null;

    function showTip(row, bar) {
      if (active) active.classList.remove("is-active");
      active = bar;
      bar.classList.add("is-active");
      tip.textContent = fill(tipLabel, {
        decade: row.decade,
        films: row.films,
        rating: row.rating != null ? row.rating.toFixed(2) : "—",
      });
      // Centred over the bar but kept inside the box, which clips sideways
      // (home.css): over the first or last decade of a phone-width chart the
      // tip would otherwise lose its ends.
      // Measured in screen px, positioned in the box's own px: they differ
      // by the TV zoom (home.css), so divide it back out.
      var z = scale();
      var br = bar.getBoundingClientRect(), cr = box.getBoundingClientRect();
      var half = tip.offsetWidth / 2;
      var x = (br.left - cr.left + br.width / 2) / z;
      tip.style.left = Math.min(Math.max(x, half + 4), box.offsetWidth - half - 4) + "px";
      tip.style.top = (br.top - cr.top) / z + "px";
      tip.style.opacity = 1;
    }

    function hideTip() {
      if (active) active.classList.remove("is-active");
      active = null;
      tip.style.opacity = 0;
    }

    // Screen px per CSS px: 1, except under the home page's TV zoom, where
    // getBoundingClientRect() reports the zoomed size.
    function scale() {
      return box.offsetWidth ? box.getBoundingClientRect().width / box.offsetWidth : 1;
    }

    // The chart's width in its own CSS px.
    function chartWidth() {
      return Math.round(svg.getBoundingClientRect().width / scale());
    }

    // Drawn at the size it is shown, in CSS px, so the 12px labels stay 12px
    // on a phone rather than shrinking with a fixed 1000-unit drawing (and
    // grow with the page on a TV). Returns the width it drew at.
    function draw() {
      var W = chartWidth() || 1000;
      var narrow = W < 560;
      // Wide keeps the 1000 x 380 proportions; narrow stands taller, so the
      // bars still have height to compare by.
      var H = narrow ? Math.round(Math.min(Math.max(W * 0.86, 240), 340)) : Math.round(W * 0.38);
      var m = narrow ? { l: 36, r: 30, t: 26, b: 32 } : { l: 48, r: 48, t: 28, b: 40 };
      var bw = (W - m.l - m.r) / rows.length;
      var yFilms = function (v) { return H - m.b - (v / maxFilms) * (H - m.t - m.b); };
      var yRating = function (v) { return H - m.b - ((v - rMin) / (rMax - rMin)) * (H - m.t - m.b); };

      svg.textContent = "";
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);

      for (var t = 0; t <= maxFilms; t += step) {
        svg.appendChild(svgEl("line", { "class": "grid", x1: m.l, x2: W - m.r, y1: yFilms(t), y2: yFilms(t) }));
        svg.appendChild(svgEl("text", { x: m.l - (narrow ? 8 : 10), y: yFilms(t) + 4, "text-anchor": "end" }, t));
      }
      for (var r = rMin; r <= rMax; r++) {
        svg.appendChild(svgEl("text", { x: W - m.r + (narrow ? 6 : 10), y: yRating(r) + 4 }, r.toFixed(1)));
      }

      var points = [];
      rows.forEach(function (row, i) {
        var x = m.l + i * bw + bw * 0.18, w = bw * 0.64;
        var bar = svgEl("rect", {
          "class": "bar" + (row.best ? " is-best" : ""),
          x: x, y: yFilms(row.films), width: w, height: H - m.b - yFilms(row.films), rx: narrow ? 4 : 6,
          style: "--i:" + i,
        });
        // The whole column answers, not just the bar: a 30px bar is a poor
        // target for a finger.
        var hit = svgEl("rect", { "class": "hit", x: m.l + i * bw, y: m.t, width: bw, height: H - m.t - m.b });
        [hit, bar].forEach(function (el) {
          el.addEventListener("pointerenter", function () { showTip(row, bar); });
          el.addEventListener("pointerleave", function (e) {
            // A finger lifting is a pointerleave too; a tapped tip stays
            // until the next tap elsewhere.
            if (e.pointerType === "mouse" && !bar.contains(e.relatedTarget) && e.relatedTarget !== hit) hideTip();
          });
        });
        svg.appendChild(hit);
        svg.appendChild(bar);
        // "1970s" in Uzbek is "1970-yillar", wider than a phone's column;
        // the bare year says the same under a decades chart.
        svg.appendChild(svgEl("text", { x: x + w / 2, y: H - m.b + (narrow ? 20 : 24), "text-anchor": "middle" },
          narrow ? row.decade : fill(decadeLabel, { decade: row.decade })));
        if (row.rating != null) points.push({ x: m.l + i * bw + bw / 2, y: yRating(row.rating), rating: row.rating });
      });

      if (points.length > 1) {
        svg.appendChild(svgEl("path", {
          "class": "line",
          d: "M" + points.map(function (p) { return p.x + "," + p.y; }).join(" L"),
        }));
      }
      points.forEach(function (p) {
        svg.appendChild(svgEl("circle", { "class": "pt", cx: p.x, cy: p.y, r: narrow ? 4 : 5 }));
        svg.appendChild(svgEl("text", { "class": "val", x: p.x, y: p.y - (narrow ? 11 : 14), "text-anchor": "middle" }, p.rating.toFixed(2)));
      });
      return W;
    }

    // Shown first, so the chart has a width to be drawn at.
    box.classList.add("is-drawn");
    var drawnAt = draw();

    // A tap anywhere off the chart puts a touch-opened tip away.
    document.addEventListener("pointerdown", function (e) {
      if (active && !svg.contains(e.target)) hideTip();
    });

    // A width change (rotation, a resized window) redraws, without the draw-in.
    if ("ResizeObserver" in window) {
      new ResizeObserver(function () {
        var w = chartWidth();
        if (w && Math.abs(w - drawnAt) > 1) {
          hideTip();
          drawnAt = draw();
        }
      }).observe(box);
    }

    // Draw-in once, as it arrives. A chart already on screen at load just
    // stays drawn, rather than vanishing to replay.
    if (reduce || !("IntersectionObserver" in window)) return;
    var rect = svg.getBoundingClientRect();
    if (rect.top < window.innerHeight) return;
    svg.style.visibility = "hidden";
    var io = new IntersectionObserver(function (entries) {
      if (!entries[0].isIntersecting) return;
      svg.style.visibility = "";
      svg.classList.add("is-animating");
      io.disconnect();
    }, { threshold: 0.35 });
    io.observe(svg);
  }

  /* --- 7. Header search ------------------------------------------------------------------------
     The field lives in the header bar and the matches list in a dropdown
     right under it -- no overlay. Where the bar is too tight for the field
     (CSS, up to 1240px) a magnifier button stands in and opens the same
     field as a row under the bar. Enter follows an option only once the
     reader has arrowed to it; otherwise the form submits to the Browse page. */

  function initHeaderSearch() {
    var form = document.querySelector("[data-header-search]");
    if (!form) return;
    var input = form.querySelector("input");
    var list = form.querySelector(".header-search__list");
    var toggle = document.querySelector("[data-search-toggle]");
    var endpoint = form.getAttribute("data-endpoint");
    var kindLabel = {
      movie: form.getAttribute("data-label-movie"),
      series: form.getAttribute("data-label-series"),
    };
    var items = [];
    var selected = -1;
    var pending = null;
    var debounce = null;

    function setOpen(open) {
      list.hidden = !open;
      input.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open) input.removeAttribute("aria-activedescendant");
    }

    function note(text) {
      list.innerHTML = "";
      var p = document.createElement("p");
      p.className = "header-search__note";
      p.textContent = text;
      list.appendChild(p);
      items = [];
      selected = -1;
      setOpen(true);
    }

    function select(i) {
      selected = i;
      items.forEach(function (el, k) {
        el.setAttribute("aria-selected", k === i ? "true" : "false");
        if (k === i) {
          el.scrollIntoView({ block: "nearest" });
          input.setAttribute("aria-activedescendant", el.id);
        }
      });
      if (i < 0) input.removeAttribute("aria-activedescendant");
    }

    function render(results) {
      if (!results.length) {
        note(form.getAttribute("data-empty"));
        return;
      }
      list.innerHTML = "";
      items = results.map(function (r, i) {
        var a = document.createElement("a");
        a.className = "header-search__item";
        a.href = r.url;
        a.setAttribute("role", "option");
        a.setAttribute("aria-selected", "false");
        a.id = "header-search-opt-" + i;
        var img;
        if (r.poster) {
          img = document.createElement("img");
          img.src = r.poster;
          img.alt = "";
        } else {
          img = document.createElement("span");
          img.className = "header-search__ph";
        }
        var text = document.createElement("span");
        var title = document.createElement("b");
        title.textContent = r.title;
        var sub = document.createElement("small");
        sub.textContent = [kindLabel[r.kind], r.year].filter(Boolean).join(" · ");
        text.appendChild(title);
        text.appendChild(sub);
        var rating = document.createElement("small");
        rating.textContent = r.rating != null ? r.rating.toFixed(1) : "";
        a.appendChild(img);
        a.appendChild(text);
        a.appendChild(rating);
        a.addEventListener("pointerenter", function () { select(i); });
        list.appendChild(a);
        return a;
      });
      selected = -1;
      setOpen(true);
    }

    function search() {
      var q = input.value.trim();
      if (pending) pending.abort();
      if (!q) {
        setOpen(false);
        return;
      }
      if (q.length < 2) {
        note(form.getAttribute("data-hint"));
        return;
      }
      pending = window.AbortController ? new AbortController() : null;
      fetch(endpoint + "?q=" + encodeURIComponent(q), {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: pending ? pending.signal : undefined,
      })
        .then(function (res) { return res.ok ? res.json() : { results: [] }; })
        .then(function (data) { render(data.results || []); })
        .catch(function (err) {
          if (err && err.name === "AbortError") return;
          note(form.getAttribute("data-empty"));
        });
    }

    function closeRow() {
      if (!toggle) return;
      form.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
    }

    if (toggle) {
      toggle.addEventListener("click", function () {
        var open = !form.classList.contains("is-open");
        form.classList.toggle("is-open", open);
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) input.focus();
        else setOpen(false);
      });
    }

    input.addEventListener("input", function () {
      clearTimeout(debounce);
      debounce = setTimeout(search, 160);
    });

    // Back to a field that already holds a search: show its matches again.
    input.addEventListener("focus", function () {
      if (input.value.trim() && list.children.length) setOpen(true);
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        if (!list.hidden) {
          e.preventDefault();
          setOpen(false);
        } else if (form.classList.contains("is-open")) {
          e.preventDefault();
          closeRow();
          if (toggle) toggle.focus();
        }
      } else if (e.key === "ArrowDown" && items.length) {
        e.preventDefault();
        if (list.hidden) setOpen(true);
        select(selected + 1 >= items.length ? 0 : selected + 1);
      } else if (e.key === "ArrowUp" && items.length) {
        e.preventDefault();
        select(selected <= 0 ? items.length - 1 : selected - 1);
      } else if (e.key === "Enter" && !list.hidden && items[selected]) {
        e.preventDefault();
        items[selected].click();
      }
    });

    // Clicking anywhere else puts the list away; the field keeps its text.
    document.addEventListener("click", function (e) {
      if (!form.contains(e.target) && !(toggle && toggle.contains(e.target))) setOpen(false);
    });
  }

  /* --- 8. Poster morph ---------------------------------------------------------------------------
     The film and show pages' poster carries view-transition-name: film-poster
     (film.css). Naming the clicked poster the same on the way out lets a
     cross-document view transition morph one into the other. Only one element
     per page may hold a name, so the current page's own film poster, if any,
     gives it up first. Browsers without cross-document view transitions just
     navigate. */

  var FILM_PATH = /^\/(?:movies|tv)\/[^/]+\/$/;

  function initPosterMorph() {
    if (reduce || !("PageRevealEvent" in window)) return;

    document.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
      var link = e.target.closest && e.target.closest("a[href]");
      if (!link || link.origin !== location.origin || !FILM_PATH.test(link.pathname)) return;
      if (link.pathname === location.pathname) return;
      var img = link.querySelector("img");
      if (!img) return;
      document.querySelectorAll(".film-poster").forEach(function (el) {
        el.style.viewTransitionName = "none";
      });
      img.style.viewTransitionName = "film-poster";
    });

    // Coming back via the back/forward cache restores the page as it was
    // left, names included; clear them so the next click starts clean.
    window.addEventListener("pageshow", function (e) {
      if (!e.persisted) return;
      document.querySelectorAll('[style*="view-transition-name"]').forEach(function (el) {
        el.style.viewTransitionName = "";
      });
    });
  }

  function init() {
    initHeaderScroll();
    initRoomLight();
    initReel();
    initTilt();
    initRails();
    initDecades();
    initHeaderSearch();
    initPosterMorph();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
