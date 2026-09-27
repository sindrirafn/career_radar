from __future__ import annotations

from typing import Literal

from career_radar.db import connect
from career_radar.normalization import normalize_skill_name


ProfileLevel = Literal[
    "learning",
    "working",
    "strong",
]
PROFILE_LEVELS = ("learning", "working", "strong")


def display_skill_name(skill_name: str) -> str:
    """Normalize whitespace while preserving the user's readable casing."""

    return " ".join(skill_name.strip().split())


def print_profile_skills(level: str, limit: int) -> None:
    """Print skills currently included in the user's profile."""

    level_filter_sql = ""
    params: list[object] = []
    if level != "all":
        level_filter_sql = "where ps.proficiency_level = %s"
        params.append(level)

    params.append(limit)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                    s.normalized_name,
                    ps.proficiency_level,
                    ps.include_in_gap_analysis,
                    ps.evidence
                from profile_skills ps
                join skills s on s.id = ps.skill_id
                {level_filter_sql}
                order by
                    case ps.proficiency_level
                        when 'strong' then 1
                        when 'working' then 2
                        else 3
                    end,
                    s.normalized_name
                limit %s;
                """,
                params,
            )
            rows = cur.fetchall()

    if not rows:
        print(f"No profile skills found for level: {level}")
        return

    print(f"Profile skills: {level}")
    print("=" * 100)
    for normalized_name, proficiency_level, include_in_gap_analysis, evidence in rows:
        included = "yes" if include_in_gap_analysis else "no"
        print(f"{normalized_name}")
        print(f"    level: {proficiency_level}")
        print(f"    included in gap analysis: {included}")
        print(f"    evidence: {evidence or '-'}")
        print()


def set_profile_skill(
    skill_name: str,
    level: ProfileLevel,
    evidence: str | None,
    include_in_gap_analysis: bool,
) -> None:
    """Create or update one profile skill."""

    name = display_skill_name(skill_name)
    normalized_name = normalize_skill_name(name)
    if not normalized_name:
        raise RuntimeError("Skill name cannot be empty.")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into skills (
                    name,
                    normalized_name
                ) values (
                    %s,
                    %s
                )
                on conflict (normalized_name) do update
                set name = skills.name
                returning id;
                """,
                (name, normalized_name),
            )
            skill_id = cur.fetchone()[0]

            cur.execute(
                """
                insert into profile_skills (
                    skill_id,
                    proficiency_level,
                    evidence,
                    include_in_gap_analysis
                ) values (
                    %s,
                    %s,
                    %s,
                    %s
                )
                on conflict (skill_id) do update
                set
                    proficiency_level = excluded.proficiency_level,
                    evidence = coalesce(excluded.evidence, profile_skills.evidence),
                    include_in_gap_analysis = excluded.include_in_gap_analysis,
                    updated_at = now();
                """,
                (
                    skill_id,
                    level,
                    evidence.strip() if evidence else None,
                    include_in_gap_analysis,
                ),
            )

    print(f"Profile skill saved: {normalized_name} ({level})")


def remove_profile_skill(skill_name: str) -> None:
    """Remove one skill from the profile without deleting job-listing history."""

    normalized_name = normalize_skill_name(skill_name)
    if not normalized_name:
        raise RuntimeError("Skill name cannot be empty.")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id
                from skills
                where normalized_name = %s;
                """,
                (normalized_name,),
            )
            row = cur.fetchone()
            if row is None:
                print(f"Skill is not in the catalog: {normalized_name}")
                return

            skill_id = row[0]

            cur.execute(
                """
                delete from profile_skills
                where skill_id = %s;
                """,
                (skill_id,),
            )
            removed_count = cur.rowcount

            cur.execute(
                """
                delete from skills s
                where s.id = %s
                  and not exists (
                    select 1
                    from job_listing_skills jls
                    where jls.skill_id = s.id
                  )
                  and not exists (
                    select 1
                    from profile_skills ps
                    where ps.skill_id = s.id
                  );
                """,
                (skill_id,),
            )

    if removed_count:
        print(f"Profile skill removed: {normalized_name}")
    else:
        print(f"Skill exists, but was not in your profile: {normalized_name}")
