/** The pages that list: every service (the home page), and one service's commands by group. */

import type { Service } from "../api/types";
import { byGroup } from "../lib/find";
import { routeFor } from "../lib/route";
import { Crumbs, ServiceIcon } from "./bits";

const COMMANDS_ON_A_CARD = 5;

export function Home({ services }: { services: Service[] }) {
  return (
    <>
      <div class="aa-head">
        <h1>What do you want to look at?</h1>
        <p class="aa-lede">
          The notebook's reports with room to read them. Pick a service, then a command; or press Ctrl K and type what
          you're after (errors, largest files, cost…).
        </p>
      </div>
      <div class="aa-cards">
        {services.map((service) => (
          <a key={service.key} class="aa-card" href={`#/${service.key}`}>
            <ServiceIcon service={service} />
            <code>{service.title}</code>
            <p>{`${service.commands
              .slice(0, COMMANDS_ON_A_CARD)
              .map((command) => command.name)
              .join(", ")}…`}</p>
          </a>
        ))}
      </div>
    </>
  );
}

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
