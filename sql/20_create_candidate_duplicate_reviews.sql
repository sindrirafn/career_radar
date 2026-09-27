-- Decisions about opportunity-level duplicates in candidate staging.
--
-- URL identity remains the automatic, zero-risk rule. This table covers a
-- different problem: one real vacancy can have several legitimate URLs (for
-- example, the employer's careers site and an Alfreð post). We retain every
-- source row for traceability, but link confirmed duplicates to the one row
-- that should remain visible in the review workflow.
create table if not exists candidate_duplicate_reviews (
    first_candidate_id bigint not null
        references collected_job_candidates(id) on delete cascade,
    second_candidate_id bigint not null
        references collected_job_candidates(id) on delete cascade,
    decision text not null check (
        decision in ('auto_merged', 'confirmed_duplicate', 'not_duplicate')
    ),
    canonical_candidate_id bigint
        references collected_job_candidates(id) on delete set null,
    evidence text not null,
    decided_at timestamptz not null default now(),

    primary key (first_candidate_id, second_candidate_id),
    check (first_candidate_id < second_candidate_id),
    check (
        canonical_candidate_id is null
        or canonical_candidate_id in (first_candidate_id, second_candidate_id)
    ),
    check (
        (decision = 'not_duplicate' and canonical_candidate_id is null)
        or (decision in ('auto_merged', 'confirmed_duplicate')
            and canonical_candidate_id is not null)
    )
);

create index if not exists idx_candidate_duplicate_reviews_decision
    on candidate_duplicate_reviews (decision, canonical_candidate_id);
