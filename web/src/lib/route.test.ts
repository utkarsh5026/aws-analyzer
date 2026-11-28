import { describe, expect, it } from "vitest";
import { parseRoute, routeFor } from "./route";

describe("the address", () => {
  it("names the service, the command and the form's texts", () => {
    expect(parseRoute("#/lambda_functions/errors?name=orders-etl&since=7d&run=1")).toEqual({
      service: "lambda_functions",
      command: "errors",
      args: { name: "orders-etl", since: "7d", run: "1" },
    });
  });

  it("is the home page when it's empty, and a service's page without a command", () => {
    expect(parseRoute("")).toEqual({ service: "", command: "", args: {} });
    expect(parseRoute("#/s3")).toEqual({ service: "s3", command: "", args: {} });
  });

  it("leaves empty boxes out and comes back as it went in", () => {
    const hash = routeFor("s3", "ls", { uri: "s3://acme lake/raw/", limit: "", run: "1" });
    expect(hash).toBe("#/s3/ls?uri=s3%3A%2F%2Facme+lake%2Fraw%2F&run=1");
    expect(parseRoute(hash).args).toEqual({ uri: "s3://acme lake/raw/", run: "1" });
  });
});
