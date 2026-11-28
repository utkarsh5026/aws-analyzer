/**
 * Ctrl K: jump to anything already read (the services the search has listed: no AWS call), or to a command. Type
 * words, ↑ ↓ to move, Enter to open, Esc or a click outside to close.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { Resource, Service } from "../api/types";
import { type Hit as CommandHit, findCommands } from "../lib/find";
import { routeFor } from "../lib/route";
import { rankResources } from "../lib/search";
import { catalog, finderOpen, indexes } from "../state";
import { ServiceIcon } from "./bits";

const RESOURCES_EACH = 3;
const RESOURCES_MOST = 6;

interface ResourceHit {
  type: "resource";
  service: Service;
  resource: Resource;
}

type FinderHit = ResourceHit | ({ type: "command" } & CommandHit);

function resourceHits(services: Service[], query: string): ResourceHit[] {
  if (!query.trim()) return [];
  const found: ResourceHit[] = [];
  for (const service of services) {
    const index = indexes.value.get(service.key);
    if (!index) continue;
    for (const resource of rankResources(index.resources, query, RESOURCES_EACH)) {
      found.push({ type: "resource", service, resource });
    }
  }
  return found.slice(0, RESOURCES_MOST);
}

function targetOf(hit: FinderHit): string {
  if (hit.type === "command") return routeFor(hit.service.key, hit.command.name);
  return routeFor(hit.service.key, hit.resource.command, { ...hit.resource.arguments, run: "1" });
}

function keyOf(hit: FinderHit): string {
  return hit.type === "command"
    ? `c.${hit.service.key}.${hit.command.name}`
    : `r.${hit.service.key}.${hit.resource.kind}.${hit.resource.name}`;
}

interface HitProps {
  id: string;
  hit: FinderHit;
  isPicked: boolean;
  onPick: () => void;
}

function HitLine({ id, hit, isPicked, onPick }: HitProps) {
  const line = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (isPicked) line.current?.scrollIntoView({ block: "nearest" });
  }, [isPicked]);
  const name = hit.type === "command" ? hit.command.name : hit.resource.name;
  const what = hit.type === "command" ? hit.service.title : `${hit.resource.kind} · ${hit.service.title}`;
  const about = hit.type === "command" ? hit.command.about : hit.resource.detail;
  return (
    // biome-ignore lint/a11y/noNoninteractiveElementToInteractiveRole: an option of the finder's listbox
    // biome-ignore lint/a11y/useFocusableInteractive: focus stays in the search box (aria-activedescendant)
    // biome-ignore lint/a11y/useKeyWithClickEvents: the search box's ↑ ↓ and Enter pick it from the keyboard
    <li ref={line} id={id} class={isPicked ? "aa-on" : ""} role="option" aria-selected={isPicked} onClick={onPick}>
      <ServiceIcon service={hit.service} />
      <div class="aa-t">
        <code>{name}</code>
        <span>{` · ${what}`}</span>
      </div>
      <p>{about}</p>
    </li>
  );
}

function hitId(index: number): string {
  return `palette-hit-${index}`;
}

function clamp(value: number, highest: number): number {
  return Math.max(0, Math.min(highest, value));
}

export function Finder() {
  const isOpen = finderOpen.value;
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const services = catalog.value?.services ?? [];
  const commands: FinderHit[] = findCommands(services, query).map((hit) => ({ type: "command", ...hit }));
  const found: FinderHit[] = [...resourceHits(services, query), ...commands];
  const shownPick = clamp(picked, found.length - 1);

  useLayoutEffect(() => {
    if (!isOpen) return;
    setQuery("");
    setPicked(0);
    input.current?.focus();
  }, [isOpen]);

  const close = () => {
    finderOpen.value = false;
  };
  const go = (index: number) => {
    const hit = found[index];
    if (!hit) return;
    close();
    location.hash = targetOf(hit);
  };
  const keys = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setPicked(clamp(shownPick + (event.key === "ArrowDown" ? 1 : -1), found.length - 1));
    } else if (event.key === "Enter") {
      go(shownPick);
    } else if (event.key === "Escape") {
      close();
    }
  };
  const outside = (event: MouseEvent) => {
    if (event.target === event.currentTarget) close();
  };

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: a click on the backdrop closes it, as Esc does
    // biome-ignore lint/a11y/useKeyWithClickEvents: Esc is the keyboard's way to close it
    <div class="aa-palette" id="palette" hidden={!isOpen} onClick={outside}>
      <div class="aa-palette-box" role="dialog" aria-modal="true" aria-label="Search">
        <input
          ref={input}
          id="palette-input"
          type="search"
          aria-controls="palette-list"
          aria-activedescendant={found.length ? hitId(shownPick) : undefined}
          placeholder="Something already read, or a command: orders-etl, errors, largest files…"
          autocomplete="off"
          spellcheck={false}
          value={query}
          onInput={(event) => {
            setQuery(event.currentTarget.value);
            setPicked(0);
          }}
          onKeyDown={keys}
        />
        {/* biome-ignore lint/a11y/noNoninteractiveElementToInteractiveRole: the finder's list */}
        <ul id="palette-list" role="listbox">
          {isOpen
            ? found.map((hit, index) => (
                <HitLine
                  id={hitId(index)}
                  key={keyOf(hit)}
                  hit={hit}
                  isPicked={index === shownPick}
                  onPick={() => go(index)}
                />
              ))
            : null}
        </ul>
        <p class="aa-palette-hint">↑ ↓ to move · Enter to open · Esc to close · names already read, no AWS calls</p>
      </div>
    </div>
  );
}
