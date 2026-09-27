from __future__ import annotations

import re
from dataclasses import dataclass

from career_radar.listings import JobListingInput


SOFTWARE_IT_KEYWORDS = (
    "forritari",
    "forritun",
    "hugbúnaður",
    "hugbúnaðarþróun",
    "tölvunarfræði",
    "upplýsingatækni",
    "kerfisstjóri",
    "kerfisrekstur",
    "tækniþjónusta",
    "notendaþjónusta",
    "skýjalausnir",
    "innviðir",
    "sjálfvirkni",
    "gagnasérfræðingur",
    "gagnagreining",
    "gagnavinnsla",
    "gervigreind",
    "software",
    "developer",
    "software engineer",
    "systems",
    "system administrator",
    "technical support",
    "application support",
    "implementation",
    "solutions",
    "infrastructure",
    "cloud",
    "devops",
    "data",
    "analytics",
    "bi",
    "qa",
    "testing",
    "ai",
    "llm",
    "agent",
    "automation",
)

ENGINEERING_KEYWORDS = (
    "verkfræðingur",
    "verkfræði",
    "tæknifræðingur",
    "tæknimaður",
    "tæknideild",
    "rafvirki",
    "rafeindavirki",
    "rafmagn",
    "vöruþróun",
    "rannsóknir",
    "þróun",
    "framleiðsla",
    "framleiðslu",
    "gæðamál",
    "gæðastjórnun",
    "prófanir",
    "staðfesting",
    "stýringar",
    "mælingar",
    "skynjarar",
    "viðhald",
    "viðhalds",
    "iðnaður",
    "orka",
    "engineer",
    "engineering",
    "biomedical",
    "medical device",
    "healthtech",
    "health tech",
    "r&d",
    "research",
    "development",
    "product development",
    "manufacturing",
    "production",
    "quality",
    "validation",
    "verification",
    "controls",
    "instrumentation",
    "sensors",
    "process",
    "industrial",
)

TRANSFERABLE_KEYWORDS = (
    "microsoft 365",
    "m365",
    "sharepoint",
    "workpoint",
    "powershell",
    "user support",
    "technical support",
    "troubleshooting",
    "implementation",
    "customer-facing",
    "customer facing",
    "process improvement",
    "systems administration",
    "technical customer service",
    "technical sales",
    "solutions consulting",
    "digital transformation",
    "technical documentation",
    "innleiðing",
    "notendaþjónusta",
    "tækniþjónusta",
    "þjónustuborð",
    "ferlaumbætur",
    "ráðgjöf",
)

TECHNOLOGY_KEYWORDS = (
    "python",
    "sql",
    "power bi",
    "powershell",
    "microsoft 365",
    "m365",
    "sharepoint",
    "azure",
    "aws",
    "c#",
    ".net",
    "javascript",
    "typescript",
    "react",
    "api",
    "rest",
    "database",
    "databases",
    "git",
    "docker",
)

ROLE_CONCEPT_KEYWORDS = (
    "technical project work",
    "technical sales",
    "technical customer service",
    "process improvement",
    "digital transformation",
    "product management",
    "implementation",
    "solutions consulting",
    "data-driven",
    "data driven",
    "regulated product development",
    "technical documentation",
    "verkefnastjórnun",
    "vöruþróun",
    "gæðastjórnun",
    "innleiðing",
    "ráðgjöf",
)

SENIOR_PATTERNS = (
    (re.compile(r"\bprincipal\b"), "principal_level"),
    (re.compile(r"\bdirector\b"), "director_level"),
    (re.compile(r"\bhead of\b"), "head_of_level"),
    (re.compile(r"\bsenior\b"), "senior_level"),
    (re.compile(r"\blead\b"), "lead_level"),
    (re.compile(r"\btech lead\b"), "tech_lead_level"),
)
HIGH_EXPERIENCE_PATTERNS = (
    re.compile(
        r"\b([5-9]|1[0-9])\+?\s*(years|yrs)\s+"
        r"(of\s+)?(relevant\s+)?experience\b"
    ),
    re.compile(
        r"\b([5-9]|1[0-9])\+?\s*ára\s+"
        r"(viðeigandi\s+)?reynsl"
    ),
    re.compile(
        r"\breynsl\w*\s+(af|í|in|with|within)?\s*"
        r"\w*\s*\b([5-9]|1[0-9])\+?\s*(ár|years|yrs)\b"
    ),
)
MANDATORY_LICENSE_RE = re.compile(
    r"\b(licensed|professional license|löggilt|löggilding|starfsleyfi)\b"
)
EXACT_KEYWORDS = {
    ".net",
    "ai",
    "api",
    "bi",
    "c#",
    "it",
    "qa",
    "r&d",
    "rest",
    "sql",
}


@dataclass(frozen=True, slots=True)
class RelevanceResult:
    """Rule-based relevance signals for a collected job candidate."""

    score: int
    label: str
    matched_tracks: list[str]
    matched_keywords: list[str]
    hard_negative_flags: list[str]
    needs_ai_review: bool


def normalize_search_text(value: str | None) -> str:
    """Normalize text for broad keyword matching."""

    if not value:
        return ""

    return " ".join(value.casefold().split())


def listing_search_text(listing: JobListingInput) -> str:
    """Combine listing fields used by the high-recall relevance filter."""

    return normalize_search_text(
        " ".join(
            field or ""
            for field in (
                listing.title,
                listing.company,
                listing.location,
                listing.category_guess,
                listing.description,
                listing.requirements,
                listing.skills_mentioned,
                listing.notes,
            )
        )
    )


def keyword_matches(search_text: str, keywords: tuple[str, ...]) -> list[str]:
    """Return keywords found in a normalized listing text."""

    matches = []
    for keyword in keywords:
        normalized_keyword = normalize_search_text(keyword)
        if not normalized_keyword:
            continue

        if normalized_keyword in EXACT_KEYWORDS:
            pattern = re.compile(
                r"(?<![\w.+#-])"
                + re.escape(normalized_keyword)
                + r"(?![\w.+#-])"
            )
            matched = pattern.search(search_text) is not None
        else:
            matched = normalized_keyword in search_text

        if matched:
            matches.append(keyword)

    return matches


def unique_preserving_order(values: list[str]) -> list[str]:
    """Deduplicate values while preserving their original order."""

    seen = set()
    unique_values = []

    for value in values:
        normalized_value = value.casefold()
        if normalized_value in seen:
            continue
        seen.add(normalized_value)
        unique_values.append(value)

    return unique_values


def hard_negative_flags(search_text: str) -> list[str]:
    """Flag obvious reasons a listing may be too senior or incompatible."""

    flags = []
    senior_flags = [
        flag
        for pattern, flag in SENIOR_PATTERNS
        if pattern.search(search_text)
    ]
    high_experience = any(
        pattern.search(search_text) is not None
        for pattern in HIGH_EXPERIENCE_PATTERNS
    )
    mandatory_license = MANDATORY_LICENSE_RE.search(search_text) is not None

    if senior_flags and high_experience:
        flags.append("senior_or_lead_with_high_experience")
    elif high_experience:
        flags.append("high_experience_requirement")

    if mandatory_license:
        flags.append("possible_mandatory_license")

    return unique_preserving_order(flags)


def score_listing_relevance(listing: JobListingInput) -> RelevanceResult:
    """Apply a high-recall rule filter to one collected listing.

    This is not the final fit score. It is a candidate-generation gate designed
    to find plausible roles worth reviewing or sending to an AI classifier.
    """

    search_text = listing_search_text(listing)

    software_matches = keyword_matches(search_text, SOFTWARE_IT_KEYWORDS)
    engineering_matches = keyword_matches(search_text, ENGINEERING_KEYWORDS)
    transferable_matches = keyword_matches(search_text, TRANSFERABLE_KEYWORDS)
    technology_matches = keyword_matches(search_text, TECHNOLOGY_KEYWORDS)
    concept_matches = keyword_matches(search_text, ROLE_CONCEPT_KEYWORDS)

    matched_tracks = []
    score = 0

    if software_matches or technology_matches:
        matched_tracks.append("software_it")
        score += 18
        score += min(24, 4 * len(software_matches))
        score += min(24, 4 * len(technology_matches))

    if engineering_matches:
        matched_tracks.append("engineering")
        score += 18
        score += min(28, 4 * len(engineering_matches))

    if transferable_matches or concept_matches:
        matched_tracks.append("transferable")
        score += 14
        score += min(20, 4 * len(transferable_matches))
        score += min(16, 4 * len(concept_matches))

    flags = hard_negative_flags(search_text)
    if "senior_or_lead_with_high_experience" in flags:
        score -= 30
    elif "high_experience_requirement" in flags:
        score -= 15

    if "possible_mandatory_license" in flags:
        score -= 15

    score = max(0, min(100, score))

    if flags and score < 35:
        label = "hard_negative"
    elif score >= 50:
        label = "strong_candidate"
    elif score >= 20:
        label = "possible_candidate"
    else:
        label = "likely_irrelevant"

    needs_ai_review = label in ("strong_candidate", "possible_candidate")
    matched_keywords = unique_preserving_order(
        [
            *software_matches,
            *engineering_matches,
            *transferable_matches,
            *technology_matches,
            *concept_matches,
        ]
    )

    return RelevanceResult(
        score=score,
        label=label,
        matched_tracks=matched_tracks,
        matched_keywords=matched_keywords,
        hard_negative_flags=flags,
        needs_ai_review=needs_ai_review,
    )
