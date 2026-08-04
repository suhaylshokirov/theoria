"""The collaboration graph, and shortest paths through it.

Two graphs exist in this project and they answer different questions. This one
is *reachability*: is there any chain of shared films between two people, and
what is the shortest? So it is deliberately wider than `fact_collaboration`,
which asks "who works together repeatedly" and is scoped to key credits — an
appearance far down the billing still connects two people even though it isn't
a working relationship worth ranking.

Nodes are people; two people share an edge if they are credited on the same
film. The film that produced each edge is kept, because the answer to "how are
these two connected" is the list of films, not the number of hops.

Why not SQL: a recursive CTE over this graph times out (measured, >60s), because
it re-expands the same nodes at every depth and Postgres cannot memoise the
visited set across iterations. Frontier expansion in Python does the same search
in well under a second because the visited set is the whole point of BFS.
Adjacency is ~75k rows and rebuilds in ~0.1s; it's cached against the warehouse's
data version rather than a clock, so it refreshes after a load and never during
a browsing session.
"""

from __future__ import annotations

import logging
from collections import deque

from django.db import connections

logger = logging.getLogger(__name__)

# Crew jobs that count as a connection. Cast is included in full — unlike
# fact_collaboration, which takes only the top ten billed, because a shared
# film is a shared film for reachability purposes. Crew is restricted to the
# principal roles: every film has a hundred-odd below-the-line credits, and
# including them makes almost any two people two hops apart through a shared
# visual-effects house, which is true and useless.
PATH_CREW_JOBS = (
    "Director", "Screenplay", "Writer", "Story", "Original Music Composer",
    "Director of Photography", "Editor", "Production Design", "Producer",
)

MAX_DEPTH = 6

_cache: dict[str, object] = {"version": None, "adjacency": None, "names": None}


def _data_version(cursor) -> str:
    """A cheap fingerprint of the loaded data, used as the cache key.

    Row count plus latest ingestion_date: both change on any load and neither
    requires scanning the table. A timestamp-based TTL would either serve stale
    edges after a load or rebuild for no reason during a quiet afternoon.
    """
    cursor.execute("SELECT count(*), max(ingestion_date) FROM fact_credit")
    count, latest = cursor.fetchone()
    return f"{count}:{latest}"


def _build_adjacency(cursor) -> tuple[dict[int, dict[int, int]], dict[int, str]]:
    """Return {person_id: {neighbour_id: connecting_movie_id}} and {person_id: name}."""
    placeholders = ", ".join(["%s"] * len(PATH_CREW_JOBS))
    cursor.execute(
        f"""
        SELECT movie_id, person_id
        FROM fact_credit
        WHERE department = 'Acting' OR job IN ({placeholders})
        """,
        PATH_CREW_JOBS,
    )

    by_movie: dict[int, list[int]] = {}
    for movie_id, person_id in cursor.fetchall():
        by_movie.setdefault(movie_id, []).append(person_id)

    adjacency: dict[int, dict[int, int]] = {}
    for movie_id, people in by_movie.items():
        for person in people:
            edges = adjacency.setdefault(person, {})
            for other in people:
                if other != person:
                    # setdefault keeps the first film that connected the pair
                    # rather than the last, so a path is reproducible.
                    edges.setdefault(other, movie_id)

    cursor.execute("SELECT person_id, name FROM dim_person")
    names = dict(cursor.fetchall())

    logger.info(
        "Collaboration graph built: %d people, %d films", len(adjacency), len(by_movie)
    )
    return adjacency, names


def get_graph() -> tuple[dict[int, dict[int, int]], dict[int, str]]:
    """Return the cached adjacency, rebuilding it if the warehouse has changed."""
    with connections["warehouse"].cursor() as cursor:
        version = _data_version(cursor)
        if _cache["version"] != version:
            adjacency, names = _build_adjacency(cursor)
            _cache.update(version=version, adjacency=adjacency, names=names)
    return _cache["adjacency"], _cache["names"]


def find_path(source_id: int, target_id: int, max_depth: int = MAX_DEPTH):
    """Shortest chain of shared films between two people, or None if unconnected.

    Returns a list of (person_id, movie_id, person_id) steps, or [] when source
    and target are the same person.

    Bidirectional: expands whichever frontier is smaller each round. A one-sided
    search from a hub reaches ~31,000 people at depth 2 and ~41,000 at depth 3,
    so meeting in the middle keeps the frontier small on at least one side and
    turns a d-deep search into two searches of depth d/2.
    """
    adjacency, _ = get_graph()
    if source_id == target_id:
        return []
    if source_id not in adjacency or target_id not in adjacency:
        return None

    # parent[node] = (previous_node, connecting_movie_id)
    forward: dict[int, tuple[int, int] | None] = {source_id: None}
    backward: dict[int, tuple[int, int] | None] = {target_id: None}
    forward_frontier = [source_id]
    backward_frontier = [target_id]
    depth = 0

    while forward_frontier and backward_frontier and depth < max_depth:
        depth += 1
        if len(forward_frontier) <= len(backward_frontier):
            frontier, seen, other = forward_frontier, forward, backward
        else:
            frontier, seen, other = backward_frontier, backward, forward

        next_frontier = []
        for node in frontier:
            for neighbour, movie_id in adjacency[node].items():
                if neighbour in seen:
                    continue
                seen[neighbour] = (node, movie_id)
                if neighbour in other:
                    return _stitch(forward, backward, neighbour)
                next_frontier.append(neighbour)

        if frontier is forward_frontier:
            forward_frontier = next_frontier
        else:
            backward_frontier = next_frontier

    return None


def _stitch(forward, backward, meeting_id):
    """Walk both halves out from the meeting node into one source->target chain."""
    steps = []

    node = meeting_id
    while forward[node] is not None:
        previous, movie_id = forward[node]
        steps.append((previous, movie_id, node))
        node = previous
    steps.reverse()

    node = meeting_id
    while backward[node] is not None:
        following, movie_id = backward[node]
        steps.append((node, movie_id, following))
        node = following

    return steps


def component_stats() -> dict[str, int | float]:
    """Shape of the graph itself, for the page to describe what it is searching.

    Connected components via BFS flood fill. Cheap enough to compute on the
    already-cached adjacency (one pass over every node and edge) that it isn't
    worth a table of its own.
    """
    adjacency, _ = get_graph()
    if not adjacency:
        return {"people": 0, "components": 0, "largest": 0, "largest_share": 0.0}

    seen: set[int] = set()
    sizes: list[int] = []
    for start in adjacency:
        if start in seen:
            continue
        size = 0
        queue = deque([start])
        seen.add(start)
        while queue:
            node = queue.popleft()
            size += 1
            for neighbour in adjacency[node]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        sizes.append(size)

    largest = max(sizes)
    return {
        "people": len(adjacency),
        "components": len(sizes),
        "largest": largest,
        "largest_share": round(100 * largest / len(adjacency), 1),
    }


def reset_cache() -> None:
    """Drop the cached graph. Used by tests; harmless in production."""
    _cache.update(version=None, adjacency=None, names=None)
