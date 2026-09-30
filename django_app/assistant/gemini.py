"""The small, server-side Gemini bridge for Theoria's movie companion."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import config

try:
    from google import genai
except ImportError:  # Keeps local commands usable before dependencies are installed.
    genai = None


logger = logging.getLogger(__name__)
MAX_REPLY_LENGTH = 1_200
PROFILE_TITLES_PER_LIST = 6
RECOMMENDATION_INTENTS = frozenset({"recommend", "refine"})
CONVERSATION_INTENTS = frozenset(
    {
        "small_talk",
        "recommend",
        "refine",
        "repeat_concern",
        "feedback",
        "taste_assessment",
        "title_question",
    }
)
CONVERSATION_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": sorted(CONVERSATION_INTENTS)},
        "reply": {
            "type": "string",
            "description": "A concise direct reply. Leave empty for recommend or refine.",
        },
    },
    "required": ["intent", "reply"],
}


@dataclass(frozen=True)
class ConversationPlan:
    """A validated decision about whether this turn needs catalogue results."""

    intent: str
    reply: str

    @property
    def needs_recommendations(self):
        return self.intent in RECOMMENDATION_INTENTS


def _titles(summary, kind):
    """Keep the provider context useful and deliberately small."""
    return [
        {
            "title": title["title"],
            "year": title.get("year"),
            "personal_rating": title.get("personal_rating"),
        }
        for title in summary.get(kind, [])[:PROFILE_TITLES_PER_LIST]
    ]


def _prompt(message, taste_summary, candidates, recent_turns=()):
    """Build the only information Theoria shares with Gemini for one reply."""
    context = {
        "request": message,
        "taste": {
            "liked": _titles(taste_summary, "liked"),
            "top": _titles(taste_summary, "top"),
            "watch_later": _titles(taste_summary, "watch_later"),
            "watched": _titles(taste_summary, "watched"),
            "disliked": _titles(taste_summary, "disliked"),
            "not_interested": _titles(taste_summary, "not_interested"),
        },
        "movie_options": [
            {
                "title": candidate["title"],
                "year": candidate.get("year"),
                "runtime_minutes": candidate.get("runtime"),
                "imdb_rating": candidate.get("imdb_rating"),
                "status": candidate["status"],
                "reason": candidate["reason"],
            }
            for candidate in candidates
        ],
        "recent_conversation": list(recent_turns),
    }
    if candidates:
        instructions = """You are Theoria's warm, concise movie companion. Explain why the
movie options below fit this person's request and stored taste. Recommend only
movies in movie_options. Never claim to know anything outside this data. If an
option has status 'on_list', say it is already on their Watch later list. Give
one short paragraph (no headings, no markdown list), maximum 100 words."""
    else:
        instructions = """You are Theoria's warm, concise movie companion. The person is
asking about their own taste, not for new picks. Using only the taste data
below, describe the pattern you see in what they like, dislike, and watch.
Do not suggest or invent any specific movies. Give one short paragraph (no
headings, no markdown list), maximum 100 words."""

    return instructions + "\n\nTheoria context:\n" + json.dumps(
        context, ensure_ascii=True, default=float
    )  # ratings arrive as Decimal


def generate_companion_reply(message, taste_summary, candidates, *, recent_turns=()):
    """Return Gemini's explanation, or ``None`` so the caller can use rules.

    Gemini never decides which catalogue records are returned to the browser.
    Theoria selects those records first; this function only improves the human
    explanation attached to that already-safe result.
    """
    if not config.GEMINI_API_KEY or genai is None:
        return None

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        interaction = client.interactions.create(
            model=config.GEMINI_MODEL,
            input=_prompt(message, taste_summary, candidates, recent_turns),
        )
        reply = (interaction.output_text or "").strip()
    except Exception:  # The rule-based assistant remains available on outage/quota errors.
        logger.warning("Gemini movie companion request failed", exc_info=True)
        return None

    return reply[:MAX_REPLY_LENGTH] or None


def _conversation_plan_prompt(message, recent_turns, recent_recommendations):
    """Keep the model's job conversational before any catalogue query runs."""
    context = {
        "latest_message": message,
        "recent_conversation": list(recent_turns),
        "titles_already_shown": list(recent_recommendations),
    }
    return """You are Theoria's warm, concise movie companion. Classify the
latest message before the application decides whether to search its catalogue.

Use `recommend` when the person directly asks for new movie choices. Use
`refine` when they narrow or change a prior recommendation request. Use
`repeat_concern` when they say suggestions are repeated or unwanted. Use
`small_talk` for greetings and casual chat. Use `taste_assessment` when they
ask about their preferences. Use `feedback` for a reaction to a previously
shown title. Use `title_question` for a question about a title or prior choice.

For `recommend` and `refine`, leave reply empty: the application will retrieve
trusted catalogue options and ask you to write the final response. For every
other intent, write a direct, human reply of at most 60 words. Never claim to
remember a title unless it appears in the supplied context. A repeat concern
must acknowledge the frustration and say no new picks will be shown in this
reply. Do not invent movie titles or facts.

Theoria context:
""" + json.dumps(context, ensure_ascii=True)


def _fallback_conversation_plan(message):
    """Keep basic chat useful during a provider outage without false claims."""
    normalized = re.sub(r"\s+", " ", message.lower()).strip()
    if normalized in {"hi", "hello", "hey", "good morning", "good evening"}:
        return ConversationPlan(
            "small_talk", "Hi — tell me what you feel like watching, and I will help narrow it down."
        )
    if any(phrase in normalized for phrase in ("same movie", "same movies", "repeat", "repeating", "already suggested", "again")):
        return ConversationPlan(
            "repeat_concern",
            "You’re right to call that out. I will avoid the titles already shown in this chat before suggesting anything else.",
        )
    if any(
        phrase in normalized
        for phrase in (
            "my taste", "assess", "opinion", "what do you think", "how would you describe",
            "review my", "analyze my", "analyse my", "describe my", "understand my",
        )
    ):
        return ConversationPlan("taste_assessment", "")
    if "thank" in normalized:
        return ConversationPlan("small_talk", "You’re welcome. I’m here whenever you want another option.")
    return ConversationPlan("recommend", "")


def plan_companion_message(message, recent_turns, recent_recommendations):
    """Ask Gemini how to handle the turn, with a deterministic safe fallback."""
    if not config.GEMINI_API_KEY or genai is None:
        return _fallback_conversation_plan(message)

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        interaction = client.interactions.create(
            model=config.GEMINI_MODEL,
            input=_conversation_plan_prompt(message, recent_turns, recent_recommendations),
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": CONVERSATION_PLAN_SCHEMA,
            },
        )
        payload = json.loads((interaction.output_text or "").strip())
        intent = payload.get("intent")
        reply = payload.get("reply", "")
        if intent not in CONVERSATION_INTENTS or not isinstance(reply, str):
            raise ValueError("Gemini returned an invalid conversation plan")
    except Exception:
        logger.warning("Gemini conversation planning failed", exc_info=True)
        return _fallback_conversation_plan(message)

    return ConversationPlan(intent, reply.strip()[:MAX_REPLY_LENGTH])
