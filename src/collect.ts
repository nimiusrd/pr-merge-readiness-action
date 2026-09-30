/** PR のソース・patch を保存せず、正規化したメタデータだけを観測する。 */
import { isDeepStrictEqual } from "node:util";
import { CollectionError, ProposalHeadChanged, nullableString } from "./api.js";
import type { Reader } from "./api.js";
import {
  errorMessage,
  fileHistoryPath,
  integer,
  object,
  sha,
  string,
  timestamp,
} from "./contracts.js";
import type {
  ChangedFile,
  ChangeHistory,
  JsonObject,
  ObservationChange,
  Observations,
  Policy,
  PullRequest,
  Review,
} from "./contracts.js";
import { changeAges } from "./evaluate.js";
export const PR_FIELDS =
  "number state headRefOid baseRefOid baseRefName updatedAt additions deletions changedFiles";
export async function readPr(
  api: Reader,
  number: number,
): Promise<PullRequest> {
  const raw = await api.graphql(number, PR_FIELDS);
  return {
    number: integer(raw.number, "number"),
    state: string(raw.state, "state"),
    head_sha: sha(raw.headRefOid),
    base_sha: sha(raw.baseRefOid),
    base_ref: string(raw.baseRefName, "baseRefName"),
    updated_at: string(raw.updatedAt, "updatedAt"),
    additions: integer(raw.additions, "additions"),
    deletions: integer(raw.deletions, "deletions"),
    changed_files: integer(raw.changedFiles, "changedFiles"),
  };
}
export async function decisionMetadata(
  api: Reader,
  number: number,
): Promise<Review[]> {
  const reviews = (await api.pages(`/pulls/${number}/reviews`)).map((r) => {
    const user = r.user == null ? {} : object(r.user);
    return {
      id: integer(r.id, "review.id"),
      author: nullableString(user.login),
      state: string(r.state, "review.state"),
      commit_sha: nullableString(r.commit_id),
      submitted_at: nullableString(r.submitted_at),
      author_type: nullableString(user.type),
      author_association: nullableString(r.author_association),
    };
  });
  return reviews.sort(
    (a, b) => a.id - b.id || JSON.stringify(a).localeCompare(JSON.stringify(b)),
  );
}
export function observationChanges(
  before: { pr: PullRequest; reviews: Review[] },
  after: { pr: PullRequest; reviews: Review[] },
): ObservationChange[] {
  const changes: ObservationChange[] = [];
  const compare = (
    group: string,
    old: object,
    next: object,
    identity: JsonObject | null = null,
  ) => {
    const a = old as JsonObject,
      b = next as JsonObject;
    for (const field of [
      ...new Set([...Object.keys(a), ...Object.keys(b)]),
    ].sort()) {
      if (!isDeepStrictEqual(a[field] ?? null, b[field] ?? null))
        changes.push({
          group,
          identity,
          field,
          before: a[field] ?? null,
          after: b[field] ?? null,
        });
    }
  };
  compare("pr", before.pr, after.pr);
  const old = new Map(before.reviews.map((r) => [r.id, r])),
    next = new Map(after.reviews.map((r) => [r.id, r]));
  for (const id of [...new Set([...old.keys(), ...next.keys()])].sort(
    (a, b) => a - b,
  )) {
    const a = old.get(id),
      b = next.get(id);
    if (!a || !b)
      changes.push({
        group: "reviews",
        identity: { id },
        field: "record",
        before: a ?? null,
        after: b ?? null,
      });
    else compare("reviews", a, b, { id, author: a.author });
  }
  return changes;
}
export class ChangeHistoryCollector {
  requests = 0;
  private readonly cache = new Map<string, readonly [string, string]>();
  constructor(
    private readonly api: Reader,
    private readonly maxFiles = 100,
    private readonly maxRequests = 100,
  ) {}
  async collect(baseSha: string, files: ChangedFile[]): Promise<ChangeHistory> {
    const base = sha(baseSha);
    const paths = files.map(
      (file) => [file.path, fileHistoryPath(file)] as const,
    );
    if (
      paths.filter(([, previous]) => previous !== null).length > this.maxFiles
    )
      throw new CollectionError("change history file limit exceeded");
    const keyFor = (previous: string) => JSON.stringify([base, previous]);
    const missing = new Set(
      paths.flatMap(([, previous]) =>
        previous !== null && !this.cache.has(keyFor(previous))
          ? [keyFor(previous)]
          : [],
      ),
    );
    if (this.requests + missing.size > this.maxRequests)
      throw new CollectionError(
        `change history run request limit exceeded: ${this.requests}/${this.maxRequests} used, ${missing.size} needed`,
      );
    const history: ChangeHistory = { base_sha: base, files: [] };
    for (const [path, previous] of paths) {
      const record = {
        path,
        history_path: previous,
        last_commit_sha: null as string | null,
        last_changed_at: null as string | null,
      };
      if (previous !== null) {
        const key = keyFor(previous);
        if (!this.cache.has(key)) {
          this.requests++;
          const query = new URLSearchParams({
            sha: base,
            path: previous,
            per_page: "1",
          });
          const commits = await this.api.request(
            `${this.api.prefix}/commits?${query}`,
          );
          if (!Array.isArray(commits) || commits.length !== 1)
            throw new CollectionError(`last change unavailable: ${previous}`);
          const last = object(commits[0]);
          const at = string(
            object(object(last.commit).committer).date,
            "last_changed_at",
          );
          timestamp(at, "last_changed_at");
          this.cache.set(key, [sha(last.sha), at]);
        }
        [record.last_commit_sha, record.last_changed_at] = this.cache.get(key)!;
      }
      history.files.push(record);
    }
    return history;
  }
}
export interface CollectOptions {
  policy: Policy;
  historyCollector?: ChangeHistoryCollector;
  expectedHead?: string;
  now?: () => string;
}
export async function collect(
  api: Reader,
  number: number,
  options: CollectOptions,
): Promise<Observations> {
  const now = options.now ?? (() => new Date().toISOString());
  const facts: Observations = {
    schema_version: 3,
    observed_at: now(),
    repository: api.repository,
    collection_errors: [],
    stable: false,
    observation_changes: null,
  };
  try {
    const before = await readPr(api, number);
    if (
      options.expectedHead !== undefined &&
      before.head_sha !== options.expectedHead
    )
      throw new ProposalHeadChanged(
        "PR head differs from validated proposal before observation",
      );
    facts.pr = before;
    const files = (await api.pages(`/pulls/${number}/files`)).map((raw) => ({
      path: string(raw.filename, "filename"),
      previous_path:
        raw.status === "renamed"
          ? string(raw.previous_filename, "previous_filename")
          : null,
      changeType:
        raw.status === "removed"
          ? "DELETED"
          : string(raw.status, "file.status").toUpperCase(),
      additions: integer(raw.additions, "additions"),
      deletions: integer(raw.deletions, "deletions"),
    }));
    if (new Set(files.map((f) => f.path)).size !== files.length)
      throw new CollectionError("duplicate file metadata");
    const types: Record<string, number> = Object.create(null) as Record<
      string,
      number
    >;
    for (const file of files)
      types[file.changeType] = (types[file.changeType] ?? 0) + 1;
    facts.change = {
      changed_files: files.length,
      additions: files.reduce((sum, f) => sum + f.additions, 0),
      deletions: files.reduce((sum, f) => sum + f.deletions, 0),
      types,
      binary_files: null,
      mode_changes: null,
    };
    facts.files = files;
    if (
      (["changed_files", "additions", "deletions"] as const).some(
        (key) => facts.change![key] !== before[key],
      )
    )
      throw new CollectionError("PR file totals mismatch");
    facts.change_history = await (
      options.historyCollector ?? new ChangeHistoryCollector(api)
    ).collect(before.base_sha, files);
    facts.observed_at = now();
    const needsReview = changeAges(
      facts,
      options.policy.stale_change_review_days,
    ).some((f) => f.status === "stale");
    const initial = needsReview ? await decisionMetadata(api, number) : [];
    facts.reviews = initial;
    const confirmed = needsReview ? await decisionMetadata(api, number) : [];
    const after = await readPr(api, number);
    if (
      options.expectedHead !== undefined &&
      after.head_sha !== options.expectedHead
    )
      throw new ProposalHeadChanged(
        "PR head changed after proposal validation during observation",
      );
    const fields = [
      "number",
      "head_sha",
      "base_sha",
      "base_ref",
      "additions",
      "deletions",
      "changed_files",
    ] as const;
    facts.rechecked = {
      pr: fields.every((key) => before[key] === after[key]),
      reviews: isDeepStrictEqual(initial, confirmed),
    };
    facts.stable = Object.values(facts.rechecked).every(Boolean);
    facts.observation_changes = observationChanges(
      { pr: before, reviews: initial },
      { pr: after, reviews: confirmed },
    );
    facts.observed_at = now();
    if (
      !needsReview &&
      changeAges(facts, options.policy.stale_change_review_days).some(
        (f) => f.status === "stale",
      )
    )
      throw new CollectionError(
        "history review threshold crossed during observation",
      );
  } catch (error) {
    if (error instanceof ProposalHeadChanged) throw error;
    facts.collection_errors.push(errorMessage(error));
  }
  return facts;
}
export async function targets(
  api: Reader,
  event: JsonObject,
  requested?: number,
): Promise<number[]> {
  if (requested !== undefined) {
    if (integer(requested, "PR number") === 0)
      throw new CollectionError("PR number must be positive");
    return [requested];
  }
  if (Object.hasOwn(event, "pull_request"))
    return [integer(object(event.pull_request).number, "PR number")];
  return (await api.pages("/pulls?state=open")).map((p) =>
    integer(p.number, "PR number"),
  );
}
