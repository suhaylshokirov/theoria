"""Warehouse vocabulary the site displays but TMDB does not translate.

Departments, job titles and title statuses arrive from TMDB as English
strings and are stored that way; they are also *logic* (`department ==
"Acting"`, DEPARTMENT_ORDER's sort ranks), so the database values and every
comparison stay raw English. Only the rendered label is translated, through
the display_*() helpers below.

The gettext_noop() calls have no runtime effect -- they exist so
`makemessages` extracts each msgid into the .po files. gettext returns the
msgid unchanged when a translation is missing, so the long tail of job titles
(the warehouse holds ~860 distinct ones) needs no entry here: an untranslated
job simply renders in English. Only the head of the distribution is listed
(by fact_credit / fact_series_credit row count, measured 2026-09-24; the top
~80 jobs cover most of every credit list a reader actually sees).
"""

from django.utils.translation import gettext, gettext_noop

# --- Departments -------------------------------------------------------------
# The twelve TMDB reports on films, plus "Creation" (fact_series_credit's
# created-by rows). "Actors" is TMDB's own anomaly on two credits.
DEPARTMENTS = [
    gettext_noop("Acting"),
    gettext_noop("Directing"),
    gettext_noop("Writing"),
    gettext_noop("Production"),
    gettext_noop("Camera"),
    gettext_noop("Editing"),
    gettext_noop("Sound"),
    gettext_noop("Art"),
    gettext_noop("Costume & Make-Up"),
    gettext_noop("Visual Effects"),
    gettext_noop("Lighting"),
    gettext_noop("Crew"),
    gettext_noop("Creation"),
    gettext_noop("Actors"),
]

# --- Job titles --------------------------------------------------------------
JOBS = [
    gettext_noop("Actor"),
    gettext_noop("Director"),
    gettext_noop("Creator"),
    gettext_noop("Producer"),
    gettext_noop("Executive Producer"),
    gettext_noop("Co-Executive Producer"),
    gettext_noop("Co-Producer"),
    gettext_noop("Associate Producer"),
    gettext_noop("Supervising Producer"),
    gettext_noop("Consulting Producer"),
    gettext_noop("Visual Effects Producer"),
    gettext_noop("Screenplay"),
    gettext_noop("Writer"),
    gettext_noop("Story"),
    gettext_noop("Teleplay"),
    gettext_noop("Story Editor"),
    gettext_noop("Executive Story Editor"),
    gettext_noop("Editor"),
    gettext_noop("Assistant Editor"),
    gettext_noop("First Assistant Editor"),
    gettext_noop("Sound Editor"),
    gettext_noop("Supervising Sound Editor"),
    gettext_noop("Sound Effects Editor"),
    gettext_noop("Dialogue Editor"),
    gettext_noop("Music Editor"),
    gettext_noop("Visual Effects Editor"),
    gettext_noop("Original Music Composer"),
    gettext_noop("Songs"),
    gettext_noop("Musician"),
    gettext_noop("Theme Song Performance"),
    gettext_noop("Director of Photography"),
    gettext_noop("Camera Operator"),
    gettext_noop("First Assistant Camera"),
    gettext_noop("Still Photographer"),
    gettext_noop("First Assistant Director"),
    gettext_noop("Second Assistant Director"),
    gettext_noop("Casting"),
    gettext_noop("Production Design"),
    gettext_noop("Art Direction"),
    gettext_noop("Assistant Art Director"),
    gettext_noop("Set Decoration"),
    gettext_noop("Set Dresser"),
    gettext_noop("Set Designer"),
    gettext_noop("Storyboard Artist"),
    gettext_noop("Background Designer"),
    gettext_noop("Painter"),
    gettext_noop("Costume Design"),
    gettext_noop("Costume Supervisor"),
    gettext_noop("Costumer"),
    gettext_noop("Set Costumer"),
    gettext_noop("Makeup Artist"),
    gettext_noop("Hairstylist"),
    gettext_noop("Sound Designer"),
    gettext_noop("Sound Re-Recording Mixer"),
    gettext_noop("Foley Artist"),
    gettext_noop("Boom Operator"),
    gettext_noop("ADR Mixer"),
    gettext_noop("Visual Effects Supervisor"),
    gettext_noop("Visual Effects Coordinator"),
    gettext_noop("Digital Compositor"),
    gettext_noop("Compositor"),
    gettext_noop("Compositing Artist"),
    gettext_noop("CG Supervisor"),
    gettext_noop("VFX Artist"),
    gettext_noop("Special Effects"),
    gettext_noop("Special Effects Technician"),
    gettext_noop("Animation"),
    gettext_noop("Animation Director"),
    gettext_noop("Key Animation"),
    gettext_noop("Opening/Ending Animation"),
    gettext_noop("Modeling"),
    gettext_noop("Stunts"),
    gettext_noop("Stunt Double"),
    gettext_noop("Stunt Coordinator"),
    gettext_noop("Stunt Driver"),
    gettext_noop("Utility Stunts"),
    gettext_noop("Electrician"),
    gettext_noop("Lighting Technician"),
    gettext_noop("Gaffer"),
    gettext_noop("Grip"),
    gettext_noop("Key Grip"),
    gettext_noop("Driver"),
    gettext_noop("Script Supervisor"),
    gettext_noop("Unit Production Manager"),
    gettext_noop("Production Manager"),
    gettext_noop("Production Supervisor"),
    gettext_noop("Production Coordinator"),
    gettext_noop("Production Assistant"),
    gettext_noop("Location Manager"),
    gettext_noop("Property Master"),
    gettext_noop("Thanks"),
]

# --- Statuses ----------------------------------------------------------------
# dim_movie.status (live: "Released") and dim_series.status (live: "Ended",
# "Returning Series", "Canceled"), plus the other values TMDB documents.
STATUSES = [
    gettext_noop("Released"),
    gettext_noop("Rumored"),
    gettext_noop("Planned"),
    gettext_noop("In Production"),
    gettext_noop("Post Production"),
    gettext_noop("Canceled"),
    gettext_noop("Returning Series"),
    gettext_noop("Ended"),
    gettext_noop("Pilot"),
]


def _display(value):
    """Translate a warehouse value; empty/None pass through, unknown values
    come back as the English msgid (gettext's own fallback)."""
    return gettext(value) if value else value


def display_department(name):
    return _display(name)


def display_job(job):
    return _display(job)


def display_status(status):
    return _display(status)
