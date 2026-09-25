# AI Companion Step 0: What Theoria Already Knows

This document is the starting agreement for the AI Movie Companion. It is
based on the current Theoria code, not on assumptions.

## The simple picture

Theoria has two separate places for information:

1. The **user database**: accounts and each user's personal movie lists.
2. The **movie catalogue database**: movie and TV-show information such as
   title, length, description, rating, language, country, and poster.

The AI will read a user's lists from the user database, then look up matching
movie details in the catalogue database.

## What already exists

### 1. User account

Every signed-in person already has an account with:

- a unique email address;
- a username;
- a secure logged-in session.

There is no separate personal-taste profile table yet. This is fine for the
first recommendation version because the existing lists are already useful
taste signals.

### 2. Three personal movie lists

Every user can have exactly one list of each kind:

| Existing list | What it tells the companion |
| --- | --- |
| Liked | The user enjoyed or is positive about this title. |
| Top | A strong signal: this is one of the user's favourites. |
| Watch later | The user is interested but may not have watched it. |

Each list item can point to either a movie or a TV show. The system also keeps
the order of items in the `Top` list, which can later help us understand a
user's strongest favourites.

### 3. Movie catalogue facts

For a movie, Theoria can already read useful recommendation facts including:

- title;
- release date;
- runtime;
- overview/description;
- IMDb rating;
- language and country;
- related people and companies.

For TV shows, the catalogue has similar information plus genre information.

## What does not exist yet

The current code has no saved answer for these questions:

| Missing information | Why the companion needs it |
| --- | --- |
| Watched | Avoid repeating a title by default. |
| Disliked | Never recommend a title the user rejected. |
| Not interested | Do not keep suggesting something the user skipped. |
| Personal rating | Understand how strongly the user liked or disliked a title. |
| Viewing preferences | Honour choices such as short movies, language, or light mood. |

This means the first version can personalize from Liked, Top, and Watch later,
but it cannot honestly promise to know every movie a user has watched.

## The data we will add

We should add one new user-side record called **Title feedback**. It is not a
new movie; it is one user's private relationship with one existing movie or TV
show.

For each user and title, it can store:

- `watched`: yes or no;
- `disliked`: yes or no;
- `not_interested`: yes or no;
- `personal_rating`: optional number from 1 to 5;
- `updated_at`: when the user last changed this information.

A title can be both watched and liked. That is why watched and disliked must
not be one single pick-list: they describe different things.

The existing Liked, Top, and Watch later lists stay exactly as they are. We do
not replace or duplicate them.

## The first AI data summary

Once the companion begins, Python will prepare a small summary like this:

```text
User: Aydos
Strong favourites: Interstellar, Whiplash
Liked: Arrival, The Prestige
Saved for later: Blade Runner 2049
Watched: ...
Disliked: ...
Request: "I want a short, funny movie tonight"
```

The `Watched` and `Disliked` lines will be empty until title feedback is added.
Python then finds safe candidate titles before asking Grok to write its reply.

## Important rules agreed in Step 0

1. Theoria's database is the source of truth for a user's movie memory.
2. Grok is not the memory. Grok only receives a short summary needed for one
   recommendation.
3. Existing Liked, Top, and Watch later lists are the first real taste signals.
4. Watched, Disliked, Not interested, and Personal rating are new data we must
   add before making strong promises about avoiding repeat recommendations.
5. Movies and TV shows are both supported from the beginning.
6. The assistant must never expose one user's lists or preferences to another
   user.

## Step 0 is complete when

- We use the three existing lists as the first input for recommendations.
- We add the Title feedback data later as a small, separate task.
- We build the recommendation code so it works even while watched/disliked
  feedback is still empty.
