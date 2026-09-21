# Theoria — Account page (`/me/`) redesign

**Implementation brief and prompt for Claude Code**

Save this file in the repo as `docs/account-page-redesign.md`. Section 1 is the prompt to paste into Claude Code. Sections 2–11 are the spec it works from.

---

## 1. The prompt

Paste this into Claude Code from the repo root:

```text
You're implementing a redesign of the account page at /me/. The full spec is in
docs/account-page-redesign.md. Read the whole file before doing anything.

PHASE 1: Explore and plan. Do not write code in this phase.
Explore the repo and report back:
  1. The stack, and how /me/ is routed, rendered and fetched today.
  2. The component that renders a title card on the homepage and list pages
     (poster, title, year, rating). Give the file path and its props.
  3. The header/nav component and how the account pill is rendered.
  4. How theme tokens (colors, fonts, spacing) are defined, and how the
     light/dark toggle works.
  5. The data model for Liked, Watch later and Top: tables/collections,
     fields, and whether an "added at" timestamp and a rank field exist.
  6. How other paginated or sortable lists in this project are built
     (query params, server vs client, API shape), if there are any.
  7. Every place where this spec conflicts with existing patterns or data.
     Stop there and wait for my approval.

PHASE 2: Implement, after I approve the plan.
  - Extend the existing title card and header components. Do not create
    parallel versions.
  - Use the exact token, type and spacing values in the spec. Don't round
    them.
  - Sort and paginate on the server/database, never over the current page
    only.
  - Keep changes scoped to the account page, the components it needs, and
    the data/API layer for collections.
  - Add or update tests per the acceptance checklist (section 11).
  - Finish with a summary of the changed files and anything you left out
    or decided differently from the spec, with the reason.
```

The two phases matter. If Claude Code explores first, it doesn't build a second card component beside the one the homepage already uses, and it doesn't guess at your data model.

---

## 2. Scope

Redesign `/me/` so it shows:

1. **Profile hero**: username, email, member-since date, monogram avatar, *Edit profile* and *Sign out*.
2. **Jump chips**: Liked · Watch later · Top, each with its count, linking to its section.
3. **Three collections**, each shown as a grid of full title cards (poster, type, title, year, rating), not the current text rows:
   - **Liked**: paginated and sortable.
   - **Watch later**: paginated and sortable.
   - **Top**: a ranked list capped at 5. No pagination and no sort; order is the rank.

Out of scope: editing the profile, reordering Top by drag, and social or sharing features.

---

## 3. Design tokens

Use these as the dark-theme values. Map them onto the existing token system. If the codebase already has a token for a role, keep its name and update the value only when it differs. Derive the light theme the same way the rest of the site does.

### Colors

| Role | Value |
| --- | --- |
| Page ground | `#0A0A0A` |
| Poster / panel fill | `#131311` |
| Select / input fill | `#101010` |
| Hairline rule | `#1C1C19` |
| Card border | `#232320` |
| Control border | `#26261F` |
| Badge border | `#2E2E27` |
| Dashed placeholder border | `#2A2A24` |
| Dashed placeholder hover | `#3A3A31` |
| Dot separator | `#3A3A31` |
| Primary text | `#F2F2EF` |
| Secondary text / nav rest | `#B9B9B2` |
| Muted text / captions | `#8C8C86` |
| Disabled control text | `#46463D` |
| Accent (lime) | `#BFF23D` |
| Accent hover (links) | `#D7FA7E` |
| Accent tint fill (icon tiles) | `rgba(191,242,61,0.10)` |
| Accent tint border (icon tiles) | `rgba(191,242,61,0.28)` |
| Rank numeral overlay | `rgba(191,242,61,0.16)` |
| Rank slot numeral | `#23231E` |
| Destructive text | `#F2685E` |
| Destructive fill | `rgba(242,92,84,0.08)` |
| Destructive border | `rgba(242,92,84,0.38)` |
| Destructive hover | text `#FF8A80`, fill `rgba(242,92,84,0.14)`, border `rgba(242,92,84,0.60)` |
| Poster texture | `repeating-linear-gradient(135deg, rgba(255,255,255,0.03) 0 2px, transparent 2px 10px)` |

### Typography

Fonts: **Archivo** (600/700/800) for display and **Outfit** (300/400/500/600) for body, both from Google Fonts. If the site already loads different faces, keep them and apply the sizes below.

| Element | Spec |
| --- | --- |
| Profile name | Archivo 800, 124px, line-height 0.86, tracking −0.035em, uppercase |
| Section heading | Archivo 800, 46px, tracking −0.015em, uppercase |
| Poster title (placeholder art) | Archivo 800, 30–32px, line-height 1.02, tracking −0.02em, uppercase |
| Empty-panel heading | Archivo 700, 28px, tracking −0.01em |
| Rank slot numeral | Archivo 800, 72px |
| Rank overlay numeral | Archivo 800, 96px |
| Card title link | Outfit 500, 18px |
| Hero meta (email · date) | Outfit 400, 17px, secondary text |
| Buttons | Outfit 500–600, 15px |
| Chips, select | Outfit 400, 14px |
| Card meta, pager count | Outfit 400, 13px, muted |
| Micro labels (`ACCOUNT`, `SORT`, `24 TITLES`) | 12–13px, tracking 0.18–0.26em, weight 500 |
| Poster type badge | 11px, tracking 0.18em |

### Radii

Poster and card: 12px. Empty panel: 14px. Avatar tile: 18px. Section icon tile: 12px. Buttons, pager and select: 10px. Hero buttons: 12px. Chips and badges: 999px. Rating pill: 6px. Remove button: 50%.

---

## 4. Layout

- **Container**: 1440px design width with an **80px** side gutter.
- **Header**: 88px tall with a 1px `#1C1C19` bottom rule. Nav gap is 34px. The account pill is accent-filled with a 24px dark monogram circle. The theme toggle is a 44px circular icon button.
- **Hero**: `grid-template-columns: minmax(0,1fr) 320px`, `gap: 72px`, `padding: 72px 80px 0`.
  - The left column stacks: an 88×5px accent bar, then 22px, the `ACCOUNT` label (accent), then 12px, the name, then 30px, the meta row (email · dot · "Member since 13 Sep 2026"), then 38px, the jump chips (gap 12px).
  - The right column stacks, with 18px gaps: a 320×200 accent tile holding the monogram (Archivo 800, 110px, dark), *Edit profile* (52px tall, outlined), and *Sign out* (52px tall, destructive style, with an 18px log-out icon, gap 10px).
- **Section rhythm**: the heading row, then 20px, a 1px rule, 34px, the grid, 34px, then the pager (22px padding-top above its own 1px rule). The first section starts 96px below the hero and the others 88px apart.
- **Section heading row**: on the left, a 44×44 icon tile, 18px, then the heading. On the right, the sort control and the count (see section 7).
- **Grid**: `repeat(5, minmax(0,1fr))` with `gap: 24px`. Each card is a flex column with `gap: 14px`: the poster, then the meta row.
- **Footer**: 96px tall with a 1px top rule, the wordmark on the left and "Account · {username}" on the right, both muted.

### Responsive (not drawn; implement as follows)

| Viewport | Grid columns | Notes |
| --- | --- | --- |
| ≥ 1280px | 5 | as designed |
| 1024–1279 | 4 | |
| 768–1023 | 3 | the hero becomes one column, with the avatar tile and buttons in a row under the name |
| < 768 | 2 | 16px side gutter; name `clamp(48px, 12vw, 124px)`; the heading row wraps so the sort control drops below the title |

The poster keeps `aspect-ratio: 2 / 3` at every breakpoint. Page size stays 10 at every breakpoint, so the server contract doesn't depend on the viewport.

---

## 5. Components

Build these as extensions of the existing components where they exist.

1. **`CollectionSection`**. Props: `key` (`liked` | `later` | `top`), `label`, `icon`, `items`, `pagination` (`page`, `pageSize`, `total`), `sort` (current value, or null for Top). It renders the heading row, the rule, the grid, the empty-fill, and the pager (not for Top).
2. **`TitleCard`**: the existing card, extended. Top to bottom: the poster (image or placeholder), the type badge (top-left), the remove button (top-right), a 64×4px accent tick (bottom-left), and, for Top only, the rank overlay numeral (bottom-right). The meta row holds the title link and `Type · Year` on the left and the rating pill on the right.
3. **`SectionIcon`**: a 44×44 tile holding a 22px stroke icon (stroke width 1.9, round caps and joins) in the accent color. Liked uses a **heart**, Watch later a **clock**, Top a **trophy**. It's decorative (`aria-hidden="true"`) because the heading already names the section.
4. **`SortSelect`**: a labelled native `<select>` (see section 7).
5. **`Pager`**: see section 8.
6. **`EmptyPanel`**: a dashed panel that spans the grid columns left over on a partly filled page (`grid-column: span N`, where N = 5 − items on the row). It shows a heading, one line of copy and one CTA:
   - Liked: "Room for more" / "Everything you like across Movies, TV Shows and Cartoons collects here, newest first." / **Browse titles** (accent-filled button).
   - Watch later: "Your queue is short" / "Save anything you mean to get to and it waits here until you do." / **Find something** (outlined button).
   - Show it only on the **last** page, and only when that page's last row isn't full. If the row is full, don't render it.
7. **`RankSlot`** (Top only): a dashed 2/3 tile with the rank numeral (02–05) and "ADD TITLE" beneath it, captioned "Empty slot". It's a real `<button>` that goes to browse.
8. **Collection empty state** (0 items): skip the grid and render one full-width `EmptyPanel` (span 5). Hide the sort control and the pager.

---

## 6. Behaviour: cards

- **Hover**: the poster lifts by `translateY(-6px)` and its border turns accent. The transition is 180ms ease on `transform` and `border-color`. The card title turns accent.
- **Remove button**: 44×44, circular, `rgba(10,10,10,0.78)` fill, `#2E2E27` border, with a 16px ✕ icon. It sits at `opacity: 0` and shows on card `:hover` **and on `:focus-visible`**, so keyboard users can reach it. On hover its border and icon turn accent. The `aria-label` is "Remove {title} from {collection}".
- **Remove flow**: remove optimistically, then refetch the current page so the next item shifts in. If this empties the current page and it isn't page 1, go to the previous page. Show a brief undo toast if the project already has a toast system; otherwise skip it.
- **Posters**: when a real poster exists, render it with `object-fit: cover` and keep the badge, tick, remove button and rank overlay on top. Add a bottom scrim so overlaid text keeps 4.5:1 contrast. The typographic placeholder in the design is the fallback for titles with no poster.
- **Placeholders in the design**: `[YEAR]` and `[RATING]` stand in for real fields. Bind them to the model. If a title has no rating, hide the pill instead of showing a dash.

---

## 7. Sorting

### Options

| Value (URL) | Label | Order | Tie-breaker |
| --- | --- | --- | --- |
| `added` *(default)* | Recently added | `added_at` desc | `id` desc |
| `title` | Title A–Z | title asc, case-insensitive, locale-aware collation | `id` asc |
| `rating` | Rating | rating desc, **nulls last** | `added_at` desc |
| `year` | Release year | release date/year desc, **nulls last** | title asc |

Leading articles ("The", "A") are **not** stripped unless the site already does that elsewhere. Match the existing behaviour.

### Where it applies

- **Liked** and **Watch later** each have their own sort.
- **Top** has no sort. Its order is always rank ascending.
- The sort control is shown only when the collection has **2 or more** items.

### URL and state

- Every collection has its own params, so changing one doesn't reset the others:
  - `?liked_sort=rating&liked_page=2&later_sort=added&later_page=1`
- Leave default values out of the URL (`added`, page `1`) to keep links clean.
- An unknown or invalid sort value falls back to `added` without an error.
- **Changing the sort resets that collection to page 1.**
- After a sort or page change, keep the scroll at that section's anchor (`#liked`, `#later`) instead of jumping to the top. With a full page load, append the anchor to the URL. With client-side fetching, scroll the section into view only if its heading has left the viewport.
- Optional: remember the viewer's last sort per collection in `localStorage` and use it when the URL has no sort param. Wrap reads and writes in try/catch, and let the URL always win.

### Where sorting happens

- Sort **in the database query**, before `LIMIT/OFFSET` or the cursor, so sort order spans the whole collection and not just the visible page. It's a bug if page 2 under "Title A–Z" can contain a title that sorts before the last title on page 1.
- Whitelist the sort values and map each one to an ORDER BY clause in code. Never interpolate the raw param into SQL.
- Add indexes that fit the queries, e.g. `(user_id, collection, added_at DESC)`. Add others for title/rating/year only if the collections are big enough to need them. Say which ones you added and why.

### Control

- A native `<select>` with a visible `<label>` "SORT" (12–13px, tracking 0.18em, muted). The select is 44px tall, with a `#101010` fill, a `#26261F` border, 10px radius, 0 14px padding, Outfit 14px.
- It sits in the heading row's right cluster, in this order: `SORT` [select] then the count (`24 TITLES`), with a 20px gap.
- It changes on `change`. With JS it fetches without a full reload. Without JS, wrap it in a `<form method="get">` with a submit button that is visually hidden until focused, so the control still works.
- While a sort or page is loading, keep the old cards visible at reduced opacity (`0.5`) and set `aria-busy="true"` on the grid. Don't show a spinner in place of the grid.

---

## 8. Pagination

- **Page size: 10** (two rows of five).
- **Pager layout**: `Showing 11–20 of 24` on the left (13px, muted). On the right, with an 8px gap: a prev arrow, the page numbers, and a next arrow.
- **Buttons**: 44px tall with a 10px radius. Arrows are 44×44 icon buttons with `aria-label` "Previous page of {collection}" / "Next page of {collection}". Page numbers have `min-width: 44px` and `padding: 0 14px`.
  - The current page is accent-filled with dark text, weight 600 and `aria-current="page"`.
  - Other pages are transparent with a `#26261F` border and `#B9B9B2` text. On hover the border and text turn accent.
  - At the ends, the arrows are rendered `disabled` with a `#1C1C19` border and `#46463D` text.
- **Truncation** when there are more than 7 pages: always show the first and last page, the current page, and one page on each side of it, with `…` in the gaps (not focusable). Example: `1 … 4 5 6 … 12`.
- **Single page**: keep the pager with `Showing 1–n of n` and both arrows disabled, as drawn.
- **Out-of-range page** (e.g. `?liked_page=99`): clamp to the last page. Don't show an empty grid.
- **Top**: no pager.
- Wrap the pager in `<nav aria-label="{collection} pages">`.

---

## 9. Data / API contract

Adapt this to the stack. The shape below is a suggestion.

```
GET /api/me/collections/{collection}?sort=added|title|rating|year&page=1&page_size=10
  collection ∈ {liked, later, top}   (top ignores sort and page; returns ≤5 by rank)

200 {
  "collection": "liked",
  "sort": "added",
  "page": 2,
  "page_size": 10,
  "total": 24,
  "total_pages": 3,
  "items": [
    {
      "id": 123,
      "slug": "lanterns",
      "title": "Lanterns",
      "type": "tv_show",           // movie | tv_show | cartoon
      "year": 2026,                // nullable
      "rating": 8.1,               // nullable
      "poster_url": "https://…",   // nullable → placeholder art
      "added_at": "2026-09-13T10:22:00Z",
      "rank": null                 // 1–5 for Top only
    }
  ]
}

DELETE /api/me/collections/{collection}/{title_id}   → 204
```

For the hero and chips, the page needs the username, email, `date_joined`, and a total count per collection. Get the counts in one query, not three round-trips if avoidable.

If `added_at` doesn't exist on the join table, add it in a migration and backfill existing rows with the creation timestamp if one is available, or else with the migration time. Say which you did.

---

## 10. Accessibility

- Use only real elements: `<a href>` for navigation (title links, chips, CTAs) and `<button>` for actions (remove, pager, theme toggle, rank slots). Don't put `onClick` on a div or span.
- Every hit target is at least 44px.
- Icon-only buttons have an `aria-label` that names the title and the collection.
- Section icons are `aria-hidden`. The `<h2>` carries the name.
- Text contrast is at least 4.5:1, including muted `#8C8C86` on `#0A0A0A` and destructive `#F2685E` on its tinted fill. Check this in light theme too.
- Focus rings stay visible. Don't remove outlines without a replacement: use a 2px accent `outline` with a 2px offset.
- Keep `prefers-reduced-motion`: turn off the hover lift and transitions.

---

## 11. Acceptance checklist

Claude Code should confirm each item, and add automated tests where the project has a test setup.

**Layout and visuals**
- [ ] Header, hero, chips, three sections and footer match the spec at 1440px.
- [ ] Sections use icon tiles (heart / clock / trophy); there are no 01/02/03 numerals.
- [ ] Sign out has the log-out icon and the destructive red styling, with a working hover state.
- [ ] Cards reuse the existing homepage card component.
- [ ] The grid drops to 4 / 3 / 2 columns at the breakpoints, and posters stay 2:3.

**Sorting**
- [ ] Liked and Watch later each sort independently by Recently added, Title A–Z, Rating and Release year.
- [ ] Sorting is applied in the query across the whole collection, verified with a test that seeds 25+ items and checks the page boundaries.
- [ ] Null ratings and years sort last.
- [ ] Changing the sort resets that collection to page 1 and leaves the other collection's sort and page alone.
- [ ] Invalid sort values fall back to `added`.
- [ ] Sort values are whitelisted, and no raw param reaches the SQL.
- [ ] The sort control is hidden when the collection has fewer than 2 items.
- [ ] The sort control still works with JS disabled.

**Pagination**
- [ ] 10 items per page, with the "Showing x–y of n" count correct.
- [ ] Prev/next are disabled at the ends, and the current page has `aria-current="page"`.
- [ ] Truncation with ellipses works past 7 pages.
- [ ] Out-of-range pages clamp to the last page.
- [ ] After a page or sort change, the viewport stays at that section.
- [ ] The partial last page shows a spanning `EmptyPanel`, and a full last row shows none.
- [ ] Removing the last item on a page moves to the previous page.

**Top**
- [ ] Shows at most 5 titles in rank order, with no sort and no pager.
- [ ] Unfilled ranks render as `RankSlot` buttons (02–05).

**Accessibility**
- [ ] The remove button is reachable by keyboard and shows on focus.
- [ ] All targets are at least 44px, and all icon buttons are labelled.
- [ ] Reduced motion is respected.
- [ ] Contrast passes in both themes.
