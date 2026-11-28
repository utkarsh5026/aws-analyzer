/**
 * What needs attention, service by service. A service's page checks it when it opens (its overview, through GET
 * /api/attention/<service>: only that service is read) and leads with what it found; Home lists the services checked
 * so far and says the others haven't been read. Every name a concern points at opens its report.
 */

import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import type { Attention, Checked, Concern, Service } from "../api/types";
import { readAgo } from "../lib/ago";
import { countLevels, countsText, levelWord, worstLevel } from "../lib/attention";
import { routeFor } from "../lib/route";
import { attentions, keepAttention, reports } from "../state";
import { ServiceIcon } from "./bits";

const MOST_TARGETS = 6;
const FIRST_SHOWN = 6;

/** Where a service's check report shows, so "See the whole report" opens it without running it again. */
export function checkReportRoute(attention: Attention): string {
  return routeFor(attention.service, attention.command, { run: "1" });
}

/** What a service's check found, read when first asked for (or again with `checkAgain`). */
export function useAttention(service: Service) {
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");

  const check = async (refresh: boolean) => {
    setChecking(true);
    setError("");
    try {
      const path = `api/attention/${encodeURIComponent(service.key)}${refresh ? "?refresh=true" : ""}`;
      const checked = await api<Checked>(path);
      reports.set(checkReportRoute(checked.attention), checked.report);
      keepAttention(checked.attention);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setChecking(false);
    }
  };

  useEffect(() => {
    if (service.checkable && !attentions.value.has(service.key)) check(false);
  }, [service.key]);

  return { attention: attentions.value.get(service.key), checking, error, checkAgain: () => check(true) };
}

/** The pill before a concern: how bad it is. */
export function LevelPill({ level }: { level: Concern["level"] }) {
  return <span class={`aa-level aa-level-${level}`}>{levelWord(level)}</span>;
}

function TargetLinks({ service, concern }: { service: Service; concern: Concern }) {
  const shown = concern.targets.slice(0, MOST_TARGETS);
  const left = concern.targets.length - shown.length;
  if (!shown.length) return null;
  return (
    <span class="aa-targets">
      {shown.map((target) =>
        target.command ? (
          <a key={target.name} href={routeFor(service.key, target.command, { ...target.arguments, run: "1" })}>
            {target.name}
          </a>
        ) : (
          <code key={target.name}>{target.name}</code>
        ),
      )}
      {left > 0 ? <span class="aa-quiet">{`+${left} more`}</span> : null}
    </span>
  );
}

/** One concern: how bad, what's wrong, why and what to do, and the names it's about. */
export function ConcernLine({ service, concern }: { service: Service; concern: Concern }) {
  return (
    <li class="aa-concern">
      <LevelPill level={concern.level} />
      <div>
        <p class="aa-concern-head">{concern.headline}</p>
        {concern.detail ? <p class="aa-concern-detail">{concern.detail}</p> : null}
        <TargetLinks service={service} concern={concern} />
      </div>
    </li>
  );
}

function Cards({ attention }: { attention: Attention }) {
  if (!attention.cards.length) return null;
  return (
    <dl class="aa-mini-cards">
      {attention.cards.map(([label = "", value = "", tone = ""]) => (
        <div key={label} class={tone ? `aa-tone-${tone}` : ""}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Concerns({ service, attention }: { service: Service; attention: Attention }) {
  const [showAll, setShowAll] = useState(false);
  if (attention.note) return <p class="aa-attention-note aa-bad">{attention.note}</p>;
  if (!attention.concerns.length) {
    return <p class="aa-attention-note aa-good">{`Every check ${attention.call} makes passed.`}</p>;
  }
  const shown = showAll ? attention.concerns : attention.concerns.slice(0, FIRST_SHOWN);
  const left = attention.concerns.length - shown.length;
  return (
    <>
      <ul class="aa-concerns">
        {shown.map((concern) => (
          <ConcernLine
            key={`${concern.level}.${concern.headline}.${concern.detail}`}
            service={service}
            concern={concern}
          />
        ))}
      </ul>
      {left > 0 ? (
        <button type="button" class="aa-ghost aa-more" onClick={() => setShowAll(true)}>
          {`Show ${left} more`}
        </button>
      ) : null}
    </>
  );
}

function CheckedLine({ attention, onAgain }: { attention: Attention; onAgain: () => void }) {
  const source = attention.from_memory ? "kept by the server" : `${attention.seconds} s from AWS`;
  return (
    <p class="aa-attention-meta">
      <span>{`From ${attention.call} · checked ${readAgo(attention.checked_at)} · ${source}`}</span>
      <a href={checkReportRoute(attention)}>See the whole report</a>
      <button type="button" class="aa-ghost" onClick={onAgain}>
        ↻ Check again
      </button>
    </p>
  );
}

/** A service page's first block: "Needs attention in <service>", checked when the page opens. */
export function NeedsAttention({ service }: { service: Service }) {
  const { attention, checking, error, checkAgain } = useAttention(service);
  if (!service.checkable) return null;
  const counts = attention ? countsText(countLevels(attention.concerns)) : "";
  return (
    <section class={`aa-attention${attention ? ` aa-worst-${worstLevel(attention) || "none"}` : ""}`}>
      <div class="aa-attention-head">
        <h2>{`Needs attention in ${service.title}`}</h2>
        <span class="aa-quiet">{attention && !checking ? counts : `only ${service.title} is read`}</span>
      </div>
      {checking ? (
        <p class="aa-attention-note">
          <span class="aa-spin" aria-hidden="true" />
          {` Checking ${service.title}… only ${service.title} is read.`}
        </p>
      ) : null}
      {error && !checking ? <p class="aa-attention-note aa-bad">{error}</p> : null}
      {attention && !checking ? (
        <>
          {attention.verdict ? (
            <p class={`aa-verdict aa-tone-${attention.tone || "warn"}`}>{attention.verdict}</p>
          ) : null}
          <Cards attention={attention} />
          <Concerns service={service} attention={attention} />
          <CheckedLine attention={attention} onAgain={checkAgain} />
        </>
      ) : null}
    </section>
  );
}

/** Asks the server for the checks it kept (after a reload), which reads nothing from AWS. */
function useKeptChecks() {
  useEffect(() => {
    api<{ checked: Attention[] }>("api/attention")
      .then((kept) => {
        for (const attention of kept.checked) keepAttention(attention);
      })
      .catch(() => undefined);
  }, []);
}

/** Home's "Checked so far": the services already checked and what they found; the others haven't been read. */
export function CheckedSoFar({ services }: { services: Service[] }) {
  useKeptChecks();
  const checkable = services.filter((service) => service.checkable);
  const checked = checkable.filter((service) => attentions.value.has(service.key));
  const waiting = checkable.filter((service) => !attentions.value.has(service.key));
  return (
    <section class="aa-checked" aria-label="Checked so far">
      <p class="aa-label">Checked so far</p>
      {checked.map((service) => {
        const attention = attentions.value.get(service.key) as Attention;
        const worst = worstLevel(attention);
        return (
          <a key={service.key} class="aa-checked-line" href={`#/${service.key}`}>
            <ServiceIcon service={service} />
            <span class="aa-checked-name">{service.title}</span>
            <span class="aa-checked-what">
              <span class={worst ? `aa-checked-${worst}` : "aa-good"}>
                {attention.note ? "couldn't read it" : countsText(countLevels(attention.concerns))}
              </span>
              {` · ${readAgo(attention.checked_at)}`}
            </span>
          </a>
        );
      })}
      <p class="aa-quiet aa-checked-rest">
        {checked.length
          ? `${waiting.length} other services haven't been checked. `
          : "Nothing checked yet: nothing is read until you pick a service. "}
        Opening a service checks it, and only it.
      </p>
    </section>
  );
}
