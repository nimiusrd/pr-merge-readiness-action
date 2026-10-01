// src/runtime.ts
import { appendFile, readFile } from "node:fs/promises";

// src/contracts.ts
var EvaluationError = class extends Error {
};
function object(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    throw new EvaluationError("table required");
  return value;
}
function integer(value, name) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0)
    throw new EvaluationError(`${name}: non-negative integer required`);
  return value;
}
function string(value, name) {
  if (typeof value !== "string" || !value)
    throw new EvaluationError(`${name}: non-empty string required`);
  return value;
}
function boolean(value, name) {
  if (typeof value !== "boolean")
    throw new EvaluationError(`${name}: boolean required`);
  return value;
}
function sha(value) {
  if (typeof value !== "string" || !/^[0-9a-f]{40}$/.test(value))
    throw new EvaluationError("invalid commit SHA");
  return value;
}
function timestamp(value, name) {
  const text = string(value, name);
  const match = /^(\d{4}-\d{2}-\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$/.exec(
    text
  );
  if (!match)
    throw new EvaluationError(
      `${name}: timezone required or invalid timestamp`
    );
  const [, date, hour, minute, second, fraction, offset] = match;
  const day = /* @__PURE__ */ new Date(`${date}T00:00:00Z`);
  if (!Number.isFinite(day.getTime()) || day.toISOString().slice(0, 10) !== date || Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59 || offset !== "Z" && (Number(offset?.slice(1, 3)) > 23 || Number(offset?.slice(4, 6)) > 59))
    throw new EvaluationError(`${name}: invalid timestamp`);
  const millis = Date.parse(`${date}T${hour}:${minute}:${second}${offset}`);
  if (!Number.isFinite(millis))
    throw new EvaluationError(`${name}: invalid timestamp`);
  return BigInt(millis) * 1000n + BigInt((fraction ?? "").padEnd(6, "0").slice(0, 6));
}
function fileHistoryPath(file) {
  const path = string(file.path, "file.path");
  if (["ADDED", "COPIED"].includes(file.changeType)) return null;
  if (file.changeType === "RENAMED")
    return string(file.previous_path, "file.previous_path");
  if (["MODIFIED", "DELETED", "CHANGED", "UNCHANGED"].includes(file.changeType))
    return path;
  throw new EvaluationError("unknown file changeType");
}
function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

// src/api.ts
var CollectionError = class extends Error {
};
var ProposalHeadChanged = class extends CollectionError {
};
var PublishError = class extends Error {
};
var GitHub = class {
  constructor(repository, options, writing = false) {
    this.repository = repository;
    this.options = options;
    this.writing = writing;
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository) || repository.split("/").some((part) => part === "." || part === ".."))
      throw this.error("invalid repository");
    this.prefix = `/repos/${repository}`;
    [this.owner, this.repo] = repository.split("/");
  }
  repository;
  options;
  writing;
  prefix;
  owner;
  repo;
  error(message) {
    return this.writing ? new PublishError(message) : new CollectionError(message);
  }
  async request(path, body, method) {
    const verb = method ?? (body === void 0 ? "GET" : "POST");
    const url = path === "/graphql" ? this.options.graphqlUrl ?? "https://api.github.com/graphql" : (this.options.apiUrl ?? "https://api.github.com").replace(/\/$/, "") + path;
    const controller = new AbortController();
    const timer = setTimeout(
      () => controller.abort(),
      this.options.timeoutMs ?? 3e4
    );
    let reader;
    try {
      const response = await (this.options.fetch ?? fetch)(url, {
        method: verb,
        redirect: "error",
        signal: controller.signal,
        headers: {
          Accept: "application/vnd.github+json",
          "Content-Type": "application/json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "pr-merge-readiness",
          ...this.options.token ? { Authorization: `Bearer ${this.options.token}` } : {}
        },
        ...body === void 0 ? {} : { body: JSON.stringify(body) }
      });
      if (!response.ok) {
        await response.body?.cancel();
        throw this.error(
          `API ${this.writing ? verb + " " : ""}${path}: HTTP ${response.status}`
        );
      }
      reader = response.body?.getReader();
      const chunks = [];
      let bytes = 0;
      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          bytes += value.byteLength;
          if (bytes > (this.options.maxResponseBytes ?? 8 * 1024 * 1024))
            throw this.error("API response exceeds limit");
          chunks.push(value);
        }
      }
      if (this.writing && bytes === 0) return null;
      const result = JSON.parse(
        Buffer.concat(chunks).toString("utf8")
      );
      if (!this.writing && result !== null && typeof result === "object" && !Array.isArray(result) && object(result).errors && (!Array.isArray(object(result).errors) || object(result).errors.length))
        throw this.error("GraphQL returned errors");
      return result;
    } catch (error) {
      if (error instanceof CollectionError || error instanceof PublishError)
        throw error;
      throw this.error(
        `API ${this.writing ? verb + " " : ""}${path}: ${error instanceof Error ? error.name : "transport error"}`
      );
    } finally {
      clearTimeout(timer);
      controller.abort();
      await reader?.cancel().catch(() => void 0);
    }
  }
  async pages(path, key) {
    const records = [];
    const separator = path.includes("?") ? "&" : "?";
    for (let page = 1; page <= (this.options.maxPages ?? 30); page++) {
      const response = await this.request(
        `${this.prefix}${path}${separator}per_page=100&page=${page}`
      );
      const batch = key ? object(response)[key] : response;
      if (!Array.isArray(batch)) throw this.error("invalid REST page");
      records.push(...batch.map(object));
      if (batch.length < 100) {
        if (key && object(response).total_count !== records.length)
          throw this.error("REST collection count mismatch");
        return records;
      }
    }
    throw this.error("REST pagination limit exceeded");
  }
  async graphql(number, selection) {
    const query = `query($owner: String!, $repo: String!, $number: Int!) { repository(owner: $owner, name: $repo) { pullRequest(number: $number) { ${selection} } } }`;
    const result = object(
      await this.request("/graphql", {
        query,
        variables: { owner: this.owner, repo: this.repo, number }
      })
    );
    return object(object(object(result.data).repository).pullRequest);
  }
};
function nullableString(value) {
  return value == null ? null : string(value, "metadata");
}

// src/inputs.ts
var ACTION_REPOSITORY = "nimiusrd/pr-merge-readiness-action";
function positive(value) {
  if (typeof value !== "string" || !/^[1-9][0-9]*$/.test(value))
    throw new EvaluationError("positive decimal integer required");
  return integer(Number(value), "number");
}
function policyFromInputs(env = process.env) {
  const value = env["INPUT_STALE-CHANGE-REVIEW-DAYS"];
  try {
    return {
      stale_change_review_days: positive(
        value === void 0 ? "30" : value.trim()
      )
    };
  } catch {
    throw new EvaluationError(
      "stale-change-review-days: positive decimal safe integer required"
    );
  }
}

// src/collect.ts
import { isDeepStrictEqual } from "node:util";

// src/evaluate.ts
function validatePolicy(value) {
  const policy = object(value);
  if (Object.keys(policy).length !== 1 || !Object.hasOwn(policy, "stale_change_review_days"))
    throw new EvaluationError("invalid policy keys");
  const days = integer(
    policy.stale_change_review_days,
    "stale_change_review_days"
  );
  if (days === 0)
    throw new EvaluationError("stale_change_review_days must be positive");
  return { stale_change_review_days: days };
}
function changeAges(facts, days) {
  const observed = timestamp(facts.observed_at, "observed_at");
  const history = facts.change_history;
  if (!history || sha(history.base_sha) !== sha(facts.pr?.base_sha))
    throw new EvaluationError("change_history base SHA mismatch");
  if (!Array.isArray(facts.files) || !Array.isArray(history.files))
    throw new EvaluationError("change_history and files must be lists");
  const expected = new Map(
    facts.files.map((file) => [
      string(file.path, "file.path"),
      fileHistoryPath(file)
    ])
  );
  const indexed = new Map(
    history.files.map((record) => [
      string(record.path, "history.path"),
      record
    ])
  );
  if (expected.size !== facts.files.length || indexed.size !== history.files.length || expected.size !== indexed.size || [...expected.keys()].some((path) => !indexed.has(path)) || facts.files.length !== integer(facts.change?.changed_files, "changed_files"))
    throw new EvaluationError("change_history file set mismatch");
  return [...expected.entries()].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([path, previous]) => {
    const record = indexed.get(path);
    if (record.history_path !== previous)
      throw new EvaluationError("change_history path mismatch");
    if (previous === null) {
      if (record.last_commit_sha !== null || record.last_changed_at !== null)
        throw new EvaluationError("new file must have null prior history");
      return { ...record, age_seconds: null, status: "new" };
    }
    sha(record.last_commit_sha);
    const age = observed - timestamp(record.last_changed_at, "last_changed_at");
    if (age < 0n)
      throw new EvaluationError("last_changed_at is after observed_at");
    return {
      ...record,
      age_seconds: Number(age) / 1e6,
      status: age > BigInt(days) * 86400000000n ? "stale" : "within_threshold"
    };
  });
}
function conditionsDecision(conditions) {
  return conditions.some((c) => c.status === "unknown") ? "INSUFFICIENT_DATA" : conditions.some((c) => c.status === "blocked") ? "HUMAN_REVIEW_REQUIRED" : "SHADOW_CONDITIONS_MET";
}
function assess(facts, policy) {
  validatePolicy(policy);
  const result = {
    decision: "INSUFFICIENT_DATA",
    conditions: [],
    observations: facts,
    policy
  };
  const condition = (name, status, detail) => result.conditions.push({ name, status, detail });
  try {
    if (facts.schema_version !== 3)
      throw new EvaluationError("unsupported facts schema");
    string(facts.observed_at, "observed_at");
    if (!Array.isArray(facts.collection_errors))
      throw new EvaluationError("collection_errors required");
    if (facts.collection_errors.length)
      throw new EvaluationError(
        "collection incomplete: " + facts.collection_errors.join(", ")
      );
    const head = sha(facts.pr?.head_sha);
    sha(facts.pr?.base_sha);
    condition(
      "freshness",
      boolean(facts.stable, "stable") ? "pass" : "unknown",
      "History review inputs must agree in both samples"
    );
    const ages = changeAges(facts, policy.stale_change_review_days);
    const stale = ages.some((file) => file.status === "stale");
    const humanReviews = [];
    if (stale) {
      if (!Array.isArray(facts.reviews))
        throw new EvaluationError("reviews required");
      const latest = /* @__PURE__ */ new Map();
      const published = facts.reviews.filter((r) => r.state !== "PENDING");
      for (const r of published) {
        string(r.submitted_at, "review.submitted_at");
        integer(r.id, "review.id");
      }
      published.sort(
        (a, b) => a.submitted_at < b.submitted_at ? -1 : a.submitted_at > b.submitted_at ? 1 : a.id - b.id
      );
      for (const review of published) {
        if (!["APPROVED", "CHANGES_REQUESTED", "DISMISSED", "COMMENTED"].includes(
          review.state
        ))
          throw new EvaluationError("unknown review state");
        if (review.state !== "COMMENTED")
          latest.set(string(review.author, "review.author"), review);
      }
      for (const review of latest.values()) {
        if (review.state !== "APPROVED" || review.commit_sha !== head) continue;
        const actor = string(review.author_type, "review.author_type");
        const association = string(
          review.author_association,
          "review.author_association"
        );
        if (actor === "User" && ["OWNER", "MEMBER", "COLLABORATOR"].includes(association))
          humanReviews.push(review.id);
      }
    }
    condition(
      "stale_change_review",
      stale && !humanReviews.length ? "blocked" : "pass",
      {
        threshold_days: policy.stale_change_review_days,
        observed_at: facts.observed_at,
        base_sha: facts.pr.base_sha,
        files: ages,
        required_human_approvals: Number(stale),
        human_approval_review_ids: humanReviews.sort((a, b) => a - b)
      }
    );
    for (const key of ["additions", "deletions", "changed_files"])
      integer(facts.change?.[key], key);
    result.decision = conditionsDecision(result.conditions);
  } catch (error) {
    condition(
      "data_integrity",
      "unknown",
      error instanceof Error ? error.message : String(error)
    );
  }
  return result;
}

// src/collect.ts
var PR_FIELDS = "number state headRefOid baseRefOid baseRefName updatedAt additions deletions changedFiles";
async function readPr(api, number) {
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
    changed_files: integer(raw.changedFiles, "changedFiles")
  };
}
async function decisionMetadata(api, number) {
  const reviews = (await api.pages(`/pulls/${number}/reviews`)).map((r) => {
    const user = r.user == null ? {} : object(r.user);
    return {
      id: integer(r.id, "review.id"),
      author: nullableString(user.login),
      state: string(r.state, "review.state"),
      commit_sha: nullableString(r.commit_id),
      submitted_at: nullableString(r.submitted_at),
      author_type: nullableString(user.type),
      author_association: nullableString(r.author_association)
    };
  });
  return reviews.sort(
    (a, b) => a.id - b.id || JSON.stringify(a).localeCompare(JSON.stringify(b))
  );
}
function observationChanges(before, after) {
  const changes = [];
  const compare = (group, old2, next2, identity = null) => {
    const a = old2, b = next2;
    for (const field of [
      .../* @__PURE__ */ new Set([...Object.keys(a), ...Object.keys(b)])
    ].sort()) {
      if (!isDeepStrictEqual(a[field] ?? null, b[field] ?? null))
        changes.push({
          group,
          identity,
          field,
          before: a[field] ?? null,
          after: b[field] ?? null
        });
    }
  };
  compare("pr", before.pr, after.pr);
  const old = new Map(before.reviews.map((r) => [r.id, r])), next = new Map(after.reviews.map((r) => [r.id, r]));
  for (const id of [.../* @__PURE__ */ new Set([...old.keys(), ...next.keys()])].sort(
    (a, b) => a - b
  )) {
    const a = old.get(id), b = next.get(id);
    if (!a || !b)
      changes.push({
        group: "reviews",
        identity: { id },
        field: "record",
        before: a ?? null,
        after: b ?? null
      });
    else compare("reviews", a, b, { id, author: a.author });
  }
  return changes;
}
var ChangeHistoryCollector = class {
  constructor(api, maxFiles = 100, maxRequests = 100) {
    this.api = api;
    this.maxFiles = maxFiles;
    this.maxRequests = maxRequests;
  }
  api;
  maxFiles;
  maxRequests;
  requests = 0;
  cache = /* @__PURE__ */ new Map();
  async collect(baseSha, files) {
    const base = sha(baseSha);
    const paths = files.map(
      (file) => [file.path, fileHistoryPath(file)]
    );
    if (paths.filter(([, previous]) => previous !== null).length > this.maxFiles)
      throw new CollectionError("change history file limit exceeded");
    const keyFor = (previous) => JSON.stringify([base, previous]);
    const missing = new Set(
      paths.flatMap(
        ([, previous]) => previous !== null && !this.cache.has(keyFor(previous)) ? [keyFor(previous)] : []
      )
    );
    if (this.requests + missing.size > this.maxRequests)
      throw new CollectionError(
        `change history run request limit exceeded: ${this.requests}/${this.maxRequests} used, ${missing.size} needed`
      );
    const history = { base_sha: base, files: [] };
    for (const [path, previous] of paths) {
      const record = {
        path,
        history_path: previous,
        last_commit_sha: null,
        last_changed_at: null
      };
      if (previous !== null) {
        const key = keyFor(previous);
        if (!this.cache.has(key)) {
          this.requests++;
          const query = new URLSearchParams({
            sha: base,
            path: previous,
            per_page: "1"
          });
          const commits = await this.api.request(
            `${this.api.prefix}/commits?${query}`
          );
          if (!Array.isArray(commits) || commits.length !== 1)
            throw new CollectionError(`last change unavailable: ${previous}`);
          const last = object(commits[0]);
          const at = string(
            object(object(last.commit).committer).date,
            "last_changed_at"
          );
          timestamp(at, "last_changed_at");
          this.cache.set(key, [sha(last.sha), at]);
        }
        [record.last_commit_sha, record.last_changed_at] = this.cache.get(key);
      }
      history.files.push(record);
    }
    return history;
  }
};
async function collect(api, number, options) {
  const now = options.now ?? (() => (/* @__PURE__ */ new Date()).toISOString());
  const facts = {
    schema_version: 3,
    observed_at: now(),
    repository: api.repository,
    collection_errors: [],
    stable: false,
    observation_changes: null
  };
  try {
    const before = await readPr(api, number);
    if (options.expectedHead !== void 0 && before.head_sha !== options.expectedHead)
      throw new ProposalHeadChanged(
        "PR head differs from event head before observation"
      );
    facts.pr = before;
    const files = (await api.pages(`/pulls/${number}/files`)).map((raw) => ({
      path: string(raw.filename, "filename"),
      previous_path: raw.status === "renamed" ? string(raw.previous_filename, "previous_filename") : null,
      changeType: raw.status === "removed" ? "DELETED" : string(raw.status, "file.status").toUpperCase(),
      additions: integer(raw.additions, "additions"),
      deletions: integer(raw.deletions, "deletions")
    }));
    if (new Set(files.map((f) => f.path)).size !== files.length)
      throw new CollectionError("duplicate file metadata");
    const types = /* @__PURE__ */ Object.create(null);
    for (const file of files)
      types[file.changeType] = (types[file.changeType] ?? 0) + 1;
    facts.change = {
      changed_files: files.length,
      additions: files.reduce((sum, f) => sum + f.additions, 0),
      deletions: files.reduce((sum, f) => sum + f.deletions, 0),
      types,
      binary_files: null,
      mode_changes: null
    };
    facts.files = files;
    if (["changed_files", "additions", "deletions"].some(
      (key) => facts.change[key] !== before[key]
    ))
      throw new CollectionError("PR file totals mismatch");
    facts.change_history = await (options.historyCollector ?? new ChangeHistoryCollector(api)).collect(before.base_sha, files);
    facts.observed_at = now();
    const needsReview = changeAges(
      facts,
      options.policy.stale_change_review_days
    ).some((f) => f.status === "stale");
    const initial = needsReview ? await decisionMetadata(api, number) : [];
    facts.reviews = initial;
    const confirmed = needsReview ? await decisionMetadata(api, number) : [];
    const after = await readPr(api, number);
    if (options.expectedHead !== void 0 && after.head_sha !== options.expectedHead)
      throw new ProposalHeadChanged(
        "PR head differs from event head during observation"
      );
    const fields = [
      "number",
      "head_sha",
      "base_sha",
      "base_ref",
      "additions",
      "deletions",
      "changed_files"
    ];
    facts.rechecked = {
      pr: fields.every((key) => before[key] === after[key]),
      reviews: isDeepStrictEqual(initial, confirmed)
    };
    facts.stable = Object.values(facts.rechecked).every(Boolean);
    facts.observation_changes = observationChanges(
      { pr: before, reviews: initial },
      { pr: after, reviews: confirmed }
    );
    facts.observed_at = now();
    if (!needsReview && changeAges(facts, options.policy.stale_change_review_days).some(
      (f) => f.status === "stale"
    ))
      throw new CollectionError(
        "history review threshold crossed during observation"
      );
  } catch (error) {
    if (error instanceof ProposalHeadChanged) throw error;
    facts.collection_errors.push(errorMessage(error));
  }
  return facts;
}
async function targets(api, event, requested) {
  if (requested !== void 0) {
    if (integer(requested, "PR number") === 0)
      throw new CollectionError("PR number must be positive");
    return [requested];
  }
  if (Object.hasOwn(event, "pull_request"))
    return [integer(object(event.pull_request).number, "PR number")];
  return (await api.pages("/pulls?state=open")).map(
    (p) => integer(p.number, "PR number")
  );
}

// src/observe.ts
async function observe(api, policy, event, number, expectedHead) {
  const collector = new ChangeHistoryCollector(api);
  const reports = [];
  for (const target of await targets(api, event, number))
    reports.push([
      target,
      assess(
        await collect(api, target, {
          policy,
          historyCollector: collector,
          expectedHead
        }),
        policy
      )
    ]);
  return reports;
}

// src/publish.ts
var DECISION_LABELS = {
  SHADOW_CONDITIONS_MET: "shadow/要対応事項なし",
  HUMAN_REVIEW_REQUIRED: "shadow/要対応",
  INSUFFICIENT_DATA: "shadow/再観測が必要"
};
var RETIRED_LABELS = [
  "shadow/レビュー待ち",
  "shadow/要マージ判断",
  "shadow/CI・レビュー待ち",
  "shadow/レビュー条件充足"
];
var MANAGED_LABELS = /* @__PURE__ */ new Set([
  ...Object.values(DECISION_LABELS),
  ...RETIRED_LABELS
]);
var LABEL_COLORS = {
  "shadow/要対応事項なし": "0E8A16",
  "shadow/要対応": "D93F0B",
  "shadow/再観測が必要": "BFD4F2"
};
var LABEL_DESCRIPTIONS = {
  "shadow/要対応事項なし": "変更履歴に基づく追加確認事項はありません。GitHubのマージ条件は別途確認してください。詳細はActionsのPR Merge Readiness。",
  "shadow/要対応": "変更履歴に基づく追加の人間レビューが必要。詳細はActionsのPR Merge Readiness。",
  "shadow/再観測が必要": "追加確認の観測が失敗、または対象・承認情報が変化。ActionsのPR Merge Readinessを手動で再実行する。"
};
async function ensureLabels(api) {
  for (const name of Object.values(DECISION_LABELS)) {
    const path = `${api.prefix}/labels/${encodeURIComponent(name)}`;
    const definition = {
      name,
      color: LABEL_COLORS[name],
      description: LABEL_DESCRIPTIONS[name]
    };
    let current;
    try {
      current = object(await api.request(path));
    } catch (error) {
      if (!(error instanceof PublishError) || !error.message.includes("HTTP 404"))
        throw error;
      try {
        current = object(await api.request(`${api.prefix}/labels`, definition));
      } catch (created) {
        if (!(created instanceof PublishError) || !created.message.includes("HTTP 422"))
          throw created;
        current = object(await api.request(path));
      }
    }
    if (current.description !== definition.description || String(current.color ?? "").toLowerCase() !== definition.color.toLowerCase())
      await api.request(
        path,
        { color: definition.color, description: definition.description },
        "PATCH"
      );
  }
}
function isPublicationTarget(pr) {
  const headRepo = object(pr.head).repo;
  return headRepo != null && object(headRepo).id === object(object(pr.base).repo).id && object(pr.user).login !== "dependabot[bot]";
}
function desiredLabel(report, current, repository, number) {
  if (!Object.hasOwn(DECISION_LABELS, report.decision))
    throw new PublishError(
      `unknown label decision: ${String(report.decision)}`
    );
  const facts = report.observations;
  const observed = facts?.pr;
  if (facts?.repository !== repository)
    throw new PublishError("report repository mismatch");
  if ((observed?.number ?? number) !== number)
    throw new PublishError("report PR mismatch");
  if (current.state === "closed" || !isPublicationTarget(current)) return null;
  const expected = {
    head_sha: object(current.head).sha,
    base_sha: object(current.base).sha,
    base_ref: object(current.base).ref
  };
  if (Object.keys(expected).some(
    (key) => observed?.[key] !== expected[key]
  ))
    return DECISION_LABELS.INSUFFICIENT_DATA;
  return DECISION_LABELS[report.decision];
}
async function syncLabels(api, number, names, desired) {
  let changed = false;
  if (desired !== null && !names.includes(desired)) {
    await api.request(`${api.prefix}/issues/${number}/labels`, {
      labels: [desired]
    });
    changed = true;
  }
  for (const name of names) {
    if (!MANAGED_LABELS.has(name) || name === desired) continue;
    try {
      await api.request(
        `${api.prefix}/issues/${number}/labels/${encodeURIComponent(name)}`,
        void 0,
        "DELETE"
      );
    } catch (error) {
      if (!(error instanceof PublishError) || !errorMessage(error).includes("HTTP 404"))
        throw error;
    }
    changed = true;
  }
  return changed ? "updated" : "unchanged";
}
function labelNames(current) {
  if (!Array.isArray(current.labels)) throw new PublishError("invalid labels");
  return current.labels.map((item) => string(object(item).name, "label.name"));
}
async function publishPr(api, number, report, expectedHead) {
  const current = object(await api.request(`${api.prefix}/pulls/${number}`));
  if (expectedHead !== void 0 && object(current.head).sha !== expectedHead)
    return "skipped";
  return syncLabels(
    api,
    number,
    labelNames(current),
    desiredLabel(report, current, api.repository, number)
  );
}
async function cleanupClosed(api) {
  const numbers = /* @__PURE__ */ new Set();
  for (const label of [...MANAGED_LABELS].sort()) {
    for (const issue of await api.pages(
      `/issues?state=closed&labels=${encodeURIComponent(label)}`
    )) {
      if (Object.hasOwn(issue, "pull_request"))
        numbers.add(integer(issue.number, "PR number"));
    }
  }
  for (const number of [...numbers].sort((a, b) => a - b)) {
    const current = object(await api.request(`${api.prefix}/pulls/${number}`));
    if (current.state === "closed")
      await syncLabels(api, number, labelNames(current), null);
  }
}

// src/report.ts
function escapeHtml(text) {
  return text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#x27;");
}
function markdown(result) {
  const safe = (value) => "<code>" + escapeHtml(JSON.stringify(value ?? null)) + "</code>";
  const facts = result.observations;
  const lines = [
    "## PR Merge Readiness",
    "",
    "変更履歴に基づく追加確認の観測時点の判定です。承認数・会話解決・CI などのマージ条件は GitHub Ruleset で確認してください。レビュー完了やマージ許可を示すものではありません。",
    "",
    "追加確認の判定: " + safe(result.decision),
    "",
    "観測時刻: " + safe(facts.observed_at),
    "",
    "対象: " + safe(facts.pr),
    "",
    "変更量・形態（判定には加点しない）: " + safe(facts.change),
    "",
    "前回変更からの経過日数の閾値（超過時は現在headへの人間の承認が必要）: " + safe(result.policy.stale_change_review_days),
    ""
  ];
  for (const condition of result.conditions) lines.push("- " + safe(condition));
  lines.push("", "### 観測間の変化", "");
  if (facts.observation_changes == null)
    lines.push("差分情報なし（再取得未完了）。");
  else if (!facts.observation_changes.length)
    lines.push("比較した正規化メタデータに変化はありません。");
  else
    for (const change of facts.observation_changes)
      lines.push("- " + safe(change));
  return lines.join("\n") + "\n";
}

// src/runtime.ts
async function output(values, env = process.env) {
  const lines = Object.entries(values).map(([key, value]) => {
    const text = String(value);
    if (/[\r\n]/.test(text)) throw new EvaluationError("invalid output value");
    return `${key}=${text}
`;
  });
  if (env.GITHUB_OUTPUT) await appendFile(env.GITHUB_OUTPUT, lines.join(""));
}
async function summary(text, env = process.env) {
  if (env.GITHUB_STEP_SUMMARY)
    await appendFile(env.GITHUB_STEP_SUMMARY, text + "\n");
}
async function reportError(error, env = process.env) {
  console.log(JSON.stringify({ error: errorMessage(error) }));
  try {
    await summary(
      "処理失敗: <code>" + escapeHtml(errorMessage(error)) + "</code>",
      env
    );
  } catch (summaryError) {
    console.log(JSON.stringify({ summary_error: errorMessage(summaryError) }));
  }
}
function verifySource(env = process.env) {
  if (!env.GITHUB_ACTION_REF)
    throw new EvaluationError(
      "local Actions are unsupported; use the remote Action pinned to a full SHA"
    );
  sha(env.GITHUB_ACTION_REF);
  if (env.GITHUB_ACTION_REPOSITORY !== ACTION_REPOSITORY)
    throw new EvaluationError("Action source repository mismatch");
}
function prEventOperation(event) {
  if (!isPublicationTarget(object(event.pull_request))) return "skip";
  if (event.action === "edited" && !Object.hasOwn(object(event.changes ?? {}), "base"))
    return "skip";
  return [
    "opened",
    "reopened",
    "synchronize",
    "edited",
    "ready_for_review",
    "converted_to_draft",
    "closed"
  ].includes(String(event.action)) ? "observe" : "skip";
}
function manualPrNumber(event) {
  if (event.inputs == null) return void 0;
  const inputs = object(event.inputs);
  if (Object.keys(inputs).some((key) => key !== "pr-number"))
    throw new EvaluationError(
      "manual inputs only support pr-number; labels are always updated"
    );
  const number = Object.hasOwn(inputs, "pr-number") ? inputs["pr-number"] : "";
  return number === "" ? void 0 : positive(number);
}
async function runAction(options = {}) {
  const env = options.env ?? process.env;
  verifySource(env);
  const eventName = string(env.GITHUB_EVENT_NAME, "GITHUB_EVENT_NAME");
  if (!["pull_request", "workflow_dispatch"].includes(eventName)) {
    await output({ operation: "skip" }, env);
    return 0;
  }
  const event = object(
    JSON.parse(
      await readFile(
        string(env.GITHUB_EVENT_PATH, "GITHUB_EVENT_PATH"),
        "utf8"
      )
    )
  );
  if (eventName === "pull_request" && prEventOperation(event) === "skip") {
    await output({ operation: "skip" }, env);
    return 0;
  }
  const number = eventName === "workflow_dispatch" ? manualPrNumber(event) : void 0;
  const expectedHead = eventName === "pull_request" ? sha(object(object(event.pull_request).head).sha) : void 0;
  const policy = policyFromInputs(env);
  const token = env.INPUT_TOKEN?.trim() ?? "";
  if (!token) throw new EvaluationError("token required");
  const repository = string(env.GITHUB_REPOSITORY, "GITHUB_REPOSITORY");
  const apiOptions = {
    token,
    apiUrl: env.GITHUB_API_URL,
    graphqlUrl: env.GITHUB_GRAPHQL_URL
  };
  const api = options.reader ?? new GitHub(repository, apiOptions);
  await output({ operation: "observe" }, env);
  const reports = await observe(api, policy, event, number, expectedHead);
  await summary(
    reports.map(([, report]) => markdown(report)).join("\n") || "評価対象の open PR はありません。",
    env
  );
  const writer = options.writer ?? new GitHub(repository, apiOptions, true);
  let failed = reports.some(
    ([, report]) => report.decision === "INSUFFICIENT_DATA"
  );
  if (reports.length) await ensureLabels(writer);
  for (const [target, report] of reports) {
    try {
      console.log(
        JSON.stringify({
          pr: target,
          publication: await publishPr(writer, target, report, expectedHead)
        })
      );
    } catch (error) {
      failed = true;
      await reportError(
        new EvaluationError(`PR #${target}: ${errorMessage(error)}`),
        env
      );
    }
  }
  if (eventName === "workflow_dispatch" && number === void 0)
    await cleanupClosed(writer);
  return Number(failed);
}
async function actionMain(options = {}) {
  try {
    return await runAction(options);
  } catch (error) {
    await reportError(error, options.env);
    return 1;
  }
}

// src/main.ts
process.exitCode = await actionMain();
