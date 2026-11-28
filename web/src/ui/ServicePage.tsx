/**
 * A service's page: what needs attention in it (checked when the page opens, only this service read), then its
 * commands by group, each a card that opens its form.
 */

import type { Service } from "../api/types";
import { byGroup } from "../lib/find";
import { routeFor } from "../lib/route";
import { NeedsAttention } from "./Attention";
import { Crumbs } from "./bits";

export function ServicePage({ service }: { service: Service }) {
  const viewName = service.title === "S3" ? "S3View" : "its View";
  return (
    <>
      <div class="aa-head">
        <Crumbs service={service} />
        <h1>{service.title}</h1>
        <p class="aa-lede">
          {`${service.commands.length} commands, the same as ${viewName} has in a notebook. Each one only reads.`}
        </p>
      </div>
      <NeedsAttention service={service} />
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
