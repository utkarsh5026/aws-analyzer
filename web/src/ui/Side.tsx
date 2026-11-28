/** The services down the side, and the commands of the one that's open, by group. */

import type { Service } from "../api/types";
import { byGroup } from "../lib/find";
import { routeFor } from "../lib/route";
import { catalog, currentRoute } from "../state";
import { ServiceIcon } from "./bits";

function CommandList({ service, current }: { service: Service; current: string }) {
  return (
    <div class="aa-cmds">
      {[...byGroup(service.commands)].map(([group, commands]) => [
        <h4 key={group}>{group}</h4>,
        ...commands.map((command) => (
          <a
            key={command.name}
            class={`aa-cmd${command.name === current ? " aa-on" : ""}`}
            href={routeFor(service.key, command.name)}
            title={command.about}
          >
            {command.name}
          </a>
        )),
      ])}
    </div>
  );
}

export function Side() {
  const route = currentRoute();
  const services = catalog.value?.services ?? [];
  return (
    <nav class="aa-side" id="side" aria-label="Services">
      {services.map((service) => {
        const isOpen = service.key === route.service;
        return [
          <a key={service.key} class={`aa-svc${isOpen ? " aa-on" : ""}`} href={`#/${service.key}`}>
            <ServiceIcon service={service} />
            <span>{service.title}</span>
          </a>,
          isOpen ? <CommandList key={`${service.key}-commands`} service={service} current={route.command} /> : null,
        ];
      })}
    </nav>
  );
}
