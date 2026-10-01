/** 実 API 応答ではない、仕様検証用の合成データ。 */
import type { Reader, Writer } from "../src/api.js";
import { CollectionError, PublishError } from "../src/api.js";
import type {
  Assessment,
  ChangedFile,
  JsonObject,
  Observations,
  Policy,
  Review,
} from "../src/contracts.js";
import { integer, object, string } from "../src/contracts.js";
import {
  LABEL_COLORS,
  LABEL_DESCRIPTIONS,
  DECISION_LABELS,
} from "../src/publish.js";
import { assess } from "../src/evaluate.js";
export const HEAD = "a".repeat(40),
  BASE = "b".repeat(40),
  OLD = "c".repeat(40),
  ACTION_SHA = "d".repeat(40);
export const AT = "2026-09-11T12:00:00.000000Z";
export const policy: Policy = { stale_change_review_days: 30 };
export function approval(overrides: Partial<Review> = {}): Review {
  return {
    id: 1,
    author: "maintainer",
    state: "APPROVED",
    commit_sha: HEAD,
    submitted_at: AT,
    author_type: "User",
    author_association: "COLLABORATOR",
    ...overrides,
  };
}
export function file(
  path = "whatever.rs",
  changeType = "MODIFIED",
): ChangedFile {
  return { path, changeType, additions: 3, deletions: 1 };
}
export function facts(lastChanged = "2026-09-01T12:00:00Z"): Observations {
  return {
    schema_version: 3,
    observed_at: AT,
    repository: "example/project",
    collection_errors: [],
    stable: true,
    pr: {
      number: 1,
      state: "OPEN",
      head_sha: HEAD,
      base_sha: BASE,
      base_ref: "trunk",
    },
    change: {
      additions: 3,
      deletions: 1,
      changed_files: 1,
      types: { MODIFIED: 1 },
    },
    files: [file()],
    change_history: {
      base_sha: BASE,
      files: [
        {
          path: "whatever.rs",
          history_path: "whatever.rs",
          last_commit_sha: OLD,
          last_changed_at: lastChanged,
        },
      ],
    },
    reviews: [],
  };
}
export function report(
  decision: Assessment["decision"] = "HUMAN_REVIEW_REQUIRED",
): Assessment {
  return { ...assess(facts(), policy), decision };
}
export function prEvent(action = "opened"): JsonObject {
  return {
    action,
    pull_request: {
      number: 1,
      head: { sha: HEAD, repo: { id: 1 } },
      base: { sha: BASE, repo: { id: 1 } },
      user: { login: "contributor" },
    },
  };
}
export function pull(names: string[] = [], state = "open"): JsonObject {
  return {
    state,
    head: { sha: HEAD, repo: { id: 1 } },
    base: { sha: BASE, ref: "trunk", repo: { id: 1 } },
    user: { login: "contributor" },
    labels: names.map((name) => ({ name })),
  };
}
export class FixtureReader implements Reader {
  repository = "example/project";
  prefix = "/repos/example/project";
  reads = 0;
  drift: JsonObject = {};
  paths: string[] = [];
  requests: string[] = [];
  failure = "";
  state: JsonObject = {
    number: 1,
    state: "OPEN",
    headRefOid: HEAD,
    baseRefOid: BASE,
    baseRefName: "trunk",
    updatedAt: AT,
    additions: 3,
    deletions: 1,
    changedFiles: 1,
  };
  files: JsonObject[] = [
    {
      filename: "whatever.rs",
      status: "modified",
      additions: 3,
      deletions: 1,
      patch: "DO NOT SAVE PATCH",
    },
  ];
  reviews: JsonObject[] = [
    {
      id: 1,
      user: { login: "maintainer", type: "User" },
      author_association: "OWNER",
      state: "APPROVED",
      commit_id: HEAD,
      submitted_at: AT,
      body: "DO NOT SAVE REVIEW",
    },
  ];
  lastChanged = "2026-09-01T12:00:00Z";
  commits: unknown;
  openNumbers = [1];
  async request(path: string): Promise<unknown> {
    this.requests.push(path);
    if (path.includes("/commits?"))
      return (
        this.commits ?? [
          {
            sha: OLD,
            commit: {
              committer: { date: this.lastChanged },
              message: "DO NOT SAVE MESSAGE",
            },
          },
        ]
      );
    throw new Error(`unexpected request ${path}`);
  }
  async graphql(number: number): Promise<JsonObject> {
    this.reads++;
    if (this.reads % 2 === 0) Object.assign(this.state, this.drift);
    return structuredClone({ ...this.state, number });
  }
  async pages(path: string): Promise<JsonObject[]> {
    if (this.failure) throw new CollectionError(this.failure);
    this.paths.push(path);
    if (path.endsWith("/files")) return structuredClone(this.files);
    if (path.endsWith("/reviews")) return structuredClone(this.reviews);
    if (path === "/pulls?state=open")
      return this.openNumbers.map((number) => ({ number }));
    throw new Error(`unexpected page ${path}`);
  }
}
export class FixtureWriter implements Writer {
  repository = "example/project";
  prefix = "/repos/example/project";
  pulls = new Map<number, JsonObject>([[1, pull()]]);
  definitions = new Map<string, JsonObject>(
    Object.values(DECISION_LABELS).map((name) => [
      name,
      {
        name,
        color: LABEL_COLORS[name],
        description: LABEL_DESCRIPTIONS[name],
      },
    ]),
  );
  calls: [string, string, JsonObject | undefined][] = [];
  closed: JsonObject[] = [];
  fail?: (verb: string, path: string) => boolean;
  async request(
    path: string,
    body?: JsonObject,
    method?: string,
  ): Promise<unknown> {
    const verb = method ?? (body ? "POST" : "GET");
    this.calls.push([verb, path, body]);
    if (this.fail?.(verb, path)) throw new PublishError("API HTTP 403");
    const parts = path.slice(this.prefix.length).split("/").filter(Boolean);
    if (parts[0] === "pulls")
      return structuredClone(this.pulls.get(Number(parts[1])));
    if (parts[0] === "labels") {
      const name = parts[1]
        ? decodeURIComponent(parts[1])
        : string(body?.name, "label");
      if (verb === "GET") {
        if (!this.definitions.has(name)) throw new PublishError("API HTTP 404");
        return structuredClone(this.definitions.get(name));
      }
      this.definitions.set(name, { ...this.definitions.get(name), ...body });
      return structuredClone(this.definitions.get(name));
    }
    if (parts[0] === "issues") {
      const pr = this.pulls.get(integer(Number(parts[1]), "number"))!;
      const labels = (pr.labels as JsonObject[]).map((item) =>
        string(item.name, "label"),
      );
      if (verb === "POST") labels.push(...(body?.labels as string[]));
      if (verb === "DELETE") {
        const index = labels.indexOf(decodeURIComponent(parts[3]!));
        if (index >= 0) labels.splice(index, 1);
        else throw new PublishError("API HTTP 404");
      }
      pr.labels = [...new Set(labels)].map((name) => ({ name }));
      return null;
    }
    throw new Error(`unexpected ${verb} ${path}`);
  }
  async pages(path: string): Promise<JsonObject[]> {
    this.calls.push(["GET", path, undefined]);
    return structuredClone(this.closed);
  }
  names(number = 1): string[] {
    return (object(this.pulls.get(number)).labels as JsonObject[]).map((item) =>
      String(item.name),
    );
  }
}
