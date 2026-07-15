"""Conservative phrase-neighbour clustering for lexicon review.

This module intentionally groups only surface-near phrases (normalization,
containment, or strong character-bigram overlap).  It creates review evidence,
not semantic equivalence and never changes the active lexicon automatically.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Set, Tuple


_REPEATED_CHAR = re.compile(r"(.)\1+")


def _normalise(phrase: str) -> str:
    return _REPEATED_CHAR.sub(r"\1", phrase.strip())


def _bigrams(phrase: str) -> Set[str]:
    return {phrase[index:index + 2] for index in range(len(phrase) - 1)}


def _link_type(left: str, right: str, min_overlap: float) -> str:
    left_norm, right_norm = _normalise(left), _normalise(right)
    if left_norm == right_norm:
        return "normalised_variant"
    if len(left_norm) >= 2 and (left_norm in right_norm or right_norm in left_norm):
        return "contained_phrase"
    left_bigrams, right_bigrams = _bigrams(left_norm), _bigrams(right_norm)
    if left_bigrams and right_bigrams:
        overlap = len(left_bigrams & right_bigrams) / len(left_bigrams | right_bigrams)
        if overlap >= min_overlap:
            return "surface_overlap"
    return ""


def cluster_phrase_candidates(
    candidates: Iterable[Dict[str, Any]],
    min_overlap: float = 0.5,
) -> List[Dict[str, Any]]:
    """Return review-only clusters for candidates with concrete lexical links."""
    by_phrase = {
        str(candidate.get("phrase", "")).strip(): candidate
        for candidate in candidates
        if str(candidate.get("phrase", "")).strip()
    }
    phrases = sorted(by_phrase)
    parents = {phrase: phrase for phrase in phrases}
    links: Dict[Tuple[str, str], str] = {}

    def find(value: str) -> str:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for index, left in enumerate(phrases):
        for right in phrases[index + 1:]:
            link = _link_type(left, right, min_overlap)
            if link:
                union(left, right)
                links[(left, right)] = link

    groups: Dict[str, List[str]] = defaultdict(list)
    for phrase in phrases:
        groups[find(phrase)].append(phrase)

    clusters: List[Dict[str, Any]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda phrase: (-by_phrase[phrase].get("freq", 0), len(phrase), phrase))
        cluster_key = "\0".join(sorted(members))
        cluster_id = "cluster_" + hashlib.sha256(cluster_key.encode("utf-8")).hexdigest()[:12]
        member_entries = []
        link_types: Set[str] = set()
        for phrase in members:
            member_links = sorted({
                link for (left, right), link in links.items()
                if phrase in (left, right) and left in members and right in members
            })
            link_types.update(member_links)
            source = by_phrase[phrase]
            member_entries.append({
                "phrase": phrase,
                "freq": source.get("freq", 0),
                "style_keyness": source.get("style_keyness", 0),
                "link_types": member_links,
            })
        clusters.append({
            "cluster_id": cluster_id,
            "canonical_phrase": members[0],
            "members": member_entries,
            "link_types": sorted(link_types),
            "review_status": "pending",
            "note": "Surface-near candidate cluster; confirm semantic equivalence manually.",
        })

    return sorted(clusters, key=lambda cluster: (-len(cluster["members"]), cluster["canonical_phrase"]))
