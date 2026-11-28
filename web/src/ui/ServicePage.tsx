/** A service's page: what's live in it (Lambda: what's running now), then its commands by group, each a card. */

import type { ComponentType } from "preact";
import type { Service } from "../api/types";
import { byGroup } from "../lib/find";
import { routeFor } from "../lib/route";
import { Crumbs } from "./bits";
import { LiveActivity } from "./LiveActivity";

const LIVE_PANELS: Record<string, ComponentType> = { lambda_functions: LiveActivity };

export function ServicePage({ service }: { service: Service }) {
  const viewName = service.title === "S3" ? "S3View" : "its View";
  const Live = LIVE_PANELS[service.key];
  return (
    <>
      <div class="aa-head">
        <Crumbs service={service} />
        <h1>{service.title}</h1>
        <p class="aa-lede">
          {`${service.commands.length} commands, the same as ${viewName} has in a notebook. Each one only reads.`}
        </p>
      </div>
      {Live ? <Live /> : null}
      {[...byGroup(service.commands)].map(([group, commands]) => [
        <h2 key={`${group}-title`} class="aa-group">
          {group}
        </h2>,
        <div key={group} class="aa-cards">
          {commands.map((command) => (
            <a key={command.name} class="aa-card" href={routeFor(service.key, command.name)}>
              <code>{`${command.name}()`}</code>
              <p>{command.about}</p>
            </a>
          ))}
        </div>,
      ])}
    </>
  );
}
