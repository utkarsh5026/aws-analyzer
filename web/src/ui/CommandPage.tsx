/** A command: its form, the call the form makes (which can be edited instead), and its report. */

import { useEffect, useState } from "preact/hooks";
import type { Command, Parameter, Service } from "../api/types";
import { callText } from "../lib/call";
import { type Route, routeFor } from "../lib/route";
import { Crumbs } from "./bits";
import { Result } from "./Result";
import { useRuns } from "./useRuns";

type Texts = Record<string, string>;
type Flags = Record<string, boolean>;

function isOnByDefault(parameter: Parameter): boolean {
  return parameter.default === "True";
}

function startingFlags(command: Command, args: Texts): Flags {
  const flags: Flags = {};
  for (const parameter of command.parameters) {
    if (parameter.kind !== "flag") continue;
    const given = args[parameter.name];
    flags[parameter.name] = given ? given === "true" : isOnByDefault(parameter);
  }
  return flags;
}

/** The form's values as texts: a flag left as its default is '' (so it stays out of the call and the address). */
function formValues(command: Command, texts: Texts, flags: Flags): Texts {
  const values: Texts = {};
  for (const parameter of command.parameters) {
    if (parameter.kind !== "flag") {
      values[parameter.name] = texts[parameter.name] ?? "";
      continue;
    }
    const checked = flags[parameter.name] ?? false;
    values[parameter.name] = checked === isOnByDefault(parameter) ? "" : String(checked);
  }
  return values;
}

interface FieldProps {
  parameter: Parameter;
  text: string;
  checked: boolean;
  onText: (text: string) => void;
  onFlag: (checked: boolean) => void;
}

function Field({ parameter, text, checked, onText, onFlag }: FieldProps) {
  if (parameter.kind === "flag") {
    return (
      <div class="aa-field aa-flag">
        <label>
          <input
            type="checkbox"
            name={parameter.name}
            checked={checked}
            onInput={(event) => onFlag(event.currentTarget.checked)}
          />
          <span>{parameter.name}</span>
        </label>
      </div>
    );
  }
  return (
    <div class="aa-field">
      <label for={`f-${parameter.name}`}>
        {parameter.required ? <b>{parameter.name}</b> : parameter.name}
        {parameter.required ? " (needed)" : ""}
      </label>
      <input
        type="text"
        id={`f-${parameter.name}`}
        name={parameter.name}
        value={text}
        placeholder={parameter.required ? "" : parameter.default}
        autocomplete="off"
        spellcheck={false}
        onInput={(event) => onText(event.currentTarget.value)}
      />
    </div>
  );
}

function Description({ command }: { command: Command }) {
  if (!command.doc.includes("\n\n")) return null;
  return (
    <details class="aa-doc">
      <summary>The whole description</summary>
      <pre>{command.doc}</pre>
    </details>
  );
}

interface PageProps {
  service: Service;
  command: Command;
  route: Route;
}

export function CommandPage({ service, command, route }: PageProps) {
  const [texts, setTexts] = useState<Texts>(() => ({ ...route.args }));
  const [flags, setFlags] = useState<Flags>(() => startingFlags(command, route.args));
  const [typedCall, setTypedCall] = useState<string | null>(null);
  const { shown, runForm, runCall } = useRuns(service, command);

  const values = formValues(command, texts, flags);
  const call = typedCall ?? callText(command, values);

  useEffect(() => {
    if (route.args.run) runForm(false);
  }, []);

  const submit = (event: Event) => {
    event.preventDefault();
    if (typedCall !== null) {
      runCall(typedCall);
      return;
    }
    const target = routeFor(service.key, command.name, { ...values, run: "1" });
    if (target === location.hash) runForm(true);
    else location.hash = target;
  };

  return (
    <>
      <div class="aa-head">
        <Crumbs service={service} group={command.group} />
        <h1>
          <code>{`${command.name}${command.signature}`}</code>
        </h1>
        <p class="aa-lede">{command.about}</p>
        <Description command={command} />
      </div>
      <form class="aa-form" autocomplete="off" onSubmit={submit}>
        {command.parameters.length ? (
          <div class="aa-fields">
            {command.parameters.map((parameter) => (
              <Field
                key={parameter.name}
                parameter={parameter}
                text={texts[parameter.name] ?? ""}
                checked={flags[parameter.name] ?? false}
                onText={(text) => setTexts({ ...texts, [parameter.name]: text })}
                onFlag={(checked) => setFlags({ ...flags, [parameter.name]: checked })}
              />
            ))}
          </div>
        ) : null}
        <div class="aa-actions">
          <button class="aa-run" type="submit">
            ▶ Run
          </button>
          <div class="aa-callbox">
            <input
              type="text"
              aria-label="The call, as you'd write it in a notebook"
              spellcheck={false}
              autocomplete="off"
              value={call}
              onInput={(event) => setTypedCall(event.currentTarget.value)}
            />
            <button class="aa-ghost" type="button" onClick={() => navigator.clipboard?.writeText(`ui.${call}`)}>
              Copy
            </button>
          </div>
        </div>
      </form>
      <Result shown={shown} service={service} onAgain={() => runForm(true)} onStep={runCall} />
    </>
  );
}
