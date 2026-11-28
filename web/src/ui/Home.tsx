/**
 * The home page: search one service at a time. The service picked is the only one read (its listing call, through
 * GET /api/resources); services read before search instantly and their matches show "from memory"; the others are
 * buttons, so reading one is a click. A pasted s3:// path, ARN or console link picks its service by itself.
 */

import { useEffect, useState } from "preact/hooks";
import { api } from "../api/client";
import type { Catalog, Resource, ResourceIndex, Service } from "../api/types";
import { routeFor } from "../lib/route";
import { rankResources, readPasted } from "../lib/search";
import { chooseSearchService, indexes, keepIndex, openInNewTab, searchService } from "../state";
import { ServiceIcon } from "./bits";

const FROM_MEMORY_EACH = 3;
const FROM_MEMORY_MOST = 6;
const SECOND = 1000;
const MINUTE = 60 * SECOND;

interface Reading {
  key: string;
  error: string;
}

/** 'just now', '4 min ago', '2 h ago': how long ago something was read. */
function readAgo(iso: string, now = Date.now()): string {
  const gone = now - Date.parse(iso);
  if (Number.isNaN(gone) || gone < MINUTE) return "just now";
  const minutes = Math.round(gone / MINUTE);
  return minutes < 60 ? `${minutes} min ago` : `${Math.round(minutes / 60)} h ago`;
}

function openTarget(service: string, resource: Resource): string {
  return routeFor(service, resource.command, { ...resource.arguments, run: "1" });
}

function kindsOf(index: ResourceIndex): string {
  const kinds = [...new Set(index.resources.map((each) => each.kind))];
  return kinds.length ? kinds.map((kind) => `${kind}s`).join(" and ") : "things";
}

interface HitProps {
  service: Service;
  resource: Resource;
  isPicked: boolean;
  onPick: () => void;
}

function Hit({ service, resource, isPicked, onPick }: HitProps) {
  const target = openTarget(service.key, resource);
  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: the search box's ↑ ↓ and Enter pick it from the keyboard
    // biome-ignore lint/a11y/noStaticElementInteractions: pointing picks the line; its link and button do the rest
    <div class={`aa-hit${isPicked ? " aa-on" : ""}`} onClick={onPick}>
      <ServiceIcon service={service} />
      <a class="aa-hit-name" href={target}>
        {resource.name}
      </a>
      <span class="aa-hit-what">{`${resource.kind} · ${resource.detail}`}</span>
      <button type="button" class="aa-ghost aa-hit-tab" onClick={() => openInNewTab(target)}>
        ↗ New tab
      </button>
    </div>
  );
}

interface MemoryHit {
  service: Service;
  resource: Resource;
  readAt: string;
}

function fromMemory(services: Service[], current: string, query: string): MemoryHit[] {
  if (!query.trim()) return [];
  const found: MemoryHit[] = [];
  for (const service of services) {
    const index = indexes.value.get(service.key);
    if (!index || service.key === current) continue;
    for (const resource of rankResources(index.resources, query, FROM_MEMORY_EACH)) {
      found.push({ service, resource, readAt: index.read_at });
    }
  }
  return found.slice(0, FROM_MEMORY_MOST);
}

function MemoryHits({ hits }: { hits: MemoryHit[] }) {
  if (!hits.length) return null;
  return (
    <div class="aa-memory">
      <p class="aa-label">
        Also matching, in services read earlier <span class="aa-cached">from memory · no new reads</span>
      </p>
      {hits.map((hit) => (
        <a
          key={`${hit.service.key}.${hit.resource.kind}.${hit.resource.name}`}
          href={openTarget(hit.service.key, hit.resource)}
        >
          <ServiceIcon service={hit.service} />
          <code>{hit.resource.name}</code>
          <span>{`${hit.service.title} · ${hit.resource.kind} · read ${readAgo(hit.readAt)}`}</span>
        </a>
      ))}
    </div>
  );
}

function ServiceChips({ services, current }: { services: Service[]; current: string }) {
  return (
    <fieldset class="aa-chips" aria-label="Search in">
      <span class="aa-chips-label">Search in</span>
      {services.map((service) => {
        const isRead = indexes.value.has(service.key);
        return (
          <button
            key={service.key}
            type="button"
            class={`aa-chip${service.key === current ? " aa-on" : ""}`}
            aria-pressed={service.key === current}
            onClick={() => chooseSearchService(service.key)}
          >
            <ServiceIcon service={service} />
            {service.title}
            {isRead ? <span class="aa-read" title="Already read: searching it asks AWS nothing" /> : null}
          </button>
        );
      })}
    </fieldset>
  );
}

interface ResultsProps {
  service: Service;
  index: ResourceIndex | undefined;
  reading: Reading | null;
  hits: Resource[];
  picked: number;
  query: string;
  onPick: (index: number) => void;
  onAgain: () => void;
}

function ResultsHead({ service, index, onAgain }: { service: Service; index: ResourceIndex; onAgain: () => void }) {
  const source = index.from_memory ? "kept by the server" : `${index.seconds} s from AWS`;
  return (
    <div class="aa-results-head">
      <span>{`${index.resources.length.toLocaleString()} ${kindsOf(index)} in ${service.title}`}</span>
      <span class="aa-quiet">{`read ${readAgo(index.read_at)} · ${source}`}</span>
      <button type="button" class="aa-ghost" onClick={onAgain}>
        ↻ Read again
      </button>
    </div>
  );
}

function Results({ service, index, reading, hits, picked, query, onPick, onAgain }: ResultsProps) {
  if (reading?.key === service.key && reading.error) {
    return <p class="aa-results-note aa-bad">{reading.error}</p>;
  }
  if (!index) {
    return (
      <p class="aa-results-note">
        <span class="aa-spin" aria-hidden="true" />
        {` Reading ${service.title}'s names… only ${service.title} is read.`}
      </p>
    );
  }
  return (
    <section class="aa-results" aria-label={`Results in ${service.title}`}>
      <ResultsHead service={service} index={index} onAgain={onAgain} />
      {hits.map((resource, position) => (
        <Hit
          key={`${resource.kind}.${resource.name}`}
          service={service}
          resource={resource}
          isPicked={position === picked}
          onPick={() => onPick(position)}
        />
      ))}
      {hits.length ? null : (
        <p class="aa-results-note">{query ? `Nothing in ${service.title} matches “${query}”.` : "Nothing here."}</p>
      )}
    </section>
  );
}

function NotRead({ services }: { services: Service[] }) {
  const waiting = services.filter((service) => !indexes.value.has(service.key));
  if (!waiting.length) return null;
  return (
    <p class="aa-notread">
      Not searched yet:{" "}
      {waiting.map((service) => (
        <button key={service.key} type="button" class="aa-ghost" onClick={() => chooseSearchService(service.key)}>
          {service.title}
        </button>
      ))}{" "}
      <span class="aa-quiet">each reads that service once, then it's instant too.</span>
    </p>
  );
}

function Browse({ services }: { services: Service[] }) {
  return (
    <aside class="aa-browse">
      <p class="aa-label">Every command, by service</p>
      {services.map((service) => (
        <a key={service.key} href={`#/${service.key}`}>
          <ServiceIcon service={service} />
          {service.title}
        </a>
      ))}
      <p class="aa-quiet aa-paste-hint">
        Pasting picks the service for you: an <code>s3://</code> path, an ARN or a console link.
      </p>
    </aside>
  );
}

function useIndex(key: string) {
  const [reading, setReading] = useState<Reading | null>(null);

  const read = async (refresh: boolean) => {
    setReading({ key, error: "" });
    try {
      keepIndex(await api<ResourceIndex>(`api/resources/${encodeURIComponent(key)}${refresh ? "?refresh=true" : ""}`));
      setReading(null);
    } catch (error) {
      setReading({ key, error: error instanceof Error ? error.message : String(error) });
    }
  };

  useEffect(() => {
    if (key && !indexes.value.has(key)) read(false);
  }, [key]);

  return { reading, readAgain: () => read(true) };
}

export function Home({ loaded }: { loaded: Catalog }) {
  const searchable = loaded.services.filter((service) => service.searchable);
  const service = searchable.find((each) => each.key === searchService.value) ?? searchable[0];
  const key = service?.key ?? "";
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState(0);
  const { reading, readAgain } = useIndex(key);
  const index = indexes.value.get(key);
  const hits = index ? rankResources(index.resources, query) : [];

  if (!service) return <p class="aa-loading">No service can be searched yet.</p>;

  const typed = (text: string) => {
    const pasted = readPasted(text);
    if (pasted && searchable.some((each) => each.key === pasted.service)) {
      chooseSearchService(pasted.service);
      setQuery(pasted.name);
    } else {
      setQuery(text);
    }
    setPicked(0);
  };
  const keys = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setPicked(Math.max(0, Math.min(hits.length - 1, picked + step)));
    } else if (event.key === "Enter" && hits[picked]) {
      location.hash = openTarget(key, hits[picked]);
    }
  };

  return (
    <div class="aa-home">
      <div class="aa-home-main">
        <p class="aa-label">{`Read-only · ${loaded.region || "boto3's default region"}`}</p>
        <h1 class="aa-home-title">Find anything in your account</h1>
        <div class="aa-searchbox">
          <select
            aria-label="Service to search"
            value={key}
            onChange={(event) => chooseSearchService(event.currentTarget.value)}
          >
            {searchable.map((each) => (
              <option key={each.key} value={each.key}>
                {each.title}
              </option>
            ))}
          </select>
          <input
            id="search"
            type="search"
            aria-label={`Search ${service.title}`}
            placeholder={`Search ${service.title} by name…`}
            autocomplete="off"
            spellcheck={false}
            value={query}
            onInput={(event) => typed(event.currentTarget.value)}
            onKeyDown={keys}
          />
        </div>
        <ServiceChips services={searchable} current={key} />
        <Results
          service={service}
          index={index}
          reading={reading}
          hits={hits}
          picked={picked}
          query={query}
          onPick={setPicked}
          onAgain={readAgain}
        />
        <MemoryHits hits={fromMemory(searchable, key, query)} />
        <NotRead services={searchable} />
      </div>
      <Browse services={loaded.services} />
    </div>
  );
}
