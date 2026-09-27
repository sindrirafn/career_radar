-- Keep raw source text intact while making invalid calendar dates queryable.
-- A single-quoted body is compatible with the project's SQL-file splitter.
create or replace function career_radar_iso_date(value text)
returns date
language plpgsql
immutable
strict
as '
begin
    if value !~ ''^[0-9]{4}-[0-9]{2}-[0-9]{2}$'' then
        return null;
    end if;
    return value::date;
exception
    when datetime_field_overflow or invalid_datetime_format then
        return null;
end;
';
