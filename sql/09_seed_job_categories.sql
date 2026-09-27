-- Seed the controlled category vocabulary.
--
-- These categories are not mutually exclusive. Treat them as strategic tags
-- that describe the type of opportunity from different angles.
insert into job_categories (name, normalized_name, description)
values
    ('Engineering', 'engineering', 'Engineering and technical-engineering roles.'),
    ('Biomedical', 'biomedical', 'Biomedical, medical-device, health-tech, or life-science roles.'),
    ('Healthcare', 'healthcare', 'Healthcare, pharma, clinical, or health-sector roles that are not necessarily medical-device roles.'),
    ('Data', 'data', 'Data analysis, BI, analytics, reporting, and data workflows.'),
    ('Software', 'software', 'Software development, programming, application development, or developer roles.'),
    ('Automation', 'automation', 'Workflow automation, process automation, scripting, industrial automation, or controls.'),
    ('IT Support', 'it_support', 'User support, help desk, endpoint support, and frontline technical service.'),
    ('Infrastructure', 'infrastructure', 'Systems, hosting, networking, cloud, platform, and operational infrastructure.'),
    ('Project Management', 'project_management', 'Project coordination, project delivery, planning, and cross-functional execution.'),
    ('Manufacturing', 'manufacturing', 'Manufacturing engineering, production processes, and process improvement.'),
    ('Quality', 'quality', 'Quality, validation, QMS-adjacent, or regulated-process work.'),
    ('Telecommunications', 'telecommunications', 'Telecom, radio systems, connectivity, and communications infrastructure.'),
    ('Research and Development', 'r_and_d', 'Research, experimentation, prototyping, testing, and product development.'),
    ('Business Analysis', 'business_analysis', 'Business analysis, requirements, process analysis, and commercial/operational insight.')
on conflict (normalized_name) do update
set
    name = excluded.name,
    description = excluded.description;

