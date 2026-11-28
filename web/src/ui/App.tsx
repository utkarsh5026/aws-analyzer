/** The page: the bar, the services down the side, the page the address names, and the finder over it all. */

import { useEffect } from "preact/hooks";
import type { Catalog } from "../api/types";
import { catalog, currentRoute, finderOpen, hash, loadError } from "../state";
import { Bar } from "./Bar";
import { CommandPage } from "./CommandPage";
import { Finder } from "./Finder";
import { Home, ServicePage } from "./Home";
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
  return <Home services={loaded.services} />;
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
      <div class="aa-shell">
        <Side />
        <Main />
      </div>
      <Finder />
    </>
  );
}
