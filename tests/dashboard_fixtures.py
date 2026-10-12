"""Canned result sets for the analytics dashboard's fifteen queries (Task 133).

Shared by the view, cache, translation and insights tests so they all exercise
the same realistic shape. `full_rows()` fills every panel with a little data;
`empty_rows()` is a freshly created warehouse with nothing loaded yet.
"""

from decimal import Decimal

from analytics.cached_reads import DASHBOARD_QUERIES


def empty_rows():
    rows = {filename: [] for filename in DASHBOARD_QUERIES}
    return rows


def full_rows():
    rows = empty_rows()
    rows["dashboard_kpis.sql"] = [{
        "movie_count": 1218,
        "series_count": 734,
        "rated_episode_count": 39999,
        "median_movie_rating": Decimal("7.30"),
        "median_series_rating": Decimal("7.90"),
        "median_return_multiple": Decimal("4.52"),
        "non_english_pct": Decimal("9.5"),
        "imdb_tmdb_correlation": Decimal("0.87"),
    }]
    rows["rating_distribution.sql"] = [
        {"kind": "movie", "band_start": Decimal("7.0"), "title_count": 307},
        {"kind": "movie", "band_start": Decimal("7.5"), "title_count": 276},
        {"kind": "series", "band_start": Decimal("8.0"), "title_count": 180},
    ]
    rows["budget_vs_revenue.sql"] = [
        {"movie_slug": "small-flop", "title": "Small Flop", "release_year": 2024,
         "poster_path": None, "budget": 2_000_000, "revenue": 1_000_000,
         "return_multiple": Decimal("0.50")},
        {"movie_slug": "cheap-hit", "title": "Cheap Hit", "release_year": 1999,
         "poster_path": "/hit.jpg", "budget": 1_500_000, "revenue": 300_000_000,
         "return_multiple": Decimal("200.00")},  # under the 5M callout floor
        {"movie_slug": "big-hit", "title": "Big Hit", "release_year": 1982,
         "poster_path": "/big.jpg", "budget": 10_000_000, "revenue": 800_000_000,
         "return_multiple": Decimal("80.00")},
        {"movie_slug": "epic", "title": "Epic", "release_year": 2022,
         "poster_path": "/epic.jpg", "budget": 650_000_000, "revenue": 1_000_000_000,
         "return_multiple": Decimal("1.54")},
    ]
    rows["return_bands_by_genre.sql"] = [{
        "genre_name": "Action", "movie_count": 40, "median_multiple": Decimal("4.02"),
        "under_1x": 2, "from_1x_to_2x": 6, "from_2x_to_5x": 16, "over_5x": 16,
    }]
    rows["studio_returns.sql"] = [{
        "studio_slug": "test-studio", "studio_name": "Test Studio", "movie_count": 12,
        "median_multiple": Decimal("3.7"), "avg_rating": Decimal("7.1"),
        "total_revenue": Decimal("45000000000"),
    }]
    rows["runtime_by_decade.sql"] = [
        {"decade": 1970, "movie_count": 202, "median_runtime": Decimal("110")},
        {"decade": 2020, "movie_count": 194, "median_runtime": Decimal("116")},
    ]
    rows["non_english_cinema_over_time.sql"] = [
        {"decade": 1970, "film_count": 202, "non_english_count": 50, "non_english_pct": Decimal("24.8")},
        {"decade": 2020, "film_count": 195, "non_english_count": 17, "non_english_pct": Decimal("8.7")},
    ]
    rows["genre_share_by_decade.sql"] = [
        {"decade": 1970, "genre_name": "Drama", "movie_count": 90, "decade_movies": 201, "share_pct": Decimal("44.8")},
        {"decade": 1970, "genre_name": "Action", "movie_count": 39, "decade_movies": 201, "share_pct": Decimal("19.4")},
        {"decade": 2020, "genre_name": "Action", "movie_count": 97, "decade_movies": 195, "share_pct": Decimal("49.7")},
    ]
    rows["films_by_production_country.sql"] = [
        {"country_name": "Japan", "film_count": 7, "film_share_pct": Decimal("0.6"), "avg_rating": Decimal("7.4")},
    ]
    rows["original_language_mix.sql"] = [
        {"language_code": "en", "language_name": "English", "movie_count": 90},
        {"language_code": "ja", "language_name": "Japanese", "movie_count": 10},
    ]
    rows["top_rated_directors.sql"] = [{
        "director_slug": "test-director", "director_name": "Test Director",
        "movie_count": 5, "avg_rating": Decimal("8.2"), "total_votes": 1000,
    }]
    rows["signature_partnerships.sql"] = [{
        "director_slug": "test-director", "director_name": "Test Director",
        "collaborator_slug": "test-composer", "collaborator_name": "Test Composer",
        "craft": "Original Music Composer", "films_together": 9,
        "first_year": 1990, "last_year": 2020,
    }]
    rows["season_trajectory.sql"] = [
        {"season_number": 1, "show_count": 300, "avg_rating": Decimal("7.66"), "avg_change_from_season_1": Decimal("0.00")},
        {"season_number": 2, "show_count": 229, "avg_rating": Decimal("7.61"), "avg_change_from_season_1": Decimal("-0.01")},
        {"season_number": 3, "show_count": 207, "avg_rating": Decimal("7.40"), "avg_change_from_season_1": Decimal("-0.20")},
        {"season_number": 4, "show_count": 173, "avg_rating": Decimal("7.30"), "avg_change_from_season_1": Decimal("-0.30")},
    ]
    rows["series_rating_by_status.sql"] = [
        {"status": "Ended", "series_count": 540, "median_rating": Decimal("7.90")},
        {"status": "Returning Series", "series_count": 118, "median_rating": Decimal("8.00")},
    ]
    rows["network_quality.sql"] = [
        {"network_name": "Netflix", "series_count": 82, "median_rating": Decimal("7.80")},
    ]
    return rows
