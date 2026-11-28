/** Finding a command (Ctrl K), and listing a service's commands by their help() group. */

import type { Command, Service } from "../api/types";

const MOST_FOUND = 40;
const NAME_WEIGHT = 10;
const ABOUT_PENALTY = 1000;

export interface Hit {
  service: Service;
  command: Command;
  score: number;
}

function wordsOf(query: string): string[] {
  return query.toLowerCase().split(/\s+/).filter(Boolean);
}

function hitFor(service: Service, command: Command, words: string[]): Hit | null {
  const name = command.name.toLowerCase();
  const title = service.title.toLowerCase();
  const text = `${title} ${name} ${command.about.toLowerCase()}`;
  if (!words.every((word) => text.includes(word))) return null;
  const inName = words.filter((word) => name.includes(word) || title.includes(word));
  return { service, command, score: inName.length * NAME_WEIGHT - command.about.length / ABOUT_PENALTY };
}

/**
 * The commands every word of the query is in (name, service or description), those that name it first, at most
 * MOST_FOUND; an empty query lists every command.
 */
export function findCommands(services: Service[], query: string): Hit[] {
  const words = wordsOf(query);
  const found: Hit[] = [];
  for (const service of services) {
    for (const command of service.commands) {
      const hit = hitFor(service, command, words);
      if (hit) found.push(hit);
    }
  }
  return found.sort((a, b) => b.score - a.score).slice(0, MOST_FOUND);
}

/** A service's commands by group, in the order the groups first come. */
export function byGroup(commands: Command[]): Map<string, Command[]> {
  const groups = new Map<string, Command[]>();
  for (const command of commands) {
    const group = groups.get(command.group) ?? [];
    group.push(command);
    groups.set(command.group, group);
  }
  return groups;
}
