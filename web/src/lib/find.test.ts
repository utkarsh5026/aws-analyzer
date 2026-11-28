import { describe, expect, it } from "vitest";
import type { Command, Service } from "../api/types";
import { byGroup, findCommands } from "./find";

function command(name: string, about: string, group = "Look"): Command {
  return { name, about, group, signature: "()", doc: about, parameters: [] };
}

const lambda: Service = {
  key: "lambda_functions",
  title: "Lambda",
  icon: "lambda",
  data: true,
  searchable: true,
  checkable: true,
  commands: [
    command("errors", "Why a function failed: each cause, when it started", "When something goes wrong"),
    command("logs", "A function's log lines, newest first, errors marked"),
    command("functions", "Every function in the region"),
  ],
};
const s3: Service = {
  key: "s3",
  title: "S3",
  icon: "s3",
  data: true,
  searchable: true,
  checkable: true,
  commands: [command("largest", "The largest files under a prefix"), command("ls", "Folders and files")],
};

describe("finding a command", () => {
  it("lists those that hold every word, the ones that name it first", () => {
    const found = findCommands([lambda, s3], "errors");
    expect(found.map((hit) => hit.command.name)).toEqual(["errors", "logs"]);
  });

  it("matches a service's name too", () => {
    expect(findCommands([lambda, s3], "s3 files").map((hit) => hit.command.name)).toEqual(["ls", "largest"]);
  });

  it("lists every command for an empty query, at most forty", () => {
    expect(findCommands([lambda, s3], "  ")).toHaveLength(5);
    const many: Service = { ...s3, commands: Array.from({ length: 60 }, (_, i) => command(`c${i}`, "")) };
    expect(findCommands([many], "")).toHaveLength(40);
  });
});

describe("a service's commands by group", () => {
  it("keeps the groups in the order they first come", () => {
    const groups = byGroup([...lambda.commands, command("tail", "", "When something goes wrong")]);
    expect([...groups.keys()]).toEqual(["When something goes wrong", "Look"]);
    expect(groups.get("When something goes wrong")?.map((each) => each.name)).toEqual(["errors", "tail"]);
  });
});
