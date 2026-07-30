from __future__ import annotations

import re

_ENGLISH_QUERY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,.'’&()/+:-]*$")


def collect_search_queries(scenes: dict) -> tuple[str, ...]:
    queries: list[str] = []
    seen: set[str] = set()
    for scene in scenes["scenes"]:
        brief = scene["searchBrief"]
        if brief["language"] != "en":
            raise ValueError("localized V2 asset search briefs must use English")
        for raw in brief["queries"]:
            query = " ".join(str(raw).split())
            if not _ENGLISH_QUERY.fullmatch(query):
                raise ValueError(f"invalid English asset search query: {query}")
            key = query.casefold()
            if key not in seen:
                seen.add(key)
                queries.append(query)
    return tuple(queries)
