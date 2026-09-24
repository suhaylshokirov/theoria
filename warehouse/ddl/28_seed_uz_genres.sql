-- Task 93: Uzbek genre names, hand-written.
--
-- TMDB has no Uzbek genre vocabulary: genre/movie/list?language=uz returns
-- `"name": null` for every id, so there is nothing for the pipeline to fetch.
-- These 27 rows (every genre in dim_genre: movie and TV) are seed data and
-- live here, in a .sql file, rather than in the loader or a shell session.
--
-- The nightly loader only ever writes lang = 'ru' to genre_translation, so it
-- can never overwrite these. ON CONFLICT DO NOTHING keeps the file re-runnable
-- and stops it clobbering a later hand-correction.
--
-- Joined against dim_genre rather than inserted blind: genre_translation has an
-- FK to it, so on a fresh bootstrap (dim_genre still empty) this inserts
-- nothing and must be re-run once after the first pipeline load. Genre ids that
-- do not exist in dim_genre are skipped, not errors.
--
-- The names are a machine-assisted draft pending a native Uzbek reader's review,
-- same status as the interface catalog (Task 92).

INSERT INTO genre_translation (genre_id, lang, genre_name, ingestion_date)
SELECT v.genre_id, 'uz', v.genre_name, CURRENT_DATE
FROM (VALUES
    (12,    'Sarguzasht'),
    (14,    'Fentezi'),
    (16,    'Animatsiya'),
    (18,    'Drama'),
    (27,    'Dahshat'),
    (28,    'Jangari'),
    (35,    'Komediya'),
    (36,    'Tarixiy'),
    (37,    'Vestern'),
    (53,    'Triller'),
    (80,    'Jinoyat'),
    (99,    'Hujjatli'),
    (878,   'Ilmiy fantastika'),
    (9648,  'Detektiv'),
    (10402, 'Musiqa'),
    (10749, 'Melodrama'),
    (10751, 'Oilaviy'),
    (10752, 'Urush'),
    (10759, 'Jangari va sarguzasht'),
    (10762, 'Bolalar'),
    (10763, 'Yangiliklar'),
    (10764, 'Realiti-shou'),
    (10765, 'Ilmiy fantastika va fentezi'),
    (10766, 'Teleserial'),
    (10767, 'Tok-shou'),
    (10768, 'Urush va siyosat'),
    (10770, 'Televizion film')
) AS v (genre_id, genre_name)
JOIN dim_genre g ON g.genre_id = v.genre_id
ON CONFLICT (genre_id, lang) DO NOTHING;
