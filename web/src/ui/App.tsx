/**
 * The page: the bar with its tabs, the page the address names (the search home, or a service's commands down the side
 * beside the page), and the finder over it all.
 */

import { useEffect } from "preact/hooks";
import type { Catalog } from "../api/types";
import { catalog, currentRoute, finderOpen, hash, loadError } from "../state";
import { Bar } from "./Bar";
import { CommandPage } from "./CommandPage";
import { Finder } from "./Finder";
import { Home } from "./Home";
import { ServicePage } from "./ServicePage";
import { Side } from "./Side";

const TITLE = "aws-analyzer";

function Page({ loaded }: { loaded: Catalog }) {
  const route = currentRoute();
  const service = loaded.services.find((each) => each.key === route.service);
  const command = service?.commands.find((each) => each.name === route.command);

  useEffect(() => {
    if (command && service) document.title = `${command.name} · ${service.title} · ${TITLE}`;
    else document.title = service ? `${service.title} · ${TITLE}` : TITLE;
  }, [service, command]);

  if (service && command) return <CommandPage key={hash.value} service={service} command={command} route={route} />;
  if (service) return <ServicePage service={service} />;
  return <Home loaded={loaded} />;
}

function Main() {
  const loaded = catalog.value;
  return (
    <main class="aa-main" id="main" tabindex={-1}>
      {loaded ? (
        <Page loaded={loaded} />
      ) : (
        <p class="aa-loading">
          {loadError.value ? `Couldn't load the services: ${loadError.value}` : "Loading the services…"}
        </p>
      )}
    </main>
  );
}

/** Whether the address names a service the catalog has: its commands go down the side. */
function isServicePage(): boolean {
  const key = currentRoute().service;
  return Boolean(key) && Boolean(catalog.value?.services.some((service) => service.key === key));
}

function isFinderKey(event: KeyboardEvent): boolean {
  return (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k";
}

export function App() {
  useEffect(() => {
    const keys = (event: KeyboardEvent) => {
      if (!isFinderKey(event) || !catalog.value) return;
      event.preventDefault();
      finderOpen.value = true;
    };
    document.addEventListener("keydown", keys);
    return () => document.removeEventListener("keydown", keys);
  }, []);

  return (
    <>
      <Bar />
      <div class={`aa-shell${isServicePage() ? "" : " aa-wide"}`}>
        {isServicePage() ? <Side /> : null}
        <Main />
      </div>
      <Finder />
    </>
  );
}
