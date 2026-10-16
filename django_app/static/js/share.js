/* Theoria — the share sheet on /me (Task 139).

   Opens the one <dialog id="share-sheet"> from a section's [data-share-open]
   button and runs it: make / reset / stop the public link, save the note,
   preview the artwork, and turn it into a PNG.

   The artwork is the server's own card (`/s/<token>/card/`, the bare fragment
   for X-Requested-With). It is dropped twice: into an off-page stage at its
   true pixel size -- the copy that gets captured -- and into the preview, the
   same markup scaled down with CSS. The capture waits for fonts and every
   poster to decode, then writes the PNG at scale 1 so the file is exactly the
   card's size. Everything degrades: no <dialog>, no fetch or no capture
   library leaves the plain form on the page (html.has-js hides it only once
   this file has run). */
(function () {
  "use strict";

  var dialog = document.getElementById("share-sheet");
  if (!dialog || typeof dialog.showModal !== "function" || !window.fetch) {
    // No sheet here: let the plain form show again (share-sheet.css).
    document.documentElement.classList.add("no-share-sheet");
    return;
  }

  var $ = function (sel) { return dialog.querySelector(sel); };
  var msg = function (key) { return dialog.dataset["msg" + key] || ""; };

  var els = {
    name: $("[data-share-name]"),
    live: $("[data-share-live]"),
    off: $("[data-share-off]"),
    on: $("[data-share-on]"),
    preview: $("[data-share-preview]"),
    frame: $("[data-share-frame]"),
    previewState: $("[data-share-preview-state]"),
    stage: $("[data-share-stage]"),
    link: $("[data-share-link]"),
    note: $("[data-share-note]"),
    count: $("[data-share-count]"),
    image: $("[data-share-image]"),
    download: $("[data-share-download]"),
    copyImage: $("[data-share-copy-image]"),
    dangerRow: $("[data-share-danger]"),
    noteState: $("[data-share-note-state]"),
    copyLink: $("[data-share-copy-link]"),
    confirm: $("[data-share-confirm]"),
    confirmText: $("[data-share-confirm-text]"),
    confirmDo: $("[data-share-do]"),
    csrf: dialog.querySelector('input[name="csrfmiddlewaretoken"]'),
  };
  var statuses = dialog.querySelectorAll("[data-share-status]");
  var imageStatus = $(".share-group--image [data-share-status]");
  var linkStatus = $("[data-share-off] [data-share-status]");
  var drawingText = els.previewState.textContent;

  var CARD_CSS = document.querySelector('link[href*="share-card.css"]');
  var cardAssets = null;
  var current = null;      // the collection the sheet is open on
  var drawSeq = 0;         // bumps on every card request; stale answers are dropped
  var drawAbort = null;
  var blobPromise = null;  // the PNG for the current card, made ahead of the tap
  var prerenderTimer = null;
  var noteTimer = null;
  var pending = null;      // "rotate" | "disable" while the confirmation shows
  var resizeObserver = null;

  var isSafari = /^((?!chrome|chromium|android).)*safari/i.test(navigator.userAgent);
  var canShareFiles = (function () {
    try {
      return !!(navigator.share && navigator.canShare &&
        navigator.canShare({ files: [new File(["x"], "t.png", { type: "image/png" })] }));
    } catch (e) { return false; }
  })();
  var canCopyImage = !!(window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) &&
    !window.matchMedia("(pointer: coarse)").matches;

  /* --- Small helpers ----------------------------------------------------- */

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem("theoria-share-" + key);
      localStorage.setItem("theoria-share-" + key, value);
    } catch (e) {}
    return null;
  }

  // One message at a time, under the group it is about (the image group by
  // default); every other status line is cleared.
  function say(text, isError, where) {
    var target = where || imageStatus;
    for (var i = 0; i < statuses.length; i++) {
      statuses[i].textContent = statuses[i] === target ? text || "" : "";
      statuses[i].classList.toggle("is-error", statuses[i] === target && !!isError);
    }
  }

  // A button's own words: its [data-label] span, so the icon stays.
  function label(button, text) {
    var span = button.querySelector("[data-label]") || button;
    if (text === undefined) return span.textContent;
    span.textContent = text;
    return text;
  }

  // Say "Copied" / "Done" on the button that did it, then put it back.
  function flash(button, text) {
    if (!button.dataset.rest) button.dataset.rest = label(button);
    clearTimeout(button._flash);
    label(button, text);
    button.classList.add("is-done");
    button._flash = setTimeout(function () {
      label(button, button.dataset.rest);
      delete button.dataset.rest;
      button.classList.remove("is-done");
    }, 1800);
  }

  // While the PNG is being made: the button says so and can't be pressed twice.
  function working(button, on) {
    button.disabled = on;
    if (on) {
      clearTimeout(button._flash);
      button.classList.remove("is-done");
      if (!button.dataset.rest) button.dataset.rest = label(button);
      label(button, msg("Working"));
    } else if (!button.classList.contains("is-done") && button.dataset.rest) {
      label(button, button.dataset.rest);
      delete button.dataset.rest;
    }
  }

  function opener(kind) {
    return document.querySelector('[data-share-open="' + kind + '"]');
  }

  // Keep the section's own button in step, so the next open (and the "link is
  // on" dot) match what the server now holds without re-fetching the section.
  function syncButton() {
    var btn = current && opener(current.kind);
    if (!btn) return;
    btn.dataset.shareUrl = current.url || "";
    btn.dataset.shareNote = current.savedNote || "";
    if (current.url) btn.setAttribute("data-shared", "");
    else btn.removeAttribute("data-shared");
  }

  function post(action, extra) {
    var body = new FormData();
    body.append("csrfmiddlewaretoken", els.csrf ? els.csrf.value : "");
    body.append("action", action);
    if (extra && extra.note !== undefined) body.append("note", extra.note);
    return fetch(current.endpoint, {
      method: "POST",
      body: body,
      headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error(data.error || msg("Failed"));
        return data;
      });
    });
  }

  /* --- States ------------------------------------------------------------
     One layout for every collection; only the Link group has two faces. */

  function showLink(on) {
    els.off.hidden = on;
    els.on.hidden = !on;
    if (on) els.link.value = current.url;
    cancelConfirm();
  }

  function resetCard() {
    drawSeq += 1;
    if (drawAbort) drawAbort.abort();
    blobPromise = null;
    clearTimeout(prerenderTimer);
    els.stage.textContent = "";
    els.frame.textContent = "";
    els.preview.setAttribute("aria-busy", "true");
  }

  function fill() {
    showLink(!!current.url);
    els.note.value = current.savedNote;
    els.noteState.textContent = "";
    updateCount();
    els.preview.setAttribute("aria-busy", "true");
    els.previewState.textContent = drawingText;
    var fmt = dialog.querySelector('input[name="share-format"][value="' + current.format + '"]');
    var theme = dialog.querySelector('input[name="share-theme"][value="' + current.theme + '"]');
    if (fmt) fmt.checked = true;
    if (theme) theme.checked = true;
    drawCard().catch(function () {});
  }

  function open(btn) {
    var url = btn.dataset.shareUrl || "";
    var wide = !(window.matchMedia("(pointer: coarse)").matches || window.innerWidth < 700);
    var format = store("format");
    var theme = store("theme");
    current = {
      kind: btn.dataset.shareOpen,
      endpoint: btn.dataset.shareEndpoint,
      card: btn.dataset.shareCard,
      url: url,
      savedNote: btn.dataset.shareNote || "",
      format: format === "story" || format === "wide" ? format : (wide ? "wide" : "story"),
      theme: theme === "paper" ? "paper" : "ink",
    };
    dialog.dataset.slug = btn.dataset.shareSlug || "";
    els.name.textContent = btn.dataset.shareLabel || "";
    say("");
    label(els.image, canShareFiles ? els.image.dataset.labelShare : els.image.dataset.labelDownload);
    els.image.classList.toggle("can-share", canShareFiles);
    els.download.hidden = !canShareFiles;
    els.copyImage.hidden = !canCopyImage;
    dialog.showModal();
    fill();
    els.image.focus({ preventScroll: true });
  }

  /* --- The card ---------------------------------------------------------- */

  // The card's stylesheet and faces are only needed here, so /me does not pay
  // for them until a sheet opens. crossorigin on the fonts sheet is what lets
  // the capture read its @font-face rules.
  function ensureCardAssets() {
    if (cardAssets) return cardAssets;
    var hrefs = [
      { href: dialog.dataset.cardCss, attrs: {} },
      { href: dialog.dataset.fontsCss, attrs: { crossorigin: "anonymous" } },
    ];
    cardAssets = Promise.all(hrefs.map(function (item) {
      if (!item.href) return null;
      if (CARD_CSS && item.href === CARD_CSS.getAttribute("href")) return null;
      return new Promise(function (resolve) {
        var link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = item.href;
        for (var name in item.attrs) link.setAttribute(name, item.attrs[name]);
        link.onload = link.onerror = function () { resolve(); };
        document.head.appendChild(link);
      });
    }));
    return cardAssets;
  }

  function settled(stage) {
    var imgs = Array.prototype.slice.call(stage.querySelectorAll("img"));
    var decoded = imgs.map(function (img) {
      return img.decode ? img.decode().catch(function () {}) : Promise.resolve();
    });
    return Promise.all(decoded).then(function () {
      return document.fonts && document.fonts.ready;
    });
  }

  function drawCard() {
    var seq = ++drawSeq;
    if (drawAbort) drawAbort.abort();
    drawAbort = window.AbortController ? new AbortController() : null;
    blobPromise = null;
    clearTimeout(prerenderTimer);
    els.preview.setAttribute("aria-busy", "true");
    els.previewState.textContent = drawingText;

    els.live.dataset.format = current.format;
    var address = current.card + "?format=" + current.format + "&theme=" + current.theme;
    return ensureCardAssets()
      .then(function () {
        return fetch(address, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
          credentials: "same-origin",
          signal: drawAbort ? drawAbort.signal : undefined,
        });
      })
      .then(function (response) {
        if (!response.ok) throw new Error("card");
        return response.text();
      })
      .then(function (html) {
        if (seq !== drawSeq) return;
        els.stage.innerHTML = html;
        return settled(els.stage).then(function () {
          if (seq !== drawSeq) return;
          var card = els.stage.querySelector(".sc");
          if (!card) throw new Error("card");
          var box = card.getBoundingClientRect();
          els.preview.style.setProperty("--pw", Math.round(box.width));
          els.preview.style.setProperty("--ph", Math.round(box.height));
          els.frame.innerHTML = html;
          scalePreview();
          els.preview.setAttribute("aria-busy", "false");
          schedulePrerender();
        });
      })
      .catch(function (err) {
        if (seq !== drawSeq || (err && err.name === "AbortError")) throw err;
        els.previewState.textContent = msg("ImageFailed");
        say(msg("ImageFailed"), true);
        throw err;
      });
  }

  function scalePreview() {
    var card = els.frame.querySelector(".sc");
    var width = parseFloat(els.preview.style.getPropertyValue("--pw"));
    if (!card || !width || !els.preview.clientWidth) return;
    els.preview.style.setProperty("--k", els.preview.clientWidth / width);
  }

  /* --- The image --------------------------------------------------------- */

  function capture() {
    var card = els.stage.querySelector(".sc");
    if (!card || !window.modernScreenshot) return Promise.reject(new Error("no card"));
    var box = card.getBoundingClientRect();
    var options = { scale: 1, width: Math.round(box.width), height: Math.round(box.height), type: "image/png" };
    var run = function () { return window.modernScreenshot.domToBlob(card, options); };
    // Safari drops images on the first DOM -> canvas pass; a throwaway first
    // pass lets them settle so the real one has them.
    return isSafari ? run().then(run) : run();
  }

  function makeBlob() {
    return settled(els.stage).then(capture);
  }

  function getBlob() {
    if (!blobPromise) {
      blobPromise = makeBlob();
      blobPromise.catch(function () { blobPromise = null; });
    }
    return blobPromise;
  }

  // Start the PNG before anyone asks: navigator.share() must be called inside
  // the tap's short window of user activation, so the tap should only await a
  // promise that has usually settled already.
  function schedulePrerender() {
    clearTimeout(prerenderTimer);
    prerenderTimer = setTimeout(function () { getBlob().catch(function () {}); }, 500);
  }

  function fileName() {
    var kind = current.kind.replace(/_/g, "-");
    return "theoria-" + kind + "-" + (dialog.dataset.username || "me") + ".png";
  }

  function saveBlob(blob) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = fileName();
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
  }

  function failed() { say(msg("ImageFailed"), true); }

  // The image prints the link, so a private list gets its link first (and its
  // card redrawn with the real address). Resolves true when it made one.
  function ensureLink() {
    if (current.url) return Promise.resolve(false);
    return post("enable").then(function (data) {
      linkMade(data);
      return drawCard().then(function () { return true; });
    });
  }

  function linkMade(data) {
    current.url = data.url;
    current.savedNote = data.note || "";
    syncButton();
    showLink(true);
  }

  function shareOrSave() {
    var button = els.image;
    say("");
    working(button, true);
    ensureLink()
      .then(function () { return getBlob(); })
      .then(function (blob) {
        if (!canShareFiles) { saveBlob(blob); return true; }
        var file = new File([blob], fileName(), { type: "image/png" });
        return navigator.share({ files: [file], url: current.url }).then(function () { return true; });
      })
      .catch(function (err) {
        // Making the link first can outlast the tap's permission to open
        // the share sheet; the image is ready now, so the next tap is instant.
        if (err && err.name === "NotAllowedError") say(msg("TapAgain"));
        else if (!(err && err.name === "AbortError")) failed();
        return false;
      })
      .then(function (ok) {
        working(button, false);
        if (ok) flash(button, msg("Done"));
      });
  }

  function downloadOnly() {
    var button = els.download;
    say("");
    working(button, true);
    ensureLink()
      .then(function () { return getBlob(); })
      .then(function (blob) { saveBlob(blob); return true; })
      .catch(function () { failed(); return false; })
      .then(function (ok) {
        working(button, false);
        if (ok) flash(button, msg("Done"));
      });
  }

  function copyImage() {
    var button = els.copyImage;
    say("");
    working(button, true);
    // The promise goes to ClipboardItem unresolved: Safari only allows the
    // write if it starts inside the tap.
    var png = ensureLink().then(function () { return getBlob(); });
    navigator.clipboard.write([new ClipboardItem({ "image/png": png })])
      .then(function () { return true; })
      .catch(function () { failed(); return false; })
      .then(function (ok) {
        working(button, false);
        if (ok) flash(button, msg("Copied"));
      });
  }

  function copyLink() {
    var done = function () { flash(els.copyLink, msg("Copied")); };
    var fallback = function () {
      els.link.focus();
      els.link.select();
      try { if (document.execCommand("copy")) { done(); return; } } catch (e) {}
      say(msg("CopyFailed"), true);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(current.url).then(done, fallback);
    } else {
      fallback();
    }
  }

  /* --- Note -------------------------------------------------------------- */

  function updateCount() {
    var n = els.note.value.length;
    els.count.textContent = n + "/" + els.note.maxLength;
    if (els.noteState.textContent === msg("Saved") + " ·") els.noteState.textContent = "";
    els.count.classList.toggle("is-near", n >= els.note.maxLength - 20);
  }

  function saveNote() {
    clearTimeout(noteTimer);
    if (!current || !current.url) return Promise.resolve();
    var text = els.note.value;
    if (text.trim() === current.savedNote) return Promise.resolve();
    els.noteState.textContent = msg("Saving");
    return post("enable", { note: text })
      .then(function (data) {
        current.savedNote = data.note;
        syncButton();
        els.noteState.textContent = msg("Saved") + " ·";
      })
      .catch(function (err) {
        els.noteState.textContent = "";
        say(err.message, true);
      });
  }

  /* --- Rotate / stop ----------------------------------------------------- */

  function ask(action) {
    pending = action;
    els.confirmText.textContent = els.confirmDo.dataset["text" + action.charAt(0).toUpperCase() + action.slice(1)];
    els.confirmDo.textContent = els.confirmDo.dataset["yes" + action.charAt(0).toUpperCase() + action.slice(1)];
    els.dangerRow.hidden = true;
    els.confirm.hidden = false;
    els.confirmDo.focus();
  }

  function cancelConfirm() {
    pending = null;
    els.confirm.hidden = true;
    els.dangerRow.hidden = false;
  }

  function confirmed() {
    var action = pending;
    if (!action) return;
    els.confirmDo.disabled = true;
    saveNote()
      .then(function () { return post(action); })
      .then(function (data) {
        if (action === "rotate") {
          current.url = data.url;
          els.link.value = data.url;
          cancelConfirm();
          syncButton();
          drawCard().catch(function () {});
          say(msg("Reset"));
        } else {
          current.url = "";
          syncButton();
          showLink(false);
          drawCard().catch(function () {});
          say(msg("Stopped"), false, linkStatus);
          dialog.querySelector("[data-share-enable]").focus();
        }
      })
      .catch(function (err) { cancelConfirm(); say(err.message, true); })
      .then(function () { els.confirmDo.disabled = false; });
  }

  function enable() {
    var button = dialog.querySelector("[data-share-enable]");
    button.disabled = true;
    say("");
    post("enable")
      .then(function (data) {
        linkMade(data);
        els.copyLink.focus({ preventScroll: true });
        drawCard().catch(function () {});
      })
      .catch(function (err) { say(err.message, true, linkStatus); })
      .then(function () { button.disabled = false; });
  }

  /* --- Wiring ------------------------------------------------------------ */

  document.addEventListener("click", function (event) {
    var btn = event.target.closest && event.target.closest("[data-share-open]");
    if (btn && !btn.disabled) open(btn);
  });

  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) { dialog.close(); return; }
    var t = event.target.closest("button");
    if (!t || !dialog.contains(t)) return;
    if (t.hasAttribute("data-share-close")) dialog.close();
    else if (t.hasAttribute("data-share-enable")) enable();
    else if (t.hasAttribute("data-share-copy-link")) copyLink();
    else if (t.hasAttribute("data-share-image")) shareOrSave();
    else if (t.hasAttribute("data-share-download")) downloadOnly();
    else if (t.hasAttribute("data-share-copy-image")) copyImage();
    else if (t.hasAttribute("data-share-ask")) ask(t.getAttribute("data-share-ask"));
    else if (t.hasAttribute("data-share-do")) confirmed();
    else if (t.hasAttribute("data-share-undo")) {
      cancelConfirm();
      dialog.querySelector("[data-share-ask]").focus();
    }
  });

  dialog.addEventListener("change", function (event) {
    var input = event.target;
    if (input.name === "share-format" || input.name === "share-theme") {
      var key = input.name === "share-format" ? "format" : "theme";
      current[key] = input.value;
      store(key, input.value);
      drawCard().catch(function () {});
    } else if (input === els.note) {
      saveNote();
    }
  });

  els.note.addEventListener("input", function () {
    updateCount();
    clearTimeout(noteTimer);
    noteTimer = setTimeout(saveNote, 1200);
  });

  els.link.addEventListener("focus", function () { els.link.select(); });

  dialog.addEventListener("close", function () {
    saveNote();
    resetCard();
    cancelConfirm();
    say("");
  });

  if (window.ResizeObserver) {
    resizeObserver = new ResizeObserver(scalePreview);
    resizeObserver.observe(els.preview);
  } else {
    window.addEventListener("resize", scalePreview);
  }
})();
