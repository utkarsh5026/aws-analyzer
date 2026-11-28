/** A command's call as it would be written in a notebook, from what its form's boxes hold. */

import type { Command, Parameter } from "../api/types";

const PYTHON_START = /^[[{('"]/;
const PYTHON_WORDS = ["True", "False", "None"];
const YES = ["true", "1", "yes", "on"];
const NUMBER = /^-?[\d,]+(\.\d+)?$/;
const QUOTED = /^['"]/;

function looksLikePython(text: string): boolean {
  return PYTHON_START.test(text) || PYTHON_WORDS.includes(text);
}

/** A box's text as a Python value for that parameter: a flag's True / False, a number, a literal, or a string. */
export function pythonArgument(text: string, parameter: Parameter): string {
  if (parameter.kind === "flag") return YES.includes(text.toLowerCase()) ? "True" : "False";
  const takesNumber = parameter.default !== "" && !QUOTED.test(parameter.default);
  if (takesNumber && NUMBER.test(text)) return text.replace(/,/g, "");
  if (looksLikePython(text)) return text;
  return `'${text.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
}

/**
 * The call the boxes make: the required ones in order while they're all filled, the rest by name, empty boxes left
 * out ('ls(\'s3://acme/raw/\', limit=10)').
 */
export function callText(command: Command, values: Record<string, string>): string {
  const parts: string[] = [];
  let inOrder = true;
  for (const parameter of command.parameters) {
    const text = (values[parameter.name] ?? "").trim();
    if (!text) {
      if (parameter.required) inOrder = false;
      continue;
    }
    const value = pythonArgument(text, parameter);
    const byPosition = parameter.required && parameter.positional && inOrder;
    parts.push(byPosition ? value : `${parameter.name}=${value}`);
  }
  return `${command.name}(${parts.join(", ")})`;
}

const STEP_CALL = /^(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)\(/;

/** The command a report's next step calls ('ui.errors(\'etl\')' -> 'errors'), or '' when it isn't a call. */
export function calledCommand(step: string): string {
  return STEP_CALL.exec(step.trim())?.[1] ?? "";
}
