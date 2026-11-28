/** Finding a command (Ctrl K): type words, ↑ ↓ to move, Enter to open, Esc or a click outside to close. */

import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { findCommands, type Hit } from "../lib/find";
import { routeFor } from "../lib/route";
import { catalog, finderOpen } from "../state";
import { ServiceIcon } from "./bits";

interface HitProps {
  id: string;
  hit: Hit;
  isPicked: boolean;
  onPick: () => void;
}

function HitLine({ id, hit, isPicked, onPick }: HitProps) {
  const line = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (isPicked) line.current?.scrollIntoView({ block: "nearest" });
  }, [isPicked]);
  return (
    // biome-ignore lint/a11y/noNoninteractiveElementToInteractiveRole: an option of the finder's listbox
    // biome-ignore lint/a11y/useFocusableInteractive: focus stays in the search box (aria-activedescendant)
    // biome-ignore lint/a11y/useKeyWithClickEvents: the search box's ↑ ↓ and Enter pick it from the keyboard
    <li ref={line} id={id} class={isPicked ? "aa-on" : ""} role="option" aria-selected={isPicked} onClick={onPick}>
      <ServiceIcon service={hit.service} />
      <div class="aa-t">
        <code>{hit.command.name}</code>
        <span>{` · ${hit.service.title}`}</span>
      </div>
      <p>{hit.command.about}</p>
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
  const found = findCommands(catalog.value?.services ?? [], query);
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
    location.hash = routeFor(hit.service.key, hit.command.name);
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
      <div class="aa-palette-box" role="dialog" aria-modal="true" aria-label="Find a command">
        <input
          ref={input}
          id="palette-input"
          type="search"
          aria-controls="palette-list"
          aria-activedescendant={found.length ? hitId(shownPick) : undefined}
          placeholder="A command or what you want to see: errors, largest files…"
          autocomplete="off"
          spellcheck={false}
          value={query}
          onInput={(event) => {
            setQuery(event.currentTarget.value);
            setPicked(0);
          }}
          onKeyDown={keys}
        />
        {/* biome-ignore lint/a11y/noNoninteractiveElementToInteractiveRole: the finder's list of commands */}
        <ul id="palette-list" role="listbox">
          {isOpen
            ? found.map((hit, index) => (
                <HitLine
                  id={hitId(index)}
                  key={`${hit.service.key}.${hit.command.name}`}
                  hit={hit}
                  isPicked={index === shownPick}
                  onPick={() => go(index)}
                />
              ))
            : null}
        </ul>
        <p class="aa-palette-hint">↑ ↓ to move · Enter to open · Esc to close</p>
      </div>
    </div>
  );
}
