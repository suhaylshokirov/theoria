# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

People who take their taste in cinema seriously. They browse movies, shows, cartoons, people and
studios, find what they like, and keep it: what they love (Liked), what they rank highest (Top, an
ordered list) and what they mean to watch next (Watch later). Any collection can be shared publicly
with a single link, so a reader's taste is something they can show, not just store. A smaller group
are movie nerds who want to dig further: every title page holds everything worth knowing about it,
and the analytics page is built for them. Readers use English, Russian or Uzbek (the language is a
cookie, not a URL prefix), on desktop and on phones.

## Product Purpose

Theoria is a movie and TV platform for people who take their taste in cinema seriously: explore
everything, collect what matters to you, share it with anyone. Browsing gets a reader in, collections
are what they come back for, and the detail pages and analytics serve the readers who want to go
deeper. It is fed by a real TMDB → S3 data lake → PostgreSQL star-schema pipeline refreshed nightly and
read through a Django front end, but the engineering is not the product's job to show off. Success is a
reader finding titles they care about and building a collection worth sharing.

## Positioning

A platform built around a reader's taste, not a listing site and not a dashboard. Internally, the
reference point is a leaner IMDb, and the direction is away from it: new features should give readers
more ways to express and share their taste, not just more facts to look up. Never use the IMDb
comparison in user-facing copy. The catalogue is deliberate, not incidental (roughly 200 movies per
decade from the 1970s on, plus a rolling window of recent releases, and shows discovered one first-air
year at a time), which is what makes the analytics page and decade and genre browsing honest. A site
that ranks "whatever is trending" cannot offer that even time axis.

## Operating Context

- Data refreshes nightly (GitHub Actions → Neon); the site reads it with a Redis read cache keyed by a
  data version, so a new load needs no deploy.
- Hosted on Vercel as one Python function in `fra1`, beside Neon. Locally Django reads a Postgres
  replica of the warehouse.
- Pages are server-rendered Django templates with small progressive-enhancement scripts; no framework,
  no build step.
- Interface strings ship in en/ru/uz via Django catalogs (compiled `.mo` files are committed).

## Capabilities and Constraints

- Browse: movies, TV shows (seasons and episodes), people (all departments), studios, collections,
  cartoons, a "Browse"/explore view, search; per-title ratings (IMDb is the rating of record, TMDB kept
  for comparison), trailers/clips, credits.
- Analytics dashboard: charts and rankings from the warehouse.
- Accounts: signup/login with email verification, username change, saved collections.
- Three interface languages. Translate what readers see, never what code compares. Uzbek prose data
  does not exist, so Uzbek reads English for overview/tagline/biography behind a quiet marker.
- Rendered copy says "Movies", not "Films" or "titles" (the "Filmography" heading is kept).
- Never surface internal implementation names (table/column names, `.sql` filenames, surrogate keys,
  query or script names) in any user-facing string.
- The in-page AI assistant is off-limits: leave `assistant/` and its helpers alone.
- Scale: a live product with real readers. Infrastructure is added when a measured limit calls for it,
  never ruled out on principle (`docs/architecture.md` §11).
- Genuinely undecided: nothing recorded.

## Brand Commitments

Name: **Theoria**. The incumbent identity is a single design system in
`django_app/static/css/theoria.css` — a "catalog sheet" (contact sheets, catalog cards, accession
numbers) on white paper with hairline rules, Archivo/Instrument Sans/Spline Sans Mono/Work Sans, and
one lime used only as the *measurement mark*. It is recorded here only as the incumbent that exists,
not as a visual direction this init is deciding; visual work reads the stylesheet directly.

## Evidence on Hand

- Real, live data: 1,246 movies, 740 TV shows (4,192 seasons, 85,298 episodes), 255,088 people,
  IMDb ratings for nearly every title, 18,230 movie trailers/clips (counts as of 2026-10-06 in
  `README.md`; the nightly job moves them slowly).
- Posters, backdrops, profile photos and logos come from TMDB; trailers are YouTube keys.
- Screenshots of home, analytics, catalog and detail pages in `docs/screenshots/`.
- No testimonials, user counts or press exist; none should be invented.

## Product Principles

1. **Measured, not decorated.** A number, bar or chart earns its place by being a real measurement from
   the archive; lime and emphasis mark what was measured, never ornament.
2. **The title is the subject.** Posters, backdrops and people lead; the instrument recedes until the
   reader wants the numbers.
3. **One catalog, three languages.** Every page reads naturally in en/ru/uz; layouts must survive
   longer Cyrillic strings and a missing translation falling back to English quietly.
4. **Say what it shows, not where it came from.** Captions describe the content ("by decade",
   "release order"), never warehouse internals.
5. **Fails open and stays fast.** Caches, translations and optional data degrade silently; no page
   depends on an optional service being up.

## Accessibility & Inclusion

Contrast ratios are already verified in the token set (ink 19.7:1, muted ink 7.6:1, faint 4.8:1 on
paper; lime is a fill only, never text on its own, with the printed value as the relief channel).
Both light and dark (`prefers-color-scheme`) themes are supported. Mobile web is a first-class
surface with opt-in Playwright responsive-layout tests (`tests/ui`). Cyrillic and Uzbek Latin text
must render correctly.
