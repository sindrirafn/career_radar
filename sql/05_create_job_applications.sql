-- Track your workflow for jobs you may apply to or have applied to.
--
-- This table references raw_job_listings, not job_listings_v1, because foreign
-- keys should point at stable stored tables rather than views.
create table if not exists job_applications (
    id bigserial primary key,
    job_listing_id bigint not null references raw_job_listings(id),

    status text not null check (
        status in (
            'interested',
            'preparing',
            'applied',
            'interviewing',
            'rejected',
            'tracking_only',
            'not_applying',
            'withdrawn',
            'offer'
        )
    ),

    applied_on date,
    application_url text,
    cv_version text,
    cover_letter_version text,
    follow_up_date date,
    next_action text,
    notes text,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Existing local tables may have been created manually while learning. These
-- ALTER statements make the table match the project version without requiring
-- a destructive drop/recreate.
alter table job_applications
    add column if not exists cover_letter_version text;

alter table job_applications
    add column if not exists next_action text;

alter table job_applications
    add column if not exists created_at timestamptz not null default now();

alter table job_applications
    add column if not exists updated_at timestamptz not null default now();

-- Re-assert the intended constraints for local tables that may have been
-- created manually before this file existed.
alter table job_applications
    drop constraint if exists job_applications_job_listing_id_fkey;

alter table job_applications
    add constraint job_applications_job_listing_id_fkey
    foreign key (job_listing_id) references raw_job_listings(id);

alter table job_applications
    drop constraint if exists job_applications_status_check;

alter table job_applications
    add constraint job_applications_status_check
    check (
        status in (
            'interested',
            'preparing',
            'applied',
            'interviewing',
            'rejected',
            'tracking_only',
            'not_applying',
            'withdrawn',
            'offer'
        )
    );

-- One application workflow row per job listing for now. If you later want to
-- track multiple application attempts to the same listing, this can become a
-- separate history table.
create unique index if not exists idx_job_applications_job_listing_id
    on job_applications (job_listing_id);
