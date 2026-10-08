// Theoria — the cinema layer. Loaded on every page from base.html, after
// theoria.js. Everything here is progressive enhancement: with JS off the
// home reel shows its first film, the poster wall and rails are plain rows of
// links, the decade chart is a table, and the header search button is a link
// to the Movies index.
//
//   1. initHeaderScroll() — firms up the floating header once the page scrolls.
//   2. Room light         — samples the colour of the film on screen into
//                           --glow, which tints .ambient and the score card.
//   3. initReel()         — the home "Now showing" reel.
//   4. initTilt()         — posters tilt toward the pointer and catch a light.
//   5. initRails()        — drag-to-scroll strips with arrow buttons.
//   6. initDecades()      — draws the home decade chart from its table.
//   7. initPalette()      — the Ctrl+K search palette.
//   8. initPosterMorph()  — a clicked poster morphs into the film page's.
(function () {
  "use strict";

  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* --- 1. Header ------------------------------------------------------------- */

  function initHeaderScroll() {
    var header = document.querySelector(".site-header");
    if (!header) return;
    var on = false;
    function update() {
      var next = window.scrollY > 40;
      if (next !== on) {
        on = next;
        header.classList.toggle("is-scrolled", on);
      }
    }
    update();
    window.addEventListener("scroll", update, { passive: true });
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
    var W = 1000, H = 380, m = { l: 48, r: 48, t: 28, b: 40 };
    var maxFilms = Math.ceil(Math.max.apply(null, rows.map(function (r) { return r.films; })) / 50) * 50 || 50;
    var ratings = rows.filter(function (r) { return r.rating != null; }).map(function (r) { return r.rating; });
    var rMin = Math.floor(Math.min.apply(null, ratings.concat([10])));
    var rMax = Math.ceil(Math.max.apply(null, ratings.concat([0])));
    if (rMax <= rMin) rMax = rMin + 1;
    var bw = (W - m.l - m.r) / rows.length;
    var yFilms = function (v) { return H - m.b - (v / maxFilms) * (H - m.t - m.b); };
    var yRating = function (v) { return H - m.b - ((v - rMin) / (rMax - rMin)) * (H - m.t - m.b); };
    var step = maxFilms > 200 ? 100 : 50;

    for (var t = 0; t <= maxFilms; t += step) {
      svg.appendChild(svgEl("line", { "class": "grid", x1: m.l, x2: W - m.r, y1: yFilms(t), y2: yFilms(t) }));
      svg.appendChild(svgEl("text", { x: m.l - 10, y: yFilms(t) + 4, "text-anchor": "end" }, t));
    }
    for (var r = rMin; r <= rMax; r++) {
      svg.appendChild(svgEl("text", { x: W - m.r + 10, y: yRating(r) + 4 }, r.toFixed(1)));
    }

    var points = [];
    rows.forEach(function (row, i) {
      var x = m.l + i * bw + bw * 0.18, w = bw * 0.64;
      var bar = svgEl("rect", {
        "class": "bar" + (row.best ? " is-best" : ""),
        x: x, y: yFilms(row.films), width: w, height: H - m.b - yFilms(row.films), rx: 6,
        style: "--i:" + i,
      });
      bar.addEventListener("pointerenter", function () {
        tip.textContent = fill(tipLabel, {
          decade: row.decade,
          films: row.films,
          rating: row.rating != null ? row.rating.toFixed(2) : "—",
        });
        var br = bar.getBoundingClientRect(), cr = box.getBoundingClientRect();
        tip.style.left = br.left - cr.left + br.width / 2 + "px";
        tip.style.top = br.top - cr.top + "px";
        tip.style.opacity = 1;
      });
      bar.addEventListener("pointerleave", function () { tip.style.opacity = 0; });
      svg.appendChild(bar);
      svg.appendChild(svgEl("text", { x: x + w / 2, y: H - m.b + 24, "text-anchor": "middle" },
        fill(decadeLabel, { decade: row.decade })));
      if (row.rating != null) points.push({ x: m.l + i * bw + bw / 2, y: yRating(row.rating), rating: row.rating });
    });

    if (points.length > 1) {
      svg.appendChild(svgEl("path", {
        "class": "line",
        d: "M" + points.map(function (p) { return p.x + "," + p.y; }).join(" L"),
      }));
    }
    points.forEach(function (p) {
      svg.appendChild(svgEl("circle", { "class": "pt", cx: p.x, cy: p.y, r: 5 }));
      svg.appendChild(svgEl("text", { "class": "val", x: p.x, y: p.y - 14, "text-anchor": "middle" }, p.rating.toFixed(2)));
    });

    box.classList.add("is-drawn");

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

  /* --- 7. Search palette ---------------------------------------------------------------------- */

  function initPalette() {
    var palette = document.querySelector("[data-search-palette]");
    if (!palette) return;
    var input = palette.querySelector("input");
    var list = palette.querySelector(".palette__list");
    var endpoint = palette.getAttribute("data-endpoint");
    var kindLabel = {
      movie: palette.getAttribute("data-label-movie"),
      series: palette.getAttribute("data-label-series"),
    };
    var items = [];
    var selected = 0;
    var pending = null;
    var debounce = null;
    var lastFocus = null;

    function note(text) {
      list.innerHTML = "";
      var p = document.createElement("p");
      p.className = "palette__note";
      p.textContent = text;
      list.appendChild(p);
      items = [];
    }

    function select(i) {
      selected = i;
      items.forEach(function (el, k) {
        el.setAttribute("aria-selected", k === i ? "true" : "false");
        if (k === i) el.scrollIntoView({ block: "nearest" });
      });
    }

    function render(results) {
      if (!results.length) {
        note(palette.getAttribute("data-empty"));
        return;
      }
      list.innerHTML = "";
      items = results.map(function (r, i) {
        var a = document.createElement("a");
        a.className = "palette__item";
        a.href = r.url;
        a.setAttribute("role", "option");
        a.id = "search-palette-opt-" + i;
        var img;
        if (r.poster) {
          img = document.createElement("img");
          img.src = r.poster;
          img.alt = "";
        } else {
          img = document.createElement("span");
          img.className = "palette__ph";
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
      select(0);
    }

    function search() {
      var q = input.value.trim();
      if (pending) pending.abort();
      if (q.length < 2) {
        note(palette.getAttribute("data-hint"));
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
          note(palette.getAttribute("data-empty"));
        });
    }

    function open() {
      lastFocus = document.activeElement;
      palette.hidden = false;
      input.value = "";
      note(palette.getAttribute("data-hint"));
      input.focus();
    }

    function close() {
      palette.hidden = true;
      if (pending) pending.abort();
      if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
    }

    document.querySelectorAll("[data-search-open]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        open();
      });
    });

    input.addEventListener("input", function () {
      clearTimeout(debounce);
      debounce = setTimeout(search, 160);
    });

    palette.addEventListener("click", function (e) {
      if (e.target === palette) close();
    });

    document.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (palette.hidden) open();
        else close();
        return;
      }
      if (palette.hidden) return;
      if (e.key === "Escape") {
        e.preventDefault();
        close();
      } else if (e.key === "ArrowDown" && items.length) {
        e.preventDefault();
        select((selected + 1) % items.length);
      } else if (e.key === "ArrowUp" && items.length) {
        e.preventDefault();
        select((selected - 1 + items.length) % items.length);
      } else if (e.key === "Enter" && items[selected]) {
        e.preventDefault();
        items[selected].click();
      }
    });
  }

  /* --- 8. Poster morph ---------------------------------------------------------------------------
     The film page's poster carries view-transition-name: film-poster
     (film.css). Naming the clicked poster the same on the way out lets a
     cross-document view transition morph one into the other. Only one element
     per page may hold a name, so the current page's own film poster, if any,
     gives it up first. Browsers without cross-document view transitions just
     navigate. */

  var FILM_PATH = /^\/movies\/[^/]+\/$/;

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
    initPalette();
    initPosterMorph();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
