# Theoria AI Movie Companion - Product Requirements

## 1. Product idea

Theoria's AI Movie Companion helps a signed-in user choose a movie quickly.
It learns from the user's movie history and preferences, then recommends films
that fit the user's current request, such as a quiet night, a short morning
watch, or a specific mood.

The assistant is not a generic chatbot. It is a personal movie companion:

- It knows a user's positive and negative movie signals.
- It prefers films the user has not watched.
- It explains why each recommendation fits the user and the request.
- It clearly labels an already-watched suggestion when one is intentionally
  offered.
- It gets better when the user gives simple feedback.

## 2. Problem

Choosing a movie often takes longer than watching one. General-purpose AI can
suggest good movies, but it does not reliably know a person's taste, watch
history, or current situation. Theoria already has a catalogue and user movie
lists, so it can provide recommendations grounded in the user's own data.

## 3. Goals

1. Reduce the time a user spends deciding what to watch.
2. Recommend movies using durable user context, not only the current chat.
3. Avoid recommending watched or disliked movies by default.
4. Let a user express needs in natural language: mood, time available, time of
   day, company, genre, and intensity.
5. Make recommendations transparent: every suggestion includes a brief reason.
6. Improve future recommendations from explicit feedback.

## 4. Non-goals for the first version

- Replacing the existing movie search and browse pages.
- Automatically claiming that a user has watched a movie.
- Diagnosing a user's emotions or inferring sensitive personal information.
- Building a long-term free-form psychological profile from chat messages.
- Sending the entire database or a user's full history to the AI provider.
- Building social recommendations between users.

## 5. Primary user stories

### New user

As a new user, I can tell the assistant what I feel like watching and receive
useful popular or catalogue-based suggestions. The assistant should say that it
needs more feedback to personalize future choices.

### Returning user

As a returning user, I can ask, "What should I watch tonight?" and receive
three unwatched movies that fit my saved likes, top movies, watchlist, and
preferences.

### Avoid repeat recommendations

As a user, I should not receive a movie I marked watched or disliked unless I
ask for a rewatch, ask specifically about that title, or no suitable unwatched
option exists. In that case it must be labeled "Previously watched."

### Fast feedback

As a user, I can mark a recommendation as "sounds good", "not for me", or
"already watched". This is saved and affects later recommendations.

### Contextual request

As a user, I can say, "I have 90 minutes before bed and want something funny,
not too intense," and receive short, light, unwatched options.

## 6. Product behavior

The floating button in the lower-right corner opens the AI Movie Companion.

1. A signed-in user writes a request.
2. The backend reads the user's saved movie signals and preferences.
3. The backend filters the catalogue before calling the AI:
   - remove disliked movies;
   - remove watched movies by default;
   - prefer available unwatched movies;
   - use movie metadata such as genre, runtime, year, rating, and overview.
4. The backend sends Grok a compact context and a short candidate set, not the
   full database.
5. Grok returns a friendly answer with one to three recommendations and short
   reasons.
6. The user can open a movie, add it to a list, or give feedback.
7. Feedback updates the user's profile for the next conversation.

When a user is not signed in, the assistant can offer general catalogue
recommendations, but it must say that signing in enables personal suggestions.

## 7. Information the companion needs

### Movie signals (persistent)

Each user can have a relationship with a movie:

| Signal | Meaning | Recommendation effect |
| --- | --- | --- |
| liked | User enjoyed it | Find similar titles |
| top | One of the user's favourites | Strong taste signal |
| disliked | User did not enjoy it | Exclude similar themes carefully |
| watched | User has seen it | Do not recommend by default |
| watch_later | User saved it | Prefer it when it matches the request |
| not_interested | User rejected it | Exclude it from future suggestions |
| rating (optional) | 1-5 personal rating | Stronger positive or negative signal |

The existing liked, top, and watch-later lists can seed the first version.
Watched, disliked, not-interested, and rating should be added only by the owner
of the profile feature, so the data model stays coherent.

### Preference profile (persistent)

Store only preferences that are useful for movie choice and that the user can
see and edit:

- favourite genres and genres to avoid;
- preferred languages or countries;
- preferred movie length;
- preferred intensity (light, neutral, intense);
- time-of-day preferences, for example short/light in the morning;
- viewing company, for example alone, family, or friends;
- content boundaries the user deliberately chooses.

### Conversation context (temporary)

For a single request, retain only the most recent conversation turns needed to
understand follow-up questions. Do not treat all chat text as permanent profile
data. A user can explicitly save a preference when it matters.

## 8. Recommendation rules

These rules are deterministic and must run before the AI writes prose:

1. Start with the user's explicit request.
2. Exclude `disliked` and `not_interested` titles.
3. Exclude `watched` titles unless the user asks for a rewatch or there are no
   strong unwatched matches.
4. Promote `watch_later` titles when they match the request.
5. Compare candidates with liked and top movies using metadata and ratings.
6. Respect explicit constraints such as runtime, genre to avoid, and intensity.
7. Return a small, diverse set rather than ten nearly identical titles.
8. Label every recommendation as `Unwatched`, `On your list`, or `Previously
   watched`.
9. Explain each recommendation using visible facts, not invented personal
   claims.

The AI may interpret natural language and write the response. It must not be
the only source of filtering or the keeper of the user's history.

## 9. AI architecture

```text
User message
    -> Django assistant endpoint
    -> load user profile and movie signals
    -> filter and rank catalogue candidates
    -> send compact context + candidates to Grok
    -> validate structured recommendation response
    -> show recommendations and feedback controls
    -> save explicit feedback to profile data
```

Grok is called only from Django. Its API key stays in server environment
variables and is never put in browser JavaScript.

The Grok prompt should request structured output for each item:

- movie ID and title;
- status label (`unwatched`, `on_list`, or `previously_watched`);
- one-sentence reason;
- optional short follow-up question.

The frontend uses the movie ID to render trusted title, poster, and links from
Theoria's own catalogue. This prevents a model response from inventing a title
or linking to the wrong page.

## 10. Privacy and safety requirements

- A user can view and edit the preferences used for recommendations.
- A user can clear their saved preference profile and chat history.
- Only movie-related signals needed for the current recommendation are sent to
  Grok.
- Never send passwords, email addresses, authentication tokens, or unrelated
  profile fields to Grok.
- Document that an external AI provider processes the selected recommendation
  context.
- Keep API keys in deployment environment variables, never in Git or frontend
  code.
- Rate-limit the endpoint and record operational errors without logging private
  conversation content.

## 11. Success measures

- Median time from opening the assistant to selecting a movie.
- Percentage of recommendations opened, saved, or accepted.
- Percentage of suggestions rejected as already watched.
- Number of feedback signals per active user.
- User-reported usefulness of recommendations.
- Error rate and response time for the assistant endpoint.

## 12. Delivery plan

### Phase 0 - Agree on the contract

1. Approve this PRD with the team.
2. Agree with the authentication/profile owner on the exact movie-signal model
   and APIs they will provide.
3. Decide which data can leave the platform for Grok processing.
4. Define a small set of acceptance examples before coding.

### Phase 1 - Build a useful non-AI recommendation foundation

1. Add a server-side recommendation service.
2. Read existing liked, top, and watch-later lists.
3. Filter watched/disliked titles when that data is available.
4. Produce three ranked catalogue candidates with labels and reasons generated
   from rules.
5. Test the rules without any Grok API key.

This phase is important: it proves that personal context and movie selection
work even when an AI provider is unavailable.

### Phase 2 - Add the companion interface

1. Replace the floating demo button with an assistant panel.
2. Add a Django endpoint for recommendation requests.
3. Show candidate cards, movie links, labels, and loading/error states.
4. Add feedback controls: liked, not for me, already watched, and save for
   later.
5. Keep unauthenticated behavior clearly non-personal.

### Phase 3 - Integrate Grok

1. Add `XAI_API_KEY` and model configuration to local and deployment
   environments.
2. Build a server-only Grok client with timeouts and clear error handling.
3. Send Grok the request, compact profile summary, and pre-filtered candidates.
4. Require structured output and validate all returned movie IDs against the
   candidate set.
5. Fall back to the rule-based recommendation if Grok is unavailable.

### Phase 4 - Make it learn responsibly

1. Connect the final profile database fields.
2. Save explicit recommendation feedback.
3. Add an editable preference screen.
4. Add tests for repeat avoidance, dislikes, time constraints, and rewatch
   labeling.
5. Measure recommendation quality and refine ranking.

## 13. First acceptance examples

1. Given a user likes `Arrival` and has not watched `Ex Machina`, when they
   ask for thoughtful science fiction, the assistant can recommend `Ex
   Machina` and says why.
2. Given a user marked a title as disliked, that title is never recommended in
   a standard request.
3. Given a user watched `The Dark Knight`, a normal action request does not
   recommend it. A rewatch request may recommend it, labeled `Previously
   watched`.
4. Given a user asks for a movie under 100 minutes, every recommendation
   satisfies that limit when the catalogue has matching options.
5. Given no user profile data, the assistant gives transparent general
   recommendations and asks for a small amount of feedback.

## 14. Decisions the team must make before coding

1. What is the official source of truth for watched, disliked, and ratings?
2. Does a user enter preferences manually, or only through feedback at first?
3. How long should chat history be retained, if at all?
4. Is Grok allowed to receive movie titles, preference categories, and recent
   non-sensitive requests under the project's privacy policy?
5. What is the launch fallback if the Grok API is unavailable or exceeds its
   budget?
