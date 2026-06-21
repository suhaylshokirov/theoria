# Theoria

A movie analytics platform (mini IMDb + analytics) built to learn real Data Engineering:

```
TMDB API → S3 Data Lake (Bronze/Silver/Gold) → PostgreSQL warehouse (star schema) → Django UI
```

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in real values
python -c "import config"     # verify env is set up (fails loud if a var is missing)
pytest                        # run all tests
python manage.py runserver    # start Django (later phase)
```

See `CLAUDE.md` for the full task roadmap, `docs/architecture.md` for design,
and `for_learning.md` for the running learning log.
