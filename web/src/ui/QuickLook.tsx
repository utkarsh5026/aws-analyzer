/**
 * The quick look beside the search's results: what the listing already said about the line picked (its facts), what
 * its service's check found about it when the service was checked, and how to open it. It reads nothing: a service
 * not checked yet says so, and opening its page is the click that checks it.
 */

import { useState } from "preact/hooks";
import type { Resource, Service } from "../api/types";
import { concernsAbout } from "../lib/attention";
import { callText } from "../lib/call";
import { routeFor } from "../lib/route";
import { attentions, openInNewTab, reports } from "../state";
import { ConcernLine } from "./Attention";
import { ServiceIcon } from "./bits";

const COPIED_SHOWN_MS = 1600;
const MOST_CONCERNS = 3;

/** Where a resource's report opens, run at once. */
export function openTarget(service: string, resource: Resource): string {
  return routeFor(service, resource.command, { ...resource.arguments, run: "1" });
}

/** The resource's report as a notebook cell: 'ui.function_info('orders-etl')'. */
function cellFor(service: Service, resource: Resource): string {
  const command = service.commands.find((each) => each.name === resource.command);
  return command ? `ui.${callText(command, resource.arguments)}` : `ui.${resource.command}()`;
}

function CopyCell({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), COPIED_SHOWN_MS);
    } catch {
      setCopied(false);
    }
  };
  return (
    <button type="button" class="aa-ghost" title={text} onClick={copy}>
      {copied ? "Copied" : "Copy as cell"}
    </button>
  );
}

function Facts({ resource }: { resource: Resource }) {
  if (!resource.facts.length) {
    return <p class="aa-quiet">{`Its listing gives only the name: ${resource.command}() reads the rest.`}</p>;
  }
  return (
    <dl class="aa-facts">
      {resource.facts.map((fact) => (
        <div key={fact.label}>
          <dt>{fact.label}</dt>
          <dd>{fact.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function WhatTheCheckSaid({ service, resource }: { service: Service; resource: Resource }) {
  if (!service.checkable) return null;
  const attention = attentions.value.get(service.key);
  if (!attention) {
    return (
      <p class="aa-look-check aa-quiet">
        {`${service.title} hasn't been checked yet. `}
        <a href={`#/${service.key}`}>{`Open ${service.title}`}</a>
        {` to check it (only ${service.title} is read).`}
      </p>
    );
  }
  const about = concernsAbout(attention, resource.name);
  if (!about.length) {
    return <p class="aa-look-check aa-good">{`${attention.call} found nothing about ${resource.name}.`}</p>;
  }
  const shown = about.slice(0, MOST_CONCERNS);
  const left = about.length - shown.length;
  return (
    <div class="aa-look-check">
      <p class="aa-label">{`What ${attention.call} found`}</p>
      <ul class="aa-concerns aa-concerns-small">
        {shown.map((concern) => (
          <ConcernLine key={`${concern.level}.${concern.headline}`} service={service} concern={concern} />
        ))}
      </ul>
      {left > 0 ? <a href={`#/${service.key}`}>{`${left} more on ${service.title}'s page`}</a> : null}
    </div>
  );
}

/** The preview of the result picked, with Open (as Enter does), ↗ New tab and Copy as cell. */
export function QuickLook({ service, resource }: { service: Service; resource: Resource | undefined }) {
  if (!resource) return null;
  const target = openTarget(service.key, resource);
  const isKept = reports.has(target);
  return (
    <aside class="aa-look" aria-label={`Quick look at ${resource.name}`}>
      <div class="aa-look-head">
        <ServiceIcon service={service} />
        <div>
          <p class="aa-look-name">{resource.name}</p>
          <p class="aa-quiet">{`${resource.kind} · ${service.title}`}</p>
        </div>
      </div>
      <Facts resource={resource} />
      <WhatTheCheckSaid service={service} resource={resource} />
      <div class="aa-look-actions">
        <a class="aa-run aa-run-link" href={target}>
          {`Open ${resource.command}()`}
        </a>
        <button type="button" class="aa-ghost" onClick={() => openInNewTab(target)}>
          ↗ New tab
        </button>
        <CopyCell text={cellFor(service, resource)} />
      </div>
      <p class="aa-quiet aa-look-hint">
        {isKept ? "Opened earlier: its report shows at once. " : ""}
        Enter opens it, ↑ ↓ pick another.
      </p>
    </aside>
  );
}
