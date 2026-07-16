// Home counter tick-up: the footage counter rolls from zero to its value on
// load, the way a projector counter climbs as the reel runs. Progressive
// enhancement — the final number is already in the DOM, so no-JS and
// reduced-motion users just see it sitting at rest.
(function () {
  "use strict";

  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function format(value, decimals) {
    if (decimals > 0) return value.toFixed(decimals);
    // Thousands separators, so "3,133 actors" reads like a real readout.
    return Math.round(value).toLocaleString("en-US");
  }

  function run(el) {
    var target = parseFloat(el.getAttribute("data-count"));
    if (isNaN(target)) return;
    var decimals = parseInt(el.getAttribute("data-decimals") || "0", 10);

    if (reduce) {
      el.textContent = format(target, decimals);
      return;
    }

    var duration = 1400;
    var start = null;

    function step(now) {
      if (start === null) start = now;
      var t = Math.min((now - start) / duration, 1);
      // ease-out cubic — fast then settling, like a counter coming to rest
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = format(target * eased, decimals);
      if (t < 1) requestAnimationFrame(step);
      else el.textContent = format(target, decimals);
    }

    requestAnimationFrame(step);
  }

  function init() {
    var cells = document.querySelectorAll(".counter-value[data-count]");
    for (var i = 0; i < cells.length; i++) run(cells[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
