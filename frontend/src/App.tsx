import { useEffect, useState, type FormEvent } from "react";
import { Workspace } from "./Workspace";

type Counts = {
  ready_candidates: number;
  secondary_review_candidates: number;
  interested_applications: number;
  in_progress_applications: number;
  past_applications: number;
  not_applying_applications: number;
};

type Job = {
  job_listing_id?: number;
  id?: number;
  title: string;
  company: string;
  location?: string | null;
  deadline?: string | null;
  days_until_deadline?: number | null;
  application_status?: string;
  next_action?: string | null;
  follow_up_date?: string | null;
  skill_coverage_score?: number | null;
  gap_skill_count?: number | null;
  gap_skills?: string[] | null;
  ai_fit_score?: number | null;
  ai_fit_tier?: string | null;
  ai_evidence?: string | null;
  url: string;
};

type TodayPayload = {
  generated_at: string;
  counts: Counts;
  decision_queue_count: number;
  up_next: Job[];
  new_matches: Job[];
};

type ApplicationsPayload = { interested: Job[]; active: Job[]; past: Job[] };
const applicationStatuses = ["interested", "preparing", "applied", "interviewing", "offer", "rejected", "withdrawn", "not_applying"];

const formatDate = (value?: string | null) => {
  if (!value) return "No deadline";
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("en", { day: "numeric", month: "short" }).format(date);
};

const urgency = (days?: number | null) => {
  if (days === null || days === undefined) return "Flexible timing";
  if (days < 0) return "Deadline passed";
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  return `${days} days left`;
};

function Mark() {
  return <span className="mark" aria-hidden="true"><i /><i /><i /></span>;
}

function LoadingToday() {
  return <main className="content" aria-busy="true" aria-label="Loading Today">
    <section className="hero-skeleton"><span /><span /><span /></section>
    <section className="loading-grid"><span /><span /><span /></section>
  </main>;
}

function ApplicationRow({ job }: { job: Job }) {
  return <article className="application-row" role="link" tabIndex={0} onClick={() => { window.location.hash = `#applications/${job.job_listing_id}`; }} onKeyDown={(event) => { if (event.key === "Enter") window.location.hash = `#applications/${job.job_listing_id}`; }}>
    <div className="date-tile">
      <span>{urgency(job.days_until_deadline)}</span>
      <strong>{formatDate(job.deadline)}</strong>
    </div>
    <div className="role-copy">
      <h3>{job.title}</h3>
      <p>{job.company}{job.location ? ` · ${job.location}` : ""}</p>
      <small>{job.next_action || "Decide your next step."}</small>
    </div>
    <span className={`status status-${job.application_status || "interested"}`}>
      {(job.application_status || "interested").replaceAll("_", " ")}
    </span>
    <a className="quiet-link" href={job.url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Open listing<span aria-hidden="true"> ↗</span></a>
  </article>;
}

function MatchDecision({ job }: { job: Job }) {
  const [saving, setSaving] = useState<"interested" | "not_interested" | null>(null); const [decision, setDecision] = useState<string>();
  if (typeof job.id !== "number") return null;
  const decide = async (next: "interested" | "not_interested") => { setSaving(next); try { const response = await fetch("/api/v1/candidate-action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ candidate_id: job.id, decision: next }) }); const payload = await response.json(); if (!response.ok) throw new Error(payload.error); setDecision(next === "interested" ? "Added to your shortlist." : "Marked not interested."); } catch { setDecision("Your decision could not be saved."); } finally { setSaving(null); } };
  if (decision) return <p className="decision-state">{decision}</p>;
  return <div className="decision-actions" onClick={(event) => event.stopPropagation()}><button onClick={() => decide("interested")} disabled={saving !== null}>{saving === "interested" ? "Adding…" : "Interested"}</button><button className="secondary" onClick={() => decide("not_interested")} disabled={saving !== null}>{saving === "not_interested" ? "Saving…" : "Not interested"}</button></div>;
}

function MatchCard({ job }: { job: Job }) {
  const [expanded, setExpanded] = useState(false); const fit = Math.max(0, Math.min(100, job.ai_fit_score ?? 0));
  const toggle = () => setExpanded((value) => !value);
  return <article className={`match-card ${expanded ? "expanded" : ""}`} role="button" tabIndex={0} onClick={toggle} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } }}>
    <div className="match-summary"><span className="fit-score"><strong>{fit}%</strong><small>fit</small></span><div><h3>{job.title}</h3><p>{job.company}{job.location ? ` · ${job.location}` : ""}</p></div><span className="detail-cue" aria-hidden="true">{expanded ? "−" : "+"}</span></div>
    <div className="match-meta"><span>{job.deadline ? <>Deadline <strong>{formatDate(job.deadline)}</strong></> : "No deadline"}</span>{job.days_until_deadline !== null && job.days_until_deadline !== undefined && <span>{urgency(job.days_until_deadline)}</span>}</div>
    {expanded && <div className="match-detail" onClick={(event) => event.stopPropagation()}><p className="evidence">{job.ai_evidence || "A promising role ready for your review."}</p><div className="role-detail-actions"><a className="quiet-link" href={job.url} target="_blank" rel="noreferrer">Open listing</a><MatchDecision job={job} /></div></div>}
  </article>;
}

function ApplicationEditor({ job, onSaved }: { job: Job; onSaved: () => void }) {
  const [saving, setSaving] = useState(false); const [settling, setSettling] = useState(false); const [message, setMessage] = useState<{ text: string; kind: "success" | "error" }>();
  const flash = (text: string, kind: "success" | "error") => { setMessage({ text, kind }); window.setTimeout(() => setMessage(undefined), 4200); };
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setSaving(true); setMessage(undefined); const form = new FormData(event.currentTarget);
    try { const response = await fetch("/api/v1/application-action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_listing_id: job.job_listing_id, status: form.get("status"), follow_up_date: form.get("follow_up_date"), next_action: form.get("next_action") }) }); const payload = await response.json(); if (!response.ok) { flash(payload.error || "Your changes could not be saved.", "error"); return; } flash("Changes saved.", "success"); setSettling(true); window.setTimeout(onSaved, 260); } catch { flash("Your changes could not be saved. Your existing record is unchanged.", "error"); } finally { setSaving(false); }
  };
  return <><form className={`application-editor ${settling ? "is-settling" : ""}`} onSubmit={save}>
    <label><span>Status</span><select name="status" defaultValue={job.application_status}>{applicationStatuses.map((status) => <option key={status} value={status}>{status.replaceAll("_", " ")}</option>)}</select></label>
    <label><span>Follow up</span><input name="follow_up_date" type="date" defaultValue={job.follow_up_date || ""} /></label>
    <label className="editor-action"><span>Next action</span><input name="next_action" defaultValue={job.next_action || ""} placeholder="Add a next step" /></label>
    <div className="editor-submit"><button disabled={saving || settling}>{saving ? "Saving…" : "Save"}</button></div>
  </form>{message && <div className={`save-notice ${message.kind}`} role={message.kind === "error" ? "alert" : "status"}>{message.text}</div>}</>;
}

function ApplicationScoreDetails({ job }: { job: Job }) {
  const gaps = job.gap_skills?.filter(Boolean) || [];
  const hasDetails = job.ai_fit_score != null || job.skill_coverage_score != null || gaps.length > 0;
  if (!hasDetails) return null;
  return <details className="score-details" onClick={(event) => event.stopPropagation()}><summary>Fit details</summary><div>{job.ai_fit_score != null && <p><strong>{job.ai_fit_score}% fit</strong>{job.ai_fit_tier ? ` · ${job.ai_fit_tier}` : ""}</p>}{job.skill_coverage_score != null && <p>Skill coverage: <strong>{job.skill_coverage_score}%</strong></p>}{gaps.length > 0 && <p>Gaps: <strong>{gaps.join(", ")}</strong></p>}{job.ai_evidence && <p>{job.ai_evidence}</p>}</div></details>;
}

function Applications({ data, refresh, selectedId }: { data?: ApplicationsPayload; refresh: () => void; selectedId?: number }) {
  if (!data) return <main className="content" aria-busy="true"><section className="hero-skeleton"><span /><span /><span /></section></main>;
  const groups: Array<[string, string, Job[]]> = [["Your shortlist", "Roles you are deciding on or actively preparing.", data.interested], ["In motion", "Submitted applications, interviews, and offers.", data.active], ["Closed out", "Your completed application history.", data.past]];
  const toggle = (jobListingId?: number) => { window.location.hash = selectedId === jobListingId ? "#applications" : `#applications/${jobListingId}`; };
  return <main className="content applications-page">{groups.map(([title, description, jobs]) => <section className="application-group" key={title}><div className="section-heading"><div><p className="eyebrow">{jobs.length} roles</p><h2>{title}</h2><p className="section-intro">{description}</p></div></div>{jobs.length ? <div className="workbench-list">{jobs.map((job) => <article className={`workbench-card ${selectedId === job.job_listing_id ? "selected" : ""}`} key={job.job_listing_id} role="button" tabIndex={0} onClick={() => toggle(job.job_listing_id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(job.job_listing_id); } }}><div className="workbench-title"><div><p className="company-label">{job.company}</p><h3>{job.title}</h3><p>{job.location || "Location not listed"}</p></div><a className="quiet-link" href={job.url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Open listing</a></div><div className="workbench-meta"><span>{job.deadline ? <>Deadline <strong>{formatDate(job.deadline)}</strong></> : <strong>No deadline</strong>}</span><span>Fit <strong>{job.ai_fit_score == null ? "—" : `${job.ai_fit_score}%`}</strong></span><span>Skill coverage <strong>{job.skill_coverage_score ?? "—"}{job.skill_coverage_score !== null && job.skill_coverage_score !== undefined ? "%" : ""}</strong></span><span>{job.gap_skill_count || 0} gaps</span></div>{selectedId === job.job_listing_id && <div className="role-detail" onClick={(event) => event.stopPropagation()}><ApplicationScoreDetails job={job} /><ApplicationEditor job={job} onSaved={refresh} /></div>}</article>)}</div> : <p className="empty">Nothing in this stage right now.</p>}</section>)}</main>;
}

function Today({ data }: { data: TodayPayload }) {
  const date = new Intl.DateTimeFormat("en", { weekday: "long", day: "numeric", month: "long" })
    .format(new Date(`${data.generated_at}T12:00:00`));
  return <main className="content">
    <section className="hero" aria-labelledby="today-title">
      <div>
        <p className="eyebrow">{date}</p>
        <h1 id="today-title">Your next good move.</h1>
        <p className="lede">A focused view of the roles and follow-ups that deserve your attention.</p>
      </div>
      <div className="hero-action">
        <span>New matches</span>
        <strong>{data.counts.ready_candidates}</strong>
        <a href="#new-matches">Review now <span aria-hidden="true">↓</span></a>
      </div>
    </section>

    <section className="overview" aria-label="Career Radar overview">
      <div><span>In planning</span><strong>{data.counts.interested_applications}</strong><small>interested or preparing</small></div>
      <div><span>Ready to review</span><strong>{data.decision_queue_count}</strong><small>strong matches in the decision queue</small></div>
      <div><span>In progress</span><strong>{data.counts.in_progress_applications}</strong><small>applied, interview, offer</small></div>
    </section>

    <section className="section-block" aria-labelledby="up-next-title">
      <div className="section-heading">
        <div><p className="eyebrow">Application plan</p><h2 id="up-next-title">Up next</h2></div>
        <a className="text-link" href="#applications">See applications <span aria-hidden="true">→</span></a>
      </div>
      <div className="application-list">
        {data.up_next.length ? data.up_next.map((job) => <ApplicationRow key={job.job_listing_id} job={job} />) : (
          <p className="empty">Add a role to your application list and it will appear here.</p>
        )}
      </div>
    </section>

    <section className="section-block matches" id="new-matches" aria-labelledby="matches-title">
      <div className="section-heading">
        <div><p className="eyebrow">Fresh signal</p><h2 id="matches-title">Worth a look</h2></div>
        <a className="text-link" href="#review">Open review queue <span aria-hidden="true">→</span></a>
      </div>
      <p className="section-intro">Your strongest new matches, ranked with the evidence kept close at hand.</p>
      <div className="match-grid">
        {data.new_matches.length ? data.new_matches.map((job) => <MatchCard key={job.id} job={job} />) : (
          <p className="empty">Nothing new is waiting for your decision.</p>
        )}
      </div>
    </section>
  </main>;
}

export function App() {
  const [data, setData] = useState<TodayPayload>();
  const [error, setError] = useState<string>();
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    window.localStorage.getItem("career-radar-theme") === "dark" ? "dark" : "light"
  );
  const routeFromHash = (hash: string) => ["applications", "discover", "review", "insights", "profile"].includes(hash.slice(1).split("/")[0]) ? hash.slice(1).split("/")[0] : "today";
  const [route, setRoute] = useState(() => window.location.hash);
  const view = routeFromHash(route);
  const selectedApplicationId = () => { const value = Number(route.split("/")[1]); return Number.isInteger(value) ? value : undefined; };
  const [applications, setApplications] = useState<ApplicationsPayload>();
  const [applicationsRevision, setApplicationsRevision] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState<string>();

  useEffect(() => { window.localStorage.setItem("career-radar-theme", theme); }, [theme]);
  useEffect(() => { const updateView = () => setRoute(window.location.hash); window.addEventListener("hashchange", updateView); return () => window.removeEventListener("hashchange", updateView); }, []);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/v1/today", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json()).error || "Career Radar could not load.");
        return response.json() as Promise<TodayPayload>;
      })
      .then(setData)
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) setError("Today is unavailable right now. Your data has not changed.");
      });
    return () => controller.abort();
  }, []);

  const checkForUpdates = async () => {
    setRefreshing(true);
    setNotice(undefined);
    try {
      const response = await fetch("/api/v1/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Updates could not be checked.");
      const todayResponse = await fetch("/api/v1/today");
      if (todayResponse.ok) setData(await todayResponse.json() as TodayPayload);
      setNotice(payload.message || "Updates checked.");
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Updates could not be checked. Your existing data is unchanged.");
    } finally {
      setRefreshing(false);
      window.setTimeout(() => setNotice(undefined), 5500);
    }
  };

  const refreshApplications = (afterSave = false) => { fetch("/api/v1/applications").then(async (response) => { if (!response.ok) throw new Error(); return response.json() as Promise<ApplicationsPayload>; }).then((nextApplications) => { if (afterSave) window.location.hash = "#applications"; setApplications(nextApplications); setApplicationsRevision((revision) => revision + 1); }).catch(() => setError("Applications are unavailable right now. Your data has not changed.")); };
  useEffect(() => { if (view === "applications" && !applications) refreshApplications(); }, [view]);

  return <div className="app-shell" data-theme={theme}>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <header className="app-header">
      <a className="wordmark" href="/" aria-label="Career Radar Today"><Mark />Career Radar</a>
      <nav className="desktop-nav" aria-label="Primary navigation">
        <a className={view === "today" ? "active" : ""} href="#today">Today</a><a href="#discover">Discover</a><a className={view === "applications" ? "active" : ""} href="#applications">Applications</a><a href="#insights">Insights</a><a className={view === "profile" ? "active" : ""} href="#profile">Profile</a>
      </nav>
      <div className="header-actions"><button className="refresh-button" onClick={checkForUpdates} disabled={refreshing}>{refreshing ? "Checking…" : "Check for updates"}</button><button className="theme-toggle" onClick={() => setTheme(theme === "light" ? "dark" : "light")} aria-label={`Switch to ${theme === "light" ? "dark" : "light"} appearance`}>
        {theme === "light" ? "◐" : "◑"}
      </button></div>
    </header>
    <div id="main-content">
      {error ? <main className="content error-state"><p className="eyebrow">Connection issue</p><h1>Career Radar is still safe.</h1><p>{error}</p><a className="button" href="/">Try again</a></main> : view === "applications" ? <Applications key={applicationsRevision} data={applications} refresh={() => refreshApplications(true)} selectedId={selectedApplicationId()} /> : view !== "today" ? <Workspace view={view} /> : data ? <Today data={data} /> : <LoadingToday />}
    </div>
    <nav className="mobile-nav" aria-label="Primary navigation"><a className={view === "today" ? "active" : ""} href="#today">Today</a><a className={view === "applications" ? "active" : ""} href="#applications">Applications</a><a href="#insights">Insights</a><a className={view === "profile" ? "active" : ""} href="#profile">Profile</a></nav>
    {notice && <div className="app-notice" role="status">{notice}</div>}
  </div>;
}
