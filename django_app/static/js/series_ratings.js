// Season rating chart on a show page (Task 130).
//
// One area chart under the Episodes section's tables, following the season
// picker: "All seasons" draws the season trend (each season's average episode
// rating against a dashed line at the show-wide episode average); a single
// season draws its episodes one by one against the season average. Each
// season's spread (lowest to highest episode) is in the table's Range column
// and the tooltip, not drawn: whiskers stretched the axis until the trend --
// the thing the reader came for -- went flat. Every number comes from the
// #season-chart-data block movies/season_stats.py builds -- this file only
// draws it.
//
// Same visual contract as analytics.js: one series, so one hue (the lime
// measurement mark) with its wash underneath, colours read from the CSS
// tokens and re-read on a theme switch. The figure stays hidden until a chart
// is actually drawn, so no JS, or Chart.js failing to load, leaves only the
// tables -- which already carry every number.
(function () {
  "use strict";

  var figure = document.querySelector("[data-season-chart]");
  var canvas = document.getElementById("season-rating-chart");
  var data = readJSON("season-chart-data");
  if (!figure || !canvas || !data || typeof Chart === "undefined") return;

  var picker = document.querySelector("[data-season-picker]");
  var refValue = figure.querySelector("[data-ref-value]");
  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Words come from the figure's data-t-* attributes, rendered by the
  // template with the same msgids as the tables above.
  var T = figure.dataset;

  // Uzbek formats as Russian, same as theoria.js and analytics.js: browser
  // Intl data for "uz" is unreliable.
  var LANG = (function () {
    var lang = document.documentElement.lang || "en";
    return lang === "uz" ? "ru" : lang;
  })();

  var FONT_BODY = '"Instrument Sans", system-ui, sans-serif';
  var FONT_MONO = '"Spline Sans Mono", ui-monospace, monospace';

  function readJSON(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return null;
    }
  }

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    v = v ? v.trim() : "";
    return v || fallback;
  }

  var MARK, WASH, RULE, INK, INK_FAINT, PAPER, SHEET;
  function readPalette() {
    MARK = cssVar("--lime-mark", "#65a30d");
    WASH = cssVar("--chart-wash", "rgba(163, 230, 53, 0.18)");
    RULE = cssVar("--rule", "#e3e2dd");
    INK = cssVar("--ink", "#0b0b0b");
    INK_FAINT = cssVar("--ink-faint", "#78716c");
    PAPER = cssVar("--paper", "#ffffff");
    SHEET = cssVar("--sheet-2", "#efeeea");
  }

  // Ratings print unlocalized ("8.2"), like every rating badge on the site;
  // vote counts take the language's digit grouping.
  function one(n) {
    return Number(n).toFixed(1);
  }
  function grouped(n) {
    return Number(n).toLocaleString(LANG);
  }

  // The axis is zoomed to the plotted values with half a point either side,
  // so the gap between an 8.26 and an 8.54 season is visible -- on a 0-10
  // axis every show flattens into the same line near 8. Capped at the
  // scale's real bounds.
  function boundsFor(values) {
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    return {
      min: Math.max(0, Math.floor((min - 0.5) * 2) / 2),
      max: Math.min(10, Math.ceil((max + 0.5) * 2) / 2),
    };
  }

  // The dashed reference line, drawn under the data so the measured line
  // stays on top. Its value is printed in the key, not on the canvas, where
  // it collided with the last point.
  var overlay = {
    id: "seasonOverlay",
    beforeDatasetsDraw: function (chart, args, opts) {
      if (opts.reference == null) return;
      var ctx = chart.ctx;
      var area = chart.chartArea;
      var ry = Math.round(chart.scales.y.getPixelForValue(opts.reference)) + 0.5;
      ctx.save();
      ctx.strokeStyle = INK_FAINT;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(area.left, ry);
      ctx.lineTo(area.right, ry);
      ctx.stroke();
      ctx.restore();
    },
  };

  function seasonView() {
    var seasons = data.seasons || [];
    var values = [];
    seasons.forEach(function (s) {
      if (s.avg != null) values.push(s.avg);
    });
    if (!values.length) return null;
    return {
      labels: seasons.map(function (s) { return String(s.number); }),
      values: seasons.map(function (s) { return s.avg; }),
      hollow: seasons.map(function (s) { return !!s.few; }),
      reference: data.show_avg,
      bounds: boundsFor(values),
      axis: T.tSeason,
      aria: T.tAriaSeasons,
      title: function (i) {
        var s = seasons[i];
        return s.name || T.tSeason + " " + s.number;
      },
      lines: function (i) {
        var s = seasons[i];
        if (s.avg == null) return [];
        var out = [
          T.tAverage + ": " + Number(s.avg).toFixed(2),
          T.tRange + ": " + one(s.low) + "–" + one(s.high),
          T.tRated + ": " + s.rated + " / " + s.episodes,
          T.tVotes + ": " + grouped(s.votes),
        ];
        if (s.few) out.push("(" + T.tFew + ")");
        return out;
      },
    };
  }

  function episodeView(number) {
    var season = (data.episodes || {})[number];
    if (!season) return null;
    var points = season.points;
    var values = points
      .map(function (p) { return p.rating; })
      .filter(function (v) { return v != null; });
    if (!values.length) return null;
    return {
      labels: points.map(function (p) {
        return p.number != null ? String(p.number) : "";
      }),
      values: points.map(function (p) { return p.rating; }),
      hollow: null,
      reference: season.avg,
      bounds: boundsFor(values),
      axis: T.tEpisode,
      aria: T.tAriaEpisodes,
      title: function (i) {
        var p = points[i];
        return T.tEpisode + " " + (p.number != null ? p.number : "—") +
          (p.name ? " · " + p.name : "");
      },
      lines: function (i) {
        var p = points[i];
        if (p.rating == null) return [];
        return [T.tRating + ": " + one(p.rating), T.tVotes + ": " + grouped(p.votes)];
      },
    };
  }

  function selected() {
    var checked = picker && picker.querySelector("input[name=season]:checked");
    if (checked) return checked.value;
    // No picker to read: a one-season show opens on its episodes.
    var keys = Object.keys(data.episodes || {});
    return (data.seasons || []).length >= 2 ? "all" : keys[0];
  }

  var chart = null;

  function draw() {
    if (chart) {
      chart.destroy();
      chart = null;
    }
    var value = selected();
    var view = value === "all" ? seasonView() : episodeView(value);
    figure.setAttribute("data-view", value === "all" ? "all" : "season");
    if (!view) {
      figure.hidden = true;
      return;
    }
    figure.hidden = false;
    readPalette();
    canvas.setAttribute("aria-label", view.aria);
    if (refValue) {
      refValue.textContent =
        view.reference != null ? Number(view.reference).toFixed(2) : "";
    }

    // A long season (a daily show runs 200+ episodes) reads as a line, not
    // a string of beads.
    var dense = view.values.length > 40;
    var hollow = view.hollow;

    chart = new Chart(canvas, {
      type: "line",
      data: {
        labels: view.labels,
        datasets: [
          {
            data: view.values,
            borderColor: MARK,
            borderWidth: 2,
            backgroundColor: WASH,
            fill: "start",
            // Monotone: a plain spline overshoots between points and draws
            // ratings no episode got.
            cubicInterpolationMode: "monotone",
            spanGaps: false,
            pointRadius: dense ? 0 : 4,
            pointHoverRadius: dense ? 4 : 7,
            pointHitRadius: 16,
            pointBorderWidth: 2,
            pointBorderColor: hollow
              ? hollow.map(function (h) { return h ? MARK : PAPER; })
              : PAPER,
            pointBackgroundColor: hollow
              ? hollow.map(function (h) { return h ? PAPER : MARK; })
              : MARK,
          },
        ],
      },
      plugins: [overlay],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: reduce ? false : { duration: 500 },
        interaction: { mode: "index", intersect: false },
        layout: { padding: { top: 4 } },
        plugins: {
          legend: { display: false }, // the key under the heading names each mark
          seasonOverlay: { reference: view.reference },
          tooltip: {
            backgroundColor: SHEET,
            titleColor: INK,
            bodyColor: INK,
            borderColor: RULE,
            borderWidth: 1,
            titleFont: { family: FONT_BODY, weight: "600", size: 12 },
            bodyFont: { family: FONT_MONO, size: 12 },
            padding: 10,
            displayColors: false,
            callbacks: {
              title: function (items) {
                return items.length ? view.title(items[0].dataIndex) : "";
              },
              label: function (ctx) {
                return view.lines(ctx.dataIndex);
              },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { color: RULE },
            title: {
              display: true,
              text: view.axis,
              color: INK_FAINT,
              font: { family: FONT_BODY, size: 11 },
            },
            ticks: {
              color: INK_FAINT,
              font: { family: FONT_MONO, size: 11 },
              autoSkip: true,
              maxRotation: 0,
            },
          },
          y: {
            min: view.bounds.min,
            max: view.bounds.max,
            grid: { color: RULE },
            border: { display: false },
            ticks: {
              color: INK_FAINT,
              font: { family: FONT_MONO, size: 11 },
              callback: function (v) { return one(v); },
            },
          },
        },
      },
    });
  }

  if (picker) {
    picker.addEventListener("change", function (e) {
      if (e.target.name === "season") draw();
    });
  }
  // Chart.js bakes colours in at build time, so a theme switch rebuilds.
  document.addEventListener("themechange", draw);

  draw();
})();
