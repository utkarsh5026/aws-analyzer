/** Small pieces every part of the page uses. */

import type { Service } from "../api/types";

const ICON_SIZE = 22;

/** A service's AWS architecture icon. */
export function ServiceIcon({ service }: { service: Service }) {
  return <img src={`static/icons/${service.icon}.svg`} alt="" width={ICON_SIZE} height={ICON_SIZE} />;
}

/** The breadcrumbs above a page's title: 'Services › S3 › Explore a folder'. */
export function Crumbs({ service, group }: { service?: Service; group?: string }) {
  if (!service) return null;
  return (
    <p class="aa-crumbs">
      <a href="#/">Services</a>
      {" › "}
      {group ? <a href={`#/${service.key}`}>{service.title}</a> : service.title}
      {group ? " › " : null}
      {group ?? null}
    </p>
  );
}
