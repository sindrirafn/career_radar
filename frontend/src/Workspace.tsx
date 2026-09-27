import { useEffect, useState } from "react";

type Item = Record<string, unknown>;
type WorkspaceData = { discover: Item[]; discover_count: number; review: { ready: Item[]; manual: Item[]; duplicates: Item[] }; insights: Item[]; profile: Item[] };
const text = (value: unknown) => value == null ? "" : String(value);
const percent = (value: unknown) => value == null ? null : `${Math.round(Number(value))}%`;
const list = (value: unknown) => Array.isArray(value) ? value.map(text).filter(Boolean) : [];

function ScoreDetails({ item }: { item: Item }) {
  const gaps = list(item.gap_skills ?? item.ai_skill_gaps); const fit = percent(item.ai_fit_score); const coverage = percent(item.skill_coverage_score); const evidence = text(item.ai_evidence);
  if (!fit && !coverage && !gaps.length && !evidence) return null;
  return <details className="score-details" onClick={(event) => event.stopPropagation()}><summary>Fit details</summary><div>{fit && <p><strong>{fit} fit</strong>{item.ai_fit_tier ? ` · ${text(item.ai_fit_tier)}` : ""}</p>}{coverage && <p>Skill coverage: <strong>{coverage}</strong></p>}{gaps.length > 0 && <p>Gaps: <strong>{gaps.join(", ")}</strong></p>}{evidence && <p>{evidence}</p>}</div></details>;
}

function DecisionActions({ item, onDone }: { item: Item; onDone: () => void }) {
  const [saving, setSaving] = useState<"interested" | "not_interested" | null>(null); const [message, setMessage] = useState<string>();
  const candidateId = typeof item.id === "number" ? item.id : undefined; const jobListingId = typeof item.job_listing_id === "number" ? item.job_listing_id : undefined; const existing = text(item.application_status);
  const decide = async (decision: "interested" | "not_interested") => { setSaving(decision); setMessage(undefined); const endpoint = candidateId ? "/api/v1/candidate-action" : "/api/v1/application-action"; const body = candidateId ? { candidate_id: candidateId, decision } : { job_listing_id: jobListingId, status: decision === "interested" ? "interested" : "not_applying" }; try { const response = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); const payload = await response.json(); if (!response.ok) throw new Error(payload.error || "Your decision could not be saved."); setMessage(decision === "interested" ? "Added to your shortlist." : "Marked not interested."); onDone(); } catch (reason) { setMessage(reason instanceof Error ? reason.message : "Your decision could not be saved."); } finally { setSaving(null); } };
  if (existing && existing !== "not_started") return <p className="decision-state">{existing === "not_applying" ? "Not interested" : existing === "tracking_only" ? "Keeping for future review" : "On your application list"}</p>;
  return <div className="decision-actions" onClick={(event) => event.stopPropagation()}><button onClick={() => decide("interested")} disabled={saving !== null}>{saving === "interested" ? "Adding…" : "Interested"}</button><button className="secondary" onClick={() => decide("not_interested")} disabled={saving !== null}>{saving === "not_interested" ? "Saving…" : "Not interested"}</button>{message && <span role="status">{message}</span>}</div>;
}

function RoleCard({ item, onDone, kind }: { item: Item; onDone: () => void; kind: "discover" | "review" }) {
  const [expanded, setExpanded] = useState(false); const fit = percent(item.ai_fit_score ?? item.rule_relevance_score); const deadline = text(item.deadline) || "No deadline"; const gaps = Number(item.gap_skill_count ?? list(item.ai_skill_gaps).length);
  const toggle = () => setExpanded((value) => !value);
  return <article className={`workspace-role ${expanded ? "expanded" : ""}`} role="button" tabIndex={0} onClick={toggle} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } }}><div className="workspace-role-summary">{fit && <span className="fit-score"><strong>{fit}</strong><small>fit</small></span>}<div className="workspace-role-copy"><p className="company-label">{text(item.company)}</p><h3>{text(item.title)}</h3><p>{text(item.location) || "Location not listed"}</p></div><span className="detail-cue" aria-hidden="true">{expanded ? "−" : "+"}</span></div><div className="workspace-role-meta"><span>{deadline === "No deadline" ? deadline : <>Deadline <strong>{deadline}</strong></>}</span>{fit && <span>Fit <strong>{fit}</strong></span>}{item.skill_coverage_score != null && <span>Coverage <strong>{percent(item.skill_coverage_score)}</strong></span>}{gaps > 0 && <span>{gaps} gaps</span>}</div>{expanded && <div className="role-detail" onClick={(event) => event.stopPropagation()}>{kind === "review" && text(item.ai_evidence) && <p className="role-evidence">{text(item.ai_evidence)}</p>}<ScoreDetails item={item} /><div className="role-detail-actions"><a className="quiet-link" href={text(item.url)} target="_blank" rel="noreferrer">Open listing</a><DecisionActions item={item} onDone={onDone} /></div></div>}</article>;
}

function Insights({ items }: { items: Item[] }) { return <section className="workspace-section"><div className="simple-list">{items.map((item, index) => <article className="simple-row insight-row" key={index}><div><p className="company-label">{text(item.profile_status) === "missing" ? "Gap to consider" : text(item.proficiency_level) || "Profile skill"}</p><h3>{text(item.normalized_name)}</h3><p>{text(item.jobs_requesting)} active listings mention this. {list(item.example_jobs).slice(0, 2).join(" · ")}</p></div><strong>{text(item.demand_percent)}%</strong></article>)}</div></section>; }
function Profile({ items }: { items: Item[] }) { return <section className="workspace-section"><div className="simple-list">{items.map((item, index) => <article className="simple-row profile-row" key={index}><div><p className="company-label">{text(item.proficiency_level)}</p><h3>{text(item.name)}</h3><p>{text(item.evidence)}</p></div><span>{item.include_in_gap_analysis ? "Included in matching" : "Reference only"}</span></article>)}</div></section>; }

export function Workspace({ view }: { view: string }) {
  const [data, setData] = useState<WorkspaceData>(); const load = () => { fetch("/api/v1/workspace").then((response) => response.json()).then(setData); }; useEffect(() => { load(); }, [view]);
  if (!data) return <main className="content" aria-busy="true"><section className="hero-skeleton"><span /><span /><span /></section></main>;
  if (view === "discover") return <main className="content workspace workspace-discover"><section className="workspace-context"><p className="eyebrow">Market library</p><h1>{data.discover_count} active listings</h1><p>Every active listing in your current archive. Open a role to see the evidence and make a decision.</p></section><section className="workspace-section"><div className="section-heading"><h2>All active listings</h2><span className="section-count">{data.discover.length} shown</span></div><div className="workspace-role-list">{data.discover.map((item, index) => <RoleCard key={index} item={item} onDone={load} kind="discover" />)}</div></section></main>;
  if (view === "review") {
    const removeDecidedCandidate = (candidateId: unknown) => {
      setData((current) => current ? {
        ...current,
        review: {
          ...current.review,
          ready: current.review.ready.filter((item) => item.id !== candidateId),
          manual: current.review.manual.filter((item) => item.id !== candidateId),
        },
      } : current);
      load();
    };
    return <main className="content workspace workspace-review"><section className="workspace-context"><p className="eyebrow">Decision queue</p><h1>{data.review.ready.length} ready to consider</h1><p>{data.review.manual.length} roles need a second look before you decide.</p></section><section className="workspace-section"><h2>Ready to consider</h2><div className="workspace-role-list">{data.review.ready.map((item) => <RoleCard key={`ready:${String(item.id)}`} item={item} onDone={() => removeDecidedCandidate(item.id)} kind="review" />)}</div></section><section className="workspace-section"><h2>Worth a second look</h2><div className="workspace-role-list">{data.review.manual.map((item) => <RoleCard key={`manual:${String(item.id)}`} item={item} onDone={() => removeDecidedCandidate(item.id)} kind="review" />)}</div></section></main>;
  }
  if (view === "insights") return <main className="content workspace workspace-insights"><section className="workspace-context"><p className="eyebrow">Pattern library</p><h1>Signals behind the search.</h1><p>Skills employers are asking for across the active market.</p></section><Insights items={data.insights} /></main>;
  return <main className="content workspace workspace-profile"><section className="workspace-context"><p className="eyebrow">Your profile</p><h1>The evidence Career Radar uses.</h1><p>Your skill inventory and the source behind each signal.</p></section><Profile items={data.profile} /></main>;
}
