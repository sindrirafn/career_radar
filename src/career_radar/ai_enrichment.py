from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from psycopg.types.json import Jsonb

from career_radar.candidates import CandidateLabelFilter
from career_radar.db import connect, transaction


PROMPT_VERSION = "candidate_ai_enrichment_v1"
DEFAULT_OPENAI_MODEL = "gpt-5"

AI_LABEL_FILTER_SQL = {
    "actionable": """
        c.rule_relevance_label in ('strong_candidate', 'possible_candidate')
        and c.review_status in ('unreviewed', 'approved')
    """,
    "strong": "c.rule_relevance_label = 'strong_candidate'",
    "possible": "c.rule_relevance_label = 'possible_candidate'",
    "irrelevant": "c.rule_relevance_label = 'likely_irrelevant'",
    "hard-negative": "c.rule_relevance_label = 'hard_negative'",
    "all": "true",
}
AI_REVIEW_ACTION_FILTER_SQL = {
    "ready": "recommended_action = 'review_for_promotion'",
    "needs-ai": "recommended_action = 'needs_ai_review'",
    "manual": "recommended_action = 'manual_review'",
    "reject": "recommended_action = 'reject'",
    "not-actionable": "recommended_action = 'not_actionable'",
    "all": "true",
}
AI_REVIEW_ACTION_FILTERS = tuple(AI_REVIEW_ACTION_FILTER_SQL)
AI_STATUS_ACTION_FILTERS = ("ready", "manual", "reject", "not-actionable")
AI_BULK_REVIEW_STATUSES = ("unreviewed", "approved", "rejected")

ARRAY_RESPONSE_FIELDS = (
    "tracks",
    "explicit_skills",
    "skill_gaps",
    "positive_signals",
    "concerns",
)
REQUIRED_RESPONSE_FIELDS = (
    "relevance_label",
    "fit_tier",
    "fit_score",
    "priority_score",
    "tracks",
    "explicit_skills",
    "inferred_skills",
    "skill_gaps",
    "positive_signals",
    "concerns",
    "evidence",
)

ENRICHMENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "relevance_label": {
            "type": "string",
            "enum": ["target", "adjacent", "weak", "exclude"],
            "description": "Overall relevance of the listing to Sindri's target path.",
        },
        "fit_tier": {
            "type": "string",
            "enum": ["strong", "reasonable", "stretch", "weak", "exclude"],
            "description": "How defensible the candidate fit is today.",
        },
        "fit_score": {
            "type": "integer",
            "description": "0-100 current-fit score based on profile evidence.",
        },
        "priority_score": {
            "type": "integer",
            "description": "0-100 review priority based on fit, strategy, and urgency.",
        },
        "tracks": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "software_it",
                    "engineering",
                    "biomedical_healthtech",
                    "data_automation",
                    "transferable",
                ],
            },
        },
        "explicit_skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Skills directly stated in the listing text.",
        },
        "inferred_skills": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "skill": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "evidence": {
                        "type": "string",
                        "description": "Listing text or task that supports the inference.",
                    },
                },
                "required": ["skill", "confidence", "evidence"],
            },
        },
        "skill_gaps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Important requirements or skills Sindri may need to strengthen.",
        },
        "positive_signals": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Evidence that makes this role worth reviewing.",
        },
        "concerns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Evidence that makes the role less relevant or risky.",
        },
        "evidence": {
            "type": "string",
            "description": "Short human-readable explanation grounded in the listing text.",
        },
    },
    "required": list(REQUIRED_RESPONSE_FIELDS),
}

BULK_ENRICHMENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "candidate_id": {
                        "type": "integer",
                        "description": "The unchanged candidate ID from the input.",
                    },
                    **ENRICHMENT_RESPONSE_SCHEMA["properties"],
                },
                "required": ["candidate_id", *REQUIRED_RESPONSE_FIELDS],
            },
        },
    },
    "required": ["results"],
}

OPENAI_TEXT_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "career_radar_candidate_ai_enrichment",
    "strict": True,
    "schema": ENRICHMENT_RESPONSE_SCHEMA,
}

ENRICHMENT_INSTRUCTIONS = """
You classify collected Icelandic job-listing candidates for the Career Radar
project.

Sindri's profile:
- B.Sc. Biomedical Engineering.
- B.Sc. Computer Science.
- Best strategic positioning: data + engineering + automation + technical systems.
- Also relevant: software, IT/support, Microsoft 365, SharePoint/WorkPoint,
  PowerShell automation, troubleshooting, implementation, technical user support,
  biomedical/health-tech, quality, validation, verification, and regulated work.
- Current learning path: SQL, Python, Power BI, Azure, C#/.NET, Docker/CI/CD.

Classification rules:
- Use target when the job clearly belongs to the target career direction.
- Use adjacent when the job is plausibly useful but not a direct hit.
- Use weak when there are only minor transferable signals.
- Use exclude when the role is unrelated, clearly too senior, or depends on a
  mandatory credential/license that is not in the profile.
- Separate explicit skills from inferred skills. Explicit means the skill is
  directly stated. Inferred means the tasks make the skill likely, but it was
  not stated directly.
- Do not invent facts about the job or about Sindri. If evidence is weak, say so
  in concerns or use a lower confidence label.
- Fit score measures current defensible match. Priority score measures whether a
  human should review this candidate soon.
""".strip()


@dataclass(frozen=True, slots=True)
class CandidateForEnrichment:
    """A staged job candidate plus the rule signals the AI should see."""

    id: int
    source: str
    title: str
    company: str
    url: str
    location: str | None
    posted_date_raw: str | None
    deadline_raw: str | None
    deadline: str | None
    listing_status: str
    category_guess: str | None
    description: str | None
    requirements: str | None
    skills_mentioned: str | None
    notes: str | None
    rule_relevance_score: int
    rule_relevance_label: str
    matched_tracks: list[str]
    matched_keywords: list[str]
    hard_negative_flags: list[str]
    needs_ai_review: bool
    review_status: str

    def snapshot(self) -> dict[str, Any]:
        """Return the exact candidate data included in the AI prompt."""

        return {
            "candidate_id": self.id,
            "source": self.source,
            "title": self.title,
            "company": self.company,
            "url": self.url,
            "location": self.location,
            "posted_date_raw": self.posted_date_raw,
            "deadline_raw": self.deadline_raw,
            "deadline": self.deadline,
            "listing_status": self.listing_status,
            "category_guess": self.category_guess,
            "description": self.description,
            "requirements": self.requirements,
            "skills_mentioned": self.skills_mentioned,
            "notes": self.notes,
            "rule_relevance_score": self.rule_relevance_score,
            "rule_relevance_label": self.rule_relevance_label,
            "matched_tracks": self.matched_tracks,
            "matched_keywords": self.matched_keywords,
            "hard_negative_flags": self.hard_negative_flags,
            "needs_ai_review": self.needs_ai_review,
            "review_status": self.review_status,
        }


def get_openai_model() -> str:
    """Read the AI model name from .env, with a docs-friendly default."""

    load_dotenv()
    return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)


def get_openai_client():
    """Create the OpenAI SDK client only when an actual API call is needed."""

    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to .env before running real AI "
            "enrichment, or use --dry-run to inspect the prompt without spending "
            "API credits."
        )

    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError(
            "The OpenAI Python SDK is not installed. Run pip install -r "
            "requirements.txt inside the project virtual environment."
        ) from error

    return OpenAI()


def load_candidates_for_enrichment(
    label: CandidateLabelFilter,
    limit: int,
    candidate_id: int | None = None,
    force: bool = False,
) -> list[CandidateForEnrichment]:
    """Load staged candidates that should be sent to the AI enrichment step."""

    if limit < 1:
        raise RuntimeError("--limit must be at least 1.")

    params: list[object] = []
    where_parts = []

    if candidate_id is None:
        where_parts.append(AI_LABEL_FILTER_SQL[label])
    else:
        where_parts.append("c.id = %s")
        params.append(candidate_id)

    if not force:
        where_parts.append(
            """
            not exists (
                select 1
                from candidate_ai_enrichments e
                where e.candidate_id = c.id
            )
            """
        )

    params.append(limit)
    where_sql = " and ".join(f"({part})" for part in where_parts)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                    c.id,
                    c.source,
                    c.title,
                    c.company,
                    c.url,
                    c.location,
                    c.posted_date_raw,
                    c.deadline_raw,
                    c.deadline,
                    c.listing_status,
                    c.category_guess,
                    c.description,
                    c.requirements,
                    c.skills_mentioned,
                    c.notes,
                    c.rule_relevance_score,
                    c.rule_relevance_label,
                    c.matched_tracks,
                    c.matched_keywords,
                    c.hard_negative_flags,
                    c.needs_ai_review,
                    c.review_status
                from collected_job_candidates_ranked_v1 c
                where {where_sql}
                order by
                    case c.rule_relevance_label
                        when 'strong_candidate' then 1
                        when 'possible_candidate' then 2
                        when 'hard_negative' then 3
                        else 4
                    end,
                    c.rule_relevance_score desc,
                    c.deadline nulls last,
                    c.last_seen_at desc
                limit %s;
                """,
                params,
            )
            rows = cur.fetchall()

    candidates = []
    for row in rows:
        (
            row_id,
            source,
            title,
            company,
            url,
            location,
            posted_date_raw,
            deadline_raw,
            deadline,
            listing_status,
            category_guess,
            description,
            requirements,
            skills_mentioned,
            notes,
            rule_relevance_score,
            rule_relevance_label,
            matched_tracks,
            matched_keywords,
            hard_negative_flags,
            needs_ai_review,
            review_status,
        ) = row
        candidates.append(
            CandidateForEnrichment(
                id=row_id,
                source=source,
                title=title,
                company=company,
                url=url,
                location=location,
                posted_date_raw=posted_date_raw,
                deadline_raw=deadline_raw,
                deadline=deadline.isoformat() if deadline else None,
                listing_status=listing_status,
                category_guess=category_guess,
                description=description,
                requirements=requirements,
                skills_mentioned=skills_mentioned,
                notes=notes,
                rule_relevance_score=rule_relevance_score,
                rule_relevance_label=rule_relevance_label,
                matched_tracks=list(matched_tracks or []),
                matched_keywords=list(matched_keywords or []),
                hard_negative_flags=list(hard_negative_flags or []),
                needs_ai_review=needs_ai_review,
                review_status=review_status,
            )
        )

    return candidates


def build_candidate_input(candidate: CandidateForEnrichment) -> str:
    """Build the user-side prompt payload for one candidate."""

    snapshot_json = json.dumps(
        candidate.snapshot(),
        ensure_ascii=False,
        indent=2,
    )
    return f"Analyze this collected job candidate:\n\n{snapshot_json}"


def build_manual_ai_prompt(candidate: CandidateForEnrichment) -> str:
    """Build a full prompt for manual ChatGPT-based enrichment."""

    schema_json = json.dumps(
        ENRICHMENT_RESPONSE_SCHEMA,
        ensure_ascii=False,
        indent=2,
    )
    return f"""
You are helping me enrich one staged job candidate for my Career Radar project.

Follow these instructions:

{ENRICHMENT_INSTRUCTIONS}

Return only valid JSON. Do not wrap it in Markdown. Do not include commentary
before or after the JSON.

The JSON must match this schema:

{schema_json}

{build_candidate_input(candidate)}
""".strip()


def build_bulk_manual_ai_prompt(candidates: list[CandidateForEnrichment]) -> str:
    """Build a manual ChatGPT prompt for several staged candidates at once."""

    schema_json = json.dumps(
        BULK_ENRICHMENT_RESPONSE_SCHEMA,
        ensure_ascii=False,
        indent=2,
    )
    candidates_json = json.dumps(
        [candidate.snapshot() for candidate in candidates],
        ensure_ascii=False,
        indent=2,
    )
    return f"""
You are helping me enrich staged job candidates for my Career Radar project.

Follow these instructions for every candidate:

{ENRICHMENT_INSTRUCTIONS}

Return only valid JSON. Do not wrap it in Markdown. Do not include commentary
before or after the JSON.

The JSON must match this bulk schema:

{schema_json}

Important:
- Return exactly one result for each input candidate.
- Keep each candidate_id unchanged.
- Do not merge candidates together.
- If a candidate is irrelevant, still return a result with relevance_label
  "exclude" or "weak" and explain why in concerns/evidence.

Analyze these collected job candidates:

{candidates_json}
""".strip()


def print_manual_ai_prompt(
    candidate_id: int,
    output_file: str | None = None,
) -> None:
    """Print or save a prompt that can be pasted into ChatGPT manually."""

    candidates = load_candidates_for_enrichment(
        label="all",
        limit=1,
        candidate_id=candidate_id,
        force=True,
    )
    if not candidates:
        print(f"Candidate not found: {candidate_id}")
        return

    prompt = build_manual_ai_prompt(candidates[0])
    if output_file is None:
        print(prompt)
        return

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding="utf-8")
    print(f"Wrote manual AI prompt for candidate {candidate_id} to {output_path}")


def print_bulk_manual_ai_prompt(
    label: CandidateLabelFilter,
    limit: int,
    output_file: str | None = None,
    force: bool = False,
) -> None:
    """Print or save a manual ChatGPT prompt for several candidates."""

    candidates = load_candidates_for_enrichment(
        label=label,
        limit=limit,
        force=force,
    )
    if not candidates:
        print("No candidates selected for the bulk manual AI prompt.")
        if not force:
            print(
                "Tip: use --force to include candidates that already have "
                "AI results."
            )
        return

    prompt = build_bulk_manual_ai_prompt(candidates)
    if output_file is None:
        print(prompt)
        return

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding="utf-8")
    print(
        f"Wrote manual AI prompt for {len(candidates)} candidate(s) "
        f"to {output_path}"
    )


def extract_json_object(text: str) -> str:
    """Extract a JSON object from pasted text, including Markdown code blocks."""

    cleaned_text = text.strip()
    if cleaned_text.startswith("```"):
        lines = cleaned_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned_text = "\n".join(lines).strip()

    start_index = cleaned_text.find("{")
    end_index = cleaned_text.rfind("}")
    if start_index == -1 or end_index == -1 or end_index < start_index:
        raise RuntimeError("Could not find a JSON object in the AI result text.")

    return cleaned_text[start_index : end_index + 1]


def read_manual_ai_result(result_file: str) -> dict[str, Any]:
    """Read and validate an AI enrichment JSON result from a file or stdin."""

    if result_file == "-":
        raw_text = sys.stdin.read()
    else:
        raw_text = Path(result_file).read_text(encoding="utf-8")

    payload = json.loads(extract_json_object(raw_text))
    if not isinstance(payload, dict):
        raise RuntimeError("AI enrichment result must be a JSON object.")

    return normalize_ai_payload(payload)


def read_bulk_manual_ai_results(result_file: str) -> list[tuple[int, dict[str, Any]]]:
    """Read and validate bulk AI enrichment JSON results from a file or stdin."""

    if result_file == "-":
        raw_text = sys.stdin.read()
    else:
        raw_text = Path(result_file).read_text(encoding="utf-8")

    payload = json.loads(extract_json_object(raw_text))
    if not isinstance(payload, dict):
        raise RuntimeError("Bulk AI enrichment result must be a JSON object.")

    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise RuntimeError("Bulk AI enrichment result must contain a results list.")

    seen_candidate_ids = set()
    results = []
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            raise RuntimeError("Each bulk AI result must be a JSON object.")

        candidate_id = raw_result.get("candidate_id")
        if type(candidate_id) is not int or candidate_id < 1:
            raise RuntimeError("Each bulk AI result must have a positive integer candidate_id.")
        if candidate_id in seen_candidate_ids:
            raise RuntimeError(f"Duplicate candidate_id in AI results: {candidate_id}")

        seen_candidate_ids.add(candidate_id)
        result_payload = {
            field: raw_result[field]
            for field in REQUIRED_RESPONSE_FIELDS
            if field in raw_result
        }
        results.append((candidate_id, normalize_ai_payload(result_payload)))

    return results


def normalize_ai_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the AI payload before saving it to PostgreSQL."""

    if not isinstance(payload, dict):
        raise RuntimeError("AI response must be a JSON object.")

    missing_fields = [
        field for field in REQUIRED_RESPONSE_FIELDS if field not in payload
    ]
    if missing_fields:
        raise RuntimeError(
            "AI response is missing required fields: "
            + ", ".join(missing_fields)
        )

    for field in ARRAY_RESPONSE_FIELDS:
        if not isinstance(payload[field], list):
            raise RuntimeError(f"AI response field must be a list: {field}")
        if any(not isinstance(value, str) for value in payload[field]):
            raise RuntimeError(f"AI response field must contain strings: {field}")

    for field in ("relevance_label", "fit_tier"):
        allowed = ENRICHMENT_RESPONSE_SCHEMA["properties"][field]["enum"]
        if payload[field] not in allowed:
            raise RuntimeError(f"Unsupported AI response {field}: {payload[field]}")
    allowed_tracks = ENRICHMENT_RESPONSE_SCHEMA["properties"]["tracks"]["items"]["enum"]
    if any(track not in allowed_tracks for track in payload["tracks"]):
        raise RuntimeError("AI response contains an unsupported track.")
    if not isinstance(payload["evidence"], str):
        raise RuntimeError("AI response evidence must be a string.")

    if not isinstance(payload["inferred_skills"], list):
        raise RuntimeError("AI response field must be a list: inferred_skills")
    for inferred in payload["inferred_skills"]:
        if not isinstance(inferred, dict) or any(
            not isinstance(inferred.get(field), str)
            for field in ("skill", "confidence", "evidence")
        ):
            raise RuntimeError("Each inferred skill needs skill, confidence, and evidence strings.")
        if inferred["confidence"] not in ("low", "medium", "high"):
            raise RuntimeError("Inferred skill confidence must be low, medium, or high.")

    for field in ("fit_score", "priority_score"):
        if type(payload[field]) is not int:
            raise RuntimeError(f"AI response field must be an integer: {field}")
        if payload[field] < 0 or payload[field] > 100:
            raise RuntimeError(f"AI response score must be 0-100: {field}")

    return payload


def request_ai_enrichment(
    client: object,
    candidate: CandidateForEnrichment,
    model: str,
) -> tuple[dict[str, Any], str | None]:
    """Ask OpenAI for structured enrichment of one staged candidate."""

    response = client.responses.create(
        model=model,
        instructions=ENRICHMENT_INSTRUCTIONS,
        input=build_candidate_input(candidate),
        text={"format": OPENAI_TEXT_FORMAT},
    )
    payload = json.loads(response.output_text)
    response_id = getattr(response, "id", None)
    return normalize_ai_payload(payload), response_id


def save_ai_enrichment(
    candidate: CandidateForEnrichment,
    model: str,
    payload: dict[str, Any],
    response_id: str | None,
    provider: str = "openai",
    prompt_version: str = PROMPT_VERSION,
) -> int:
    """Persist one AI enrichment result and return its database ID."""

    payload = normalize_ai_payload(payload)
    raw_response = {
        "response_id": response_id,
        "payload": payload,
    }

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into candidate_ai_enrichments (
                    candidate_id,
                    provider,
                    model,
                    prompt_version,
                    ai_relevance_label,
                    ai_fit_tier,
                    ai_fit_score,
                    ai_priority_score,
                    tracks,
                    explicit_skills,
                    inferred_skills,
                    skill_gaps,
                    positive_signals,
                    concerns,
                    evidence,
                    candidate_snapshot,
                    raw_response
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                returning id;
                """,
                (
                    candidate.id,
                    provider,
                    model,
                    prompt_version,
                    payload["relevance_label"],
                    payload["fit_tier"],
                    payload["fit_score"],
                    payload["priority_score"],
                    payload["tracks"],
                    payload["explicit_skills"],
                    Jsonb(payload["inferred_skills"]),
                    payload["skill_gaps"],
                    payload["positive_signals"],
                    payload["concerns"],
                    payload["evidence"],
                    Jsonb(candidate.snapshot()),
                    Jsonb(raw_response),
                ),
            )
            row = cur.fetchone()

    return row[0]


def has_matching_latest_ai_enrichment(
    candidate_id: int,
    provider: str,
    model: str,
    prompt_version: str,
    payload: dict[str, Any],
) -> bool:
    """Return true when the latest comparable AI result is unchanged."""

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    ai_relevance_label,
                    ai_fit_tier,
                    ai_fit_score,
                    ai_priority_score,
                    tracks,
                    explicit_skills,
                    inferred_skills,
                    skill_gaps,
                    positive_signals,
                    concerns,
                    evidence
                from candidate_ai_enrichments
                where candidate_id = %s
                  and provider = %s
                  and model = %s
                  and prompt_version = %s
                order by created_at desc, id desc
                limit 1;
                """,
                (candidate_id, provider, model, prompt_version),
            )
            row = cur.fetchone()

    if row is None:
        return False

    return (
        row[0] == payload["relevance_label"]
        and row[1] == payload["fit_tier"]
        and row[2] == payload["fit_score"]
        and row[3] == payload["priority_score"]
        and list(row[4] or []) == payload["tracks"]
        and list(row[5] or []) == payload["explicit_skills"]
        and row[6] == payload["inferred_skills"]
        and list(row[7] or []) == payload["skill_gaps"]
        and list(row[8] or []) == payload["positive_signals"]
        and list(row[9] or []) == payload["concerns"]
        and row[10] == payload["evidence"]
    )


def import_manual_ai_enrichment(
    candidate_id: int,
    result_file: str,
    model_label: str,
) -> None:
    """Save a ChatGPT-produced enrichment JSON file for one candidate."""

    candidates = load_candidates_for_enrichment(
        label="all",
        limit=1,
        candidate_id=candidate_id,
        force=True,
    )
    if not candidates:
        print(f"Candidate not found: {candidate_id}")
        return

    candidate = candidates[0]
    payload = read_manual_ai_result(result_file)
    enrichment_id = save_ai_enrichment(
        candidate=candidate,
        model=model_label,
        payload=payload,
        response_id=None,
        provider="chatgpt_manual",
    )

    print(
        f"Saved manual AI enrichment for candidate {candidate_id}: "
        f"{payload['relevance_label']} / {payload['fit_tier']} "
        f"fit={payload['fit_score']} priority={payload['priority_score']} "
        f"(AI row {enrichment_id})"
    )


@transaction()
def import_bulk_manual_ai_enrichments(
    result_file: str,
    model_label: str,
) -> None:
    """Save several ChatGPT-produced enrichment results from one JSON file."""

    results = read_bulk_manual_ai_results(result_file)
    if not results:
        print("No AI enrichment results found in the file.")
        return

    candidates_by_id = {}
    missing_candidate_ids = []
    for candidate_id, _payload in results:
        candidates = load_candidates_for_enrichment(
            label="all",
            limit=1,
            candidate_id=candidate_id,
            force=True,
        )
        if candidates:
            candidates_by_id[candidate_id] = candidates[0]
        else:
            missing_candidate_ids.append(candidate_id)

    if missing_candidate_ids:
        raise RuntimeError(
            "Candidate IDs not found: "
            + ", ".join(str(candidate_id) for candidate_id in missing_candidate_ids)
        )

    saved_count = 0
    for candidate_id, payload in results:
        candidate = candidates_by_id[candidate_id]
        enrichment_id = save_ai_enrichment(
            candidate=candidate,
            model=model_label,
            payload=payload,
            response_id=None,
            provider="chatgpt_manual",
        )
        saved_count += 1
        print(
            f"[{candidate_id}] {candidate.company} - {candidate.title}: "
            f"{payload['relevance_label']} / {payload['fit_tier']} "
            f"fit={payload['fit_score']} priority={payload['priority_score']} "
            f"(AI row {enrichment_id})"
        )

    print(f"Saved {saved_count} manual AI enrichment result(s).")


def print_enrichment_dry_run(
    candidates: list[CandidateForEnrichment],
    model: str,
) -> None:
    """Print what would be sent to AI without calling the API."""

    print("AI enrichment dry run")
    print(f"Model: {model}")
    print(f"Prompt version: {PROMPT_VERSION}")
    print(f"Candidates selected: {len(candidates)}")
    print()
    print("Structured output fields:")
    print("    " + ", ".join(REQUIRED_RESPONSE_FIELDS))
    print()

    for candidate in candidates:
        prompt = build_candidate_input(candidate)
        preview = prompt[:2500]
        omitted_chars = len(prompt) - len(preview)
        print(f"[{candidate.id}] {candidate.company} - {candidate.title}")
        print(
            f"    rule: {candidate.rule_relevance_score} "
            f"({candidate.rule_relevance_label})"
        )
        print(f"    status: {candidate.review_status}")
        print(f"    url: {candidate.url}")
        print("    prompt preview:")
        for line in preview.splitlines():
            print(f"        {line}")
        if omitted_chars > 0:
            print(f"        ...{omitted_chars} more characters omitted")
        print()


def enrich_candidates(
    label: CandidateLabelFilter,
    limit: int,
    candidate_id: int | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Run AI enrichment for staged candidates or show a safe dry run."""

    candidates = load_candidates_for_enrichment(
        label=label,
        limit=limit,
        candidate_id=candidate_id,
        force=force,
    )
    if not candidates:
        print("No candidates selected for AI enrichment.")
        if not force:
            print(
                "Tip: use --force to re-enrich candidates that already have "
                "AI results."
            )
        return

    model = get_openai_model()
    if dry_run:
        print_enrichment_dry_run(candidates, model)
        return

    client = get_openai_client()
    print(f"Running AI enrichment for {len(candidates)} candidate(s).")
    print(f"Model: {model}")

    for candidate in candidates:
        payload, response_id = request_ai_enrichment(client, candidate, model)
        enrichment_id = save_ai_enrichment(
            candidate=candidate,
            model=model,
            payload=payload,
            response_id=response_id,
        )
        print(
            f"[{candidate.id}] {candidate.company} - {candidate.title}: "
            f"{payload['relevance_label']} / {payload['fit_tier']} "
            f"fit={payload['fit_score']} priority={payload['priority_score']} "
            f"(AI row {enrichment_id})"
        )


def print_ai_enrichments(limit: int) -> None:
    """Print the latest AI enrichment results joined to candidate data."""

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    id,
                    company,
                    title,
                    review_status,
                    rule_relevance_label,
                    ai_relevance_label,
                    ai_fit_tier,
                    ai_fit_score,
                    ai_priority_score,
                    recommended_action,
                    ai_tracks,
                    ai_explicit_skills,
                    ai_skill_gaps,
                    ai_evidence,
                    url
                from collected_job_candidates_ai_v1
                where ai_enrichment_id is not null
                order by
                    case recommended_action
                        when 'review_for_promotion' then 1
                        when 'manual_review' then 2
                        when 'needs_ai_review' then 3
                        when 'reject' then 4
                        when 'not_actionable' then 5
                        else 6
                    end,
                    ai_priority_score desc,
                    ai_fit_score desc
                limit %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

    if not rows:
        print("No AI enrichment results found yet.")
        return

    print("Latest candidate AI enrichments")
    print("=" * 100)

    for row in rows:
        (
            candidate_id,
            company,
            title,
            review_status,
            rule_label,
            ai_label,
            ai_fit_tier,
            ai_fit_score,
            ai_priority_score,
            recommended_action,
            ai_tracks,
            ai_explicit_skills,
            ai_skill_gaps,
            ai_evidence,
            url,
        ) = row

        print(f"[{candidate_id}] {company} - {title}")
        print(
            f"    rule: {rule_label}; ai: {ai_label} / {ai_fit_tier}; "
            f"fit={ai_fit_score}; priority={ai_priority_score}"
        )
        print(f"    review: {review_status}; recommended: {recommended_action}")
        print(f"    tracks: {', '.join(ai_tracks or []) or '-'}")
        print(f"    explicit skills: {', '.join((ai_explicit_skills or [])[:8]) or '-'}")
        print(f"    skill gaps: {', '.join((ai_skill_gaps or [])[:8]) or '-'}")
        print(f"    evidence: {ai_evidence}")
        print(f"    url: {url}")
        print()


def print_ai_review_queue(action: str, limit: int) -> None:
    """Print candidate review state across enriched and unenriched candidates."""

    if limit < 1:
        raise RuntimeError("--limit must be at least 1.")

    where_sql = AI_REVIEW_ACTION_FILTER_SQL[action]

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                    id,
                    company,
                    title,
                    review_status,
                    rule_relevance_label,
                    rule_relevance_score,
                    recommended_action,
                    ai_relevance_label,
                    ai_fit_tier,
                    ai_fit_score,
                    ai_priority_score,
                    ai_explicit_skills,
                    ai_skill_gaps,
                    ai_evidence,
                    url
                from collected_job_candidates_ai_v1
                where {where_sql}
                order by
                    case recommended_action
                        when 'review_for_promotion' then 1
                        when 'manual_review' then 2
                        when 'needs_ai_review' then 3
                        when 'reject' then 4
                        when 'not_actionable' then 5
                        else 6
                    end,
                    ai_priority_score desc nulls last,
                    ai_fit_score desc nulls last,
                    rule_relevance_score desc,
                    id
                limit %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

    if not rows:
        print(f"No candidates found for AI review action: {action}")
        return

    print(f"AI review queue: {action}")
    print("=" * 100)

    for row in rows:
        (
            candidate_id,
            company,
            title,
            review_status,
            rule_label,
            rule_score,
            recommended_action,
            ai_label,
            ai_fit_tier,
            ai_fit_score,
            ai_priority_score,
            ai_explicit_skills,
            ai_skill_gaps,
            ai_evidence,
            url,
        ) = row

        ai_summary = "not enriched yet"
        if ai_label is not None:
            ai_summary = (
                f"{ai_label} / {ai_fit_tier}; "
                f"fit={ai_fit_score}; priority={ai_priority_score}"
            )

        print(f"[{candidate_id}] {company} - {title}")
        print(f"    recommended: {recommended_action}; review: {review_status}")
        print(f"    rule: {rule_label}; score={rule_score}")
        print(f"    ai: {ai_summary}")
        if ai_explicit_skills:
            print(f"    explicit skills: {', '.join(ai_explicit_skills[:8])}")
        if ai_skill_gaps:
            print(f"    skill gaps: {', '.join(ai_skill_gaps[:8])}")
        if ai_evidence:
            print(f"    evidence: {ai_evidence}")
        print(f"    url: {url}")
        print()


@transaction()
def apply_ai_review_status(
    action: str,
    status: str,
    limit: int,
    apply: bool = False,
) -> None:
    """Preview or apply a bulk review-status update from AI recommendations."""

    if action not in AI_STATUS_ACTION_FILTERS:
        raise RuntimeError(
            "Bulk status updates only support these actions: "
            + ", ".join(AI_STATUS_ACTION_FILTERS)
        )
    if status not in AI_BULK_REVIEW_STATUSES:
        raise RuntimeError(
            "Bulk status updates only support these statuses: "
            + ", ".join(AI_BULK_REVIEW_STATUSES)
        )
    if limit < 1:
        raise RuntimeError("--limit must be at least 1.")

    where_sql = AI_REVIEW_ACTION_FILTER_SQL[action]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                    id,
                    company,
                    title,
                    review_status,
                    recommended_action,
                    ai_relevance_label,
                    ai_fit_score,
                    ai_priority_score
                from collected_job_candidates_ai_v1
                where {where_sql}
                  and review_status in ('unreviewed', 'approved')
                order by
                    ai_priority_score desc nulls last,
                    ai_fit_score desc nulls last,
                    rule_relevance_score desc,
                    id
                limit %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

            if apply and rows:
                candidate_ids = [row[0] for row in rows]
                cur.execute(
                    """
                    update collected_job_candidates
                    set
                        review_status = %s,
                        updated_at = now()
                    where id = any(%s);
                    """,
                    (status, candidate_ids),
                )

    if not rows:
        print(f"No candidates found for action {action}.")
        return

    mode = "Applied" if apply else "Dry run"
    print(f"{mode}: set {len(rows)} candidate(s) from action {action} to {status}")
    print("=" * 100)

    for row in rows:
        (
            candidate_id,
            company,
            title,
            review_status,
            recommended_action,
            ai_label,
            ai_fit_score,
            ai_priority_score,
        ) = row
        ai_summary = "not enriched"
        if ai_label is not None:
            ai_summary = (
                f"{ai_label}; fit={ai_fit_score}; priority={ai_priority_score}"
            )
        print(f"[{candidate_id}] {company} - {title}")
        print(
            f"    {review_status} -> {status}; "
            f"recommended={recommended_action}; ai={ai_summary}"
        )

    if not apply:
        print()
        print("Add --apply to save this status change.")
