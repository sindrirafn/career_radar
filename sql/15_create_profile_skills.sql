-- Your current skill profile.
--
-- The job market side of the system tells us what employers ask for. This
-- table records what you can currently claim, practice, or discuss. Keeping it
-- in the database lets us compare real listings against your profile instead
-- of treating all requested skills equally.
create table if not exists profile_skills (
    skill_id bigint primary key references skills(id) on delete cascade,

    -- learning: actively building the skill, partial coverage in analysis
    -- working: can use the skill practically
    -- strong: comfortable discussing and using the skill with confidence
    proficiency_level text not null check (
        proficiency_level in ('learning', 'working', 'strong')
    ),

    evidence text,
    include_in_gap_analysis boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_profile_skills_proficiency_level
    on profile_skills (proficiency_level);

-- Small editable starting profile.
--
-- These are intentionally conservative. The seed gives the reports something
-- useful to compare against, but later manual edits should not be overwritten.
with seed_skills (name, normalized_name, proficiency_level, evidence) as (
    values
        ('Computer science', 'computer science', 'strong', 'B.Sc. Computer Science.'),
        ('Biomedical engineering', 'biomedical engineering', 'strong', 'B.Sc. Biomedical Engineering.'),
        ('Engineering', 'engineering', 'working', 'B.Sc. Biomedical Engineering and engineering-focused career path.'),
        ('Programming fundamentals', 'programming fundamentals', 'working', 'Computer science background.'),
        ('Programming', 'programming', 'working', 'Computer science background and current project work.'),
        ('Software engineering', 'software engineering', 'working', 'Computer science background and software project work.'),
        ('Automation', 'automation', 'learning', 'Career Radar automation path and workflow focus.'),
        ('SQL', 'sql', 'learning', 'Current Career Radar learning path and database work.'),
        ('PostgreSQL', 'postgresql', 'learning', 'Current Career Radar database.'),
        ('Python', 'python', 'learning', 'Current Career Radar import and reporting scripts.'),
        ('Data analysis', 'data analysis', 'learning', 'Current Career Radar analysis workflow.'),
        ('Documentation', 'documentation', 'learning', 'Career Radar docs and biomedical/quality career path.'),
        ('Git', 'git', 'learning', 'Career Radar project version control.'),
        ('Power BI', 'power bi', 'learning', 'Current planned reporting path.'),
        ('Medical devices', 'medical devices', 'learning', 'Biomedical/health-tech career positioning.')
)
insert into skills (
    name,
    normalized_name
)
select
    name,
    normalized_name
from seed_skills
on conflict (normalized_name) do nothing;

with seed_skills (name, normalized_name, proficiency_level, evidence) as (
    values
        ('Computer science', 'computer science', 'strong', 'B.Sc. Computer Science.'),
        ('Biomedical engineering', 'biomedical engineering', 'strong', 'B.Sc. Biomedical Engineering.'),
        ('Engineering', 'engineering', 'working', 'B.Sc. Biomedical Engineering and engineering-focused career path.'),
        ('Programming fundamentals', 'programming fundamentals', 'working', 'Computer science background.'),
        ('Programming', 'programming', 'working', 'Computer science background and current project work.'),
        ('Software engineering', 'software engineering', 'working', 'Computer science background and software project work.'),
        ('Automation', 'automation', 'learning', 'Career Radar automation path and workflow focus.'),
        ('SQL', 'sql', 'learning', 'Current Career Radar learning path and database work.'),
        ('PostgreSQL', 'postgresql', 'learning', 'Current Career Radar database.'),
        ('Python', 'python', 'learning', 'Current Career Radar import and reporting scripts.'),
        ('Data analysis', 'data analysis', 'learning', 'Current Career Radar analysis workflow.'),
        ('Documentation', 'documentation', 'learning', 'Career Radar docs and biomedical/quality career path.'),
        ('Git', 'git', 'learning', 'Career Radar project version control.'),
        ('Power BI', 'power bi', 'learning', 'Current planned reporting path.'),
        ('Medical devices', 'medical devices', 'learning', 'Biomedical/health-tech career positioning.')
)
insert into profile_skills (
    skill_id,
    proficiency_level,
    evidence
)
select
    s.id,
    seed_skills.proficiency_level,
    seed_skills.evidence
from seed_skills
join skills s on s.normalized_name = seed_skills.normalized_name
on conflict (skill_id) do nothing;
