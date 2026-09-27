-- AI enrichment results for collected job candidates.
--
-- This is intentionally a separate history table instead of extra columns on
-- collected_job_candidates. Candidate rows can change when a listing is seen
-- again, and prompts/models will also improve. Keeping each enrichment run lets
-- us compare old and new judgements instead of overwriting useful history.
drop view if exists collected_job_candidates_ai_v1;
drop view if exists candidate_ai_enrichments_latest_v1;

create table if not exists candidate_ai_enrichments (
    id bigserial primary key,
    candidate_id bigint not null references collected_job_candidates(id) on delete cascade,
    provider text not null default 'openai',
    model text not null,
    prompt_version text not null,

    -- These fields are deliberately small controlled vocabularies. The AI can
    -- reason in natural language, but the database needs stable labels that can
    -- be filtered, grouped, and charted.
    ai_relevance_label text not null check (
        ai_relevance_label in ('target', 'adjacent', 'weak', 'exclude')
    ),
    ai_fit_tier text not null check (
        ai_fit_tier in ('strong', 'reasonable', 'stretch', 'weak', 'exclude')
    ),
    ai_fit_score integer not null check (ai_fit_score between 0 and 100),
    ai_priority_score integer not null check (ai_priority_score between 0 and 100),

    tracks text[] not null default '{}',
    explicit_skills text[] not null default '{}',
    inferred_skills jsonb not null default '[]'::jsonb check (
        jsonb_typeof(inferred_skills) = 'array'
    ),
    skill_gaps text[] not null default '{}',
    positive_signals text[] not null default '{}',
    concerns text[] not null default '{}',
    evidence text not null,

    -- The snapshot records what the model saw. This protects explainability if
    -- the candidate row is refreshed later with newer source text.
    candidate_snapshot jsonb not null default '{}'::jsonb,
    raw_response jsonb not null,
    created_at timestamptz not null default now()
);

alter table candidate_ai_enrichments
    drop constraint if exists candidate_ai_enrichments_ai_fit_tier_check;

alter table candidate_ai_enrichments
    add constraint candidate_ai_enrichments_ai_fit_tier_check
    check (ai_fit_tier in ('strong', 'reasonable', 'stretch', 'weak', 'exclude'));

create index if not exists idx_candidate_ai_enrichments_candidate_created
    on candidate_ai_enrichments (candidate_id, created_at desc, id desc);

create index if not exists idx_candidate_ai_enrichments_priority
    on candidate_ai_enrichments (
        ai_relevance_label,
        ai_priority_score desc,
        ai_fit_score desc
    );

create view candidate_ai_enrichments_latest_v1 as
select distinct on (candidate_id)
    e.*
from candidate_ai_enrichments e
order by
    candidate_id,
    created_at desc,
    id desc;

create view collected_job_candidates_ai_v1 as
select
    c.*,
    e.id as ai_enrichment_id,
    e.provider as ai_provider,
    e.model as ai_model,
    e.prompt_version as ai_prompt_version,
    e.ai_relevance_label,
    e.ai_fit_tier,
    e.ai_fit_score,
    e.ai_priority_score,
    e.tracks as ai_tracks,
    e.explicit_skills as ai_explicit_skills,
    e.inferred_skills as ai_inferred_skills,
    e.skill_gaps as ai_skill_gaps,
    e.positive_signals as ai_positive_signals,
    e.concerns as ai_concerns,
    e.evidence as ai_evidence,
    e.created_at as ai_enriched_at,
    case
        when c.review_status = 'promoted' then 'already_promoted'
        when c.review_status = 'rejected' then 'already_rejected'
        when e.id is null and c.needs_ai_review then 'needs_ai_review'
        when e.id is null
         and c.rule_relevance_label in ('likely_irrelevant', 'hard_negative')
            then 'not_actionable'
        when e.id is null then 'manual_review'
        when e.ai_relevance_label in ('target', 'adjacent')
         and e.ai_fit_score >= 65
         and e.ai_priority_score >= 60
            then 'review_for_promotion'
        when e.ai_relevance_label in ('weak', 'exclude')
          or e.ai_fit_tier in ('weak', 'exclude')
            then 'reject'
        else 'manual_review'
    end as recommended_action
from collected_job_candidates_ranked_v1 c
left join candidate_ai_enrichments_latest_v1 e
    on e.candidate_id = c.id;
