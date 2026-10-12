# Season Rating Analytics — TV show pages (Tasks 130–131)

## Task 131 revision (current behaviour)

Task 131 replaced most of the Task 130 layout below at the user's request. **What ships now:**

- **The episode table is back to its original format:** one season at a time, with the same
  columns as before. There is no all-seasons table and no summary strip.
- **The slicer:** "All seasons" (shown when 2+ seasons are rated), then Season 1…N. **Season 1
  is selected on load**, so the page opens as it always did. Picking "All seasons" changes
  **only the chart**, to the season-by-season trend; the table stays on the season last picked.
- **Specials (season 0) are gone** from the slicer, the tables and the chart
  (`_build_seasons()` drops them).
- **The area chart under the table:**
  - **Line and fill:** a smooth (monotone) 2px lime line over a lime gradient that fades into
    the page. No dots at rest.
  - **Hover:** a hairline guide down the chart, with an HTML tooltip card styled from the
    site's own tokens.
  - **Markers:** best and lowest points get a small labelled marker.
  - **Average:** a dashed line, with its value printed in the key.
  - **Axis:** zoomed to the data, snapping to whole numbers when the span is wide.
  - **Motion:** the line draws in left to right (none under reduced motion).
- **Decided during the build:** the plan put the average's label on the canvas, but on a
  steady show both ends of the line run along the average, so any in-plot label landed on a
  point. The value lives in the key instead. X ticks stay plain numbers under a "Season" /
  "Episode" axis title rather than "S1"/"E1", which don't abbreviate cleanly in Russian.

The Task 130 record follows, kept for history.

---


Status: **implemented (Task 130).** Changes made during the build, after checking real shows:

- **No range markers on the chart.** The lowest-to-highest whiskers stretched the y-axis until
  the season trend went flat (Lost: averages 8.26–8.54 on a 6.5–10 axis). The axis now fits the
  plotted values with half a point either side (capped at 0 and 10). Each season's spread is in
  the table's Range column and the tooltip.
- **Averages print two decimals,** single episodes one: at one decimal, several seasons tie and
  "best season" looks arbitrary.
- **The "All seasons" view needs 2+ regular seasons that have episodes,** not just 2+ seasons:
  seasons are loaded before their episodes, and an overview of zeros said less than the old
  "not catalogued yet" line.
- **The reference-line value is printed in the chart key,** not on the canvas.
- **The season readout shows rated coverage** ("17 / 17") rather than total votes; votes are in
  the season table.

## Goal

The Episodes section of every TV show page (`series_detail.html`, `#episodes`) should help a
reader decide whether a show, or one season of it, is worth watching. Turn the section into a
small analysis panel: the whole show at a glance first, then a drill-down into one season.

## What exists today

- `movies/_season_picker.html`: a radio row, JS-gated (`html.has-js`), that switches the visible season.
- `movies/_episode_table.html`: #, title, air date, runtime and IMDb rating, client-paged 20 at a time.
- `_build_seasons()` (`movies/views.py`) already loads every episode with its IMDb rating in one
  query, so all the aggregates can be computed in Python. No new SQL or DDL is needed.
- The analytics dashboard already draws a season-rating chart with Chart.js 4.4.1
  (`static/js/analytics.js`): a lime line with a soft fill, colours read from CSS tokens.

## Layout, top to bottom

1. **Section header.** Unchanged: "Episodes" plus the "N seasons · N episodes" note.
2. **Season slicer.** The existing radio picker becomes chips: **All seasons** (selected by default),
   then Season 1…N. Single-select. Chips wrap onto new lines (The Daily Show has 31 seasons).
   Specials (season 0) get the last chip.
3. **Summary strip.** 3–4 figures that follow the slicer.
   - *All seasons:* show average, best season, weakest season, rated episodes ("47 of 52 rated").
   - *One season:* season average, best episode, weakest episode, total votes.
4. **Table.**
   - *All seasons:* a season summary table: season, episodes, aired span, average rating, best
     episode, weakest episode, votes. Clicking a row selects that season in the slicer.
   - *One season:* the existing episode table, unchanged.
5. **Area chart, under the table.**
   - *All seasons:* x = season (S1…SN), y = average IMDb rating, drawn as a line with a soft area
     fill. A faint band shows each season's lowest-to-highest episode rating, and a dashed reference
     line marks the show-wide average.
   - *One season:* x = episode number, y = episode rating, same style. The reference line becomes
     the season average.
   - Style matches the dashboard chart (`--lime-mark`, `--chart-wash`, colours read from CSS,
     redraws on theme change, respects reduced motion). Chart.js 4.4.1 from the same jsDelivr URL.
     The tooltip shows the average, rated/total episodes, the range and the vote total.

## Analyst rules

- **Average** is the plain mean of *rated* episodes, so each episode counts once, the way a viewer
  watches them. Unrated or unaired episodes are left out, never counted as 0.
- **Specials** are left out of the season trend and the show-wide average. They keep their own chip and table.
- **A season with no rated episodes** leaves a gap in the line (no joining across it) and reads
  "Not rated yet" in the table.
- **Fewer than 3 rated episodes** gets a hollow point and a "few ratings" note: the average isn't reliable.
- **A season still airing** is marked "in progress".
- **One-season shows** default the chart to the episode view, because a one-point trend tells you nothing.
- **Y-axis** is zoomed to the data (about 0.5 below the lowest value, up to 10) and clearly labelled.
  On a 0–10 axis every show looks flat at around 8.

## Data

- A pure helper, `season_rating_summary(seasons)`, computes the per-season average, lowest/highest,
  rated count, votes, flags and the show average. It is unit-testable.
- It hooks into `_build_seasons()`'s return, so the result is cached with the episodes.
- Chart data reaches the page via `json_script` inside the existing
  `{% datacache "series-episodes" %}` block.

## Progressive enhancement and constraints

- **Without JS:** the season summary table and every season's episode table render stacked, as
  they do today. The slicer and chart stay hidden behind the `html.has-js` gate.
- **Accessibility:** the canvas gets `role="img"` and a translated `aria-label`.
- **No internal names in the UI:** no table or column names, no IDs.
- **Translations:** all new strings go into ru/uz, then `makemessages`/`compilemessages`; commit the `.mo` files.
- **Templates:** numbers that code reads use `|unlocalize`.
- **Phone width (375px):** the slicer wraps, the summary strip goes 2×2, and the table scrolls inside its wrapper.
- **Design:** `/impeccable` before any template or CSS work.

## Steps

| # | Step | Files |
|---|---|---|
| 1 | Data: add `season_rating_summary()` and hook it into `_build_seasons()` | `movies/views.py` |
| 2 | Tests first: helper edge cases, then the view test | `tests/test_django_views.py` |
| 3 | Design pass with `/impeccable`: chips, summary strip, season table, chart placement | — |
| 4 | Templates: picker gets the "All" chip, new `_season_summary.html`, canvas and `json_script` | `movies/templates/movies/*` |
| 5 | JS: new `series_ratings.js` (copies analytics.js's colour/theme handling, swaps chart views); extend `initSeasonPicker()` for "All" and row clicks | `static/js/` |
| 6 | CSS for chips, summary strip and chart stage: both themes, 375px | `static/css/theoria.css` |
| 7 | Translations: ru/uz `.po`, then `compilemessages`; commit the `.mo` files | `locale/` |
| 8 | Check a long show, a one-season show and a still-airing show, in both themes and at mobile width | — |
| 9 | Bookkeeping: tasks.md, for_learning.md, `graphify update .`, commit | — |

## Tests

- `season_rating_summary`: unrated episodes excluded, specials excluded, an all-unrated season,
  a single season, the low-ratings flag.
- View test: the page renders the summary table and the JSON blocks for a show with several
  seasons and for a show with one season.
- Responsive layout via `tests/ui` against a running site.

## Deploy

Code-only and reads existing warehouse data, so there is no Neon migration step and it is safe
to deploy at any time.

## Decisions

- **One season at a time.** The slicer picks "All" or one season, not several. Picking several makes
  the chart's meaning unclear, and drilling into one season covers the "is this season worth it?" question.
- **"All seasons" shows a season summary table,** not every episode table stacked, which would mean
  1,200+ rows for a long show.
- **A zoomed y-axis,** not 0–10, so real differences between seasons are visible.
