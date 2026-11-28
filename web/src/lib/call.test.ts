import { describe, expect, it } from "vitest";
import type { Command, Parameter } from "../api/types";
import { calledCommand, callText, pythonArgument } from "./call";

function parameter(name: string, extra: Partial<Parameter> = {}): Parameter {
  return { name, default: "", required: true, kind: "text", positional: true, ...extra };
}

const ls: Command = {
  name: "ls",
  signature: "(uri, *, limit=500, details=False)",
  about: "",
  doc: "",
  group: "",
  parameters: [
    parameter("uri"),
    parameter("limit", { default: "500", required: false, positional: false }),
    parameter("details", { default: "False", required: false, kind: "flag", positional: false }),
  ],
};

describe("a box's text as Python", () => {
  it("quotes text, and escapes its quotes and backslashes", () => {
    expect(pythonArgument("it's", parameter("name"))).toBe("'it\\'s'");
    expect(pythonArgument("a\\b", parameter("name"))).toBe("'a\\\\b'");
  });

  it("keeps numbers where the default is one, and literals anywhere", () => {
    const limit = parameter("limit", { default: "500", required: false });
    expect(pythonArgument("1,000", limit)).toBe("1000");
    expect(pythonArgument("1000", parameter("since", { default: "'24h'" }))).toBe("'1000'");
    expect(pythonArgument("['a', 'b']", parameter("names"))).toBe("['a', 'b']");
    expect(pythonArgument("None", parameter("region"))).toBe("None");
  });

  it("reads a flag's yes and no", () => {
    const flag = parameter("details", { kind: "flag" });
    expect(pythonArgument("true", flag)).toBe("True");
    expect(pythonArgument("off", flag)).toBe("False");
  });
});

describe("the call the form makes", () => {
  it("gives the required boxes in order and the rest by name", () => {
    expect(callText(ls, { uri: "s3://acme/raw/", limit: "10", details: "true" })).toBe(
      "ls('s3://acme/raw/', limit=10, details=True)",
    );
  });

  it("leaves empty boxes out, and names what follows a required one left empty", () => {
    expect(callText(ls, {})).toBe("ls()");
    expect(callText(ls, { limit: " 3 " })).toBe("ls(limit=3)");
  });

  it("names the command a report's next step calls", () => {
    expect(calledCommand("ui.errors('orders-etl', since='7d')")).toBe("errors");
    expect(calledCommand(" summary('s3://acme/')")).toBe("summary");
    expect(calledCommand("aws s3 ls s3://acme/")).toBe("");
  });
});
