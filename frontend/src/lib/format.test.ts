import { describe, expect, it } from "vitest";
import { closedLabel, dateTime, resolvedLabel } from "./format";

describe("closedLabel", () => {
  const closedAt = "2026-09-15T17:42:00Z";

  it("names who closed the conversation and when", () => {
    const label = closedLabel({ closed_at: closedAt, closed_by: { id: "u2", name: "Sam (Support)" } }, "u1");
    expect(label).toBe(`Closed by Sam (Support) on ${dateTime(closedAt)}`);
  });

  it("says 'you' when the viewer closed it", () => {
    expect(closedLabel({ closed_at: closedAt, closed_by: { id: "u1", name: "Casey Customer" } }, "u1")).toBe(
      `Closed by you on ${dateTime(closedAt)}`,
    );
  });

  it("still shows the time when the closer is unknown", () => {
    expect(closedLabel({ closed_at: closedAt, closed_by: null })).toBe(`Closed on ${dateTime(closedAt)}`);
  });

  it("labels the automatic close of an unanswered resolved chat", () => {
    expect(closedLabel({ closed_at: closedAt, closed_by: null, closed_automatically: true })).toBe(
      `Closed automatically on ${dateTime(closedAt)}`,
    );
  });
});

describe("resolvedLabel", () => {
  it("names who resolved the conversation and when", () => {
    const at = "2026-09-15T18:05:00Z";
    expect(resolvedLabel({ resolved_at: at, resolved_by: { id: "a1", name: "Sam (Support)" } }, "c1")).toBe(
      `Resolved by Sam (Support) on ${dateTime(at)}`,
    );
  });
});
