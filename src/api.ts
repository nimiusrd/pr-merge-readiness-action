/** REST/GraphQL の共通通信。レスポンスとページ数を制限する。 */
import { object, string, integer, errorMessage } from "./contracts.js";
import type { JsonObject } from "./contracts.js";
export class CollectionError extends Error {}
export class ProposalHeadChanged extends CollectionError {}
export class PublishError extends Error {}
export interface Reader {
  repository: string;
  prefix: string;
  request(path: string, body?: JsonObject): Promise<unknown>;
  pages(path: string, key?: string): Promise<JsonObject[]>;
  graphql(number: number, selection: string): Promise<JsonObject>;
}
export interface Writer {
  repository: string;
  prefix: string;
  request(path: string, body?: JsonObject, method?: string): Promise<unknown>;
  pages(path: string): Promise<JsonObject[]>;
}
export interface ApiOptions {
  token: string;
  apiUrl?: string;
  graphqlUrl?: string;
  fetch?: typeof fetch;
  maxPages?: number;
  maxResponseBytes?: number;
  timeoutMs?: number;
}
export class GitHub implements Reader, Writer {
  readonly prefix: string;
  private readonly owner: string;
  private readonly repo: string;
  constructor(
    readonly repository: string,
    private readonly options: ApiOptions,
    private readonly writing = false,
  ) {
    if (
      !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository) ||
      repository.split("/").some((part) => part === "." || part === "..")
    )
      throw this.error("invalid repository");
    this.prefix = `/repos/${repository}`;
    [this.owner, this.repo] = repository.split("/") as [string, string];
  }
  private error(message: string): Error {
    return this.writing
      ? new PublishError(message)
      : new CollectionError(message);
  }
  async request(
    path: string,
    body?: JsonObject,
    method?: string,
  ): Promise<unknown> {
    const verb = method ?? (body === undefined ? "GET" : "POST");
    const url =
      path === "/graphql"
        ? (this.options.graphqlUrl ?? "https://api.github.com/graphql")
        : (this.options.apiUrl ?? "https://api.github.com").replace(/\/$/, "") +
          path;
    const controller = new AbortController();
    // 接続だけでなく body の読み込み全体にも期限を適用する。
    const timer = setTimeout(
      () => controller.abort(),
      this.options.timeoutMs ?? 30_000,
    );
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
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
          ...(this.options.token
            ? { Authorization: `Bearer ${this.options.token}` }
            : {}),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) {
        await response.body?.cancel();
        throw this.error(
          `API ${this.writing ? verb + " " : ""}${path}: HTTP ${response.status}`,
        );
      }
      reader = response.body?.getReader();
      const chunks: Uint8Array[] = [];
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
      const result: unknown = JSON.parse(
        Buffer.concat(chunks).toString("utf8"),
      );
      if (
        !this.writing &&
        result !== null &&
        typeof result === "object" &&
        !Array.isArray(result) &&
        object(result).errors &&
        (!Array.isArray(object(result).errors) ||
          (object(result).errors as unknown[]).length)
      )
        throw this.error("GraphQL returned errors");
      return result;
    } catch (error) {
      if (error instanceof CollectionError || error instanceof PublishError)
        throw error;
      // fetch の詳細（token・URL・応答本文）をログに含めない。
      throw this.error(
        `API ${this.writing ? verb + " " : ""}${path}: ${error instanceof Error ? error.name : "transport error"}`,
      );
    } finally {
      clearTimeout(timer);
      controller.abort();
      await reader?.cancel().catch(() => undefined);
    }
  }
  async pages(path: string, key?: string): Promise<JsonObject[]> {
    const records: JsonObject[] = [];
    const separator = path.includes("?") ? "&" : "?";
    for (let page = 1; page <= (this.options.maxPages ?? 30); page++) {
      const response = await this.request(
        `${this.prefix}${path}${separator}per_page=100&page=${page}`,
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
  async graphql(number: number, selection: string): Promise<JsonObject> {
    const query = `query($owner: String!, $repo: String!, $number: Int!) { repository(owner: $owner, name: $repo) { pullRequest(number: $number) { ${selection} } } }`;
    const result = object(
      await this.request("/graphql", {
        query,
        variables: { owner: this.owner, repo: this.repo, number },
      }),
    );
    return object(object(object(result.data).repository).pullRequest);
  }
}
export function nullableString(value: unknown): string | null {
  return value == null ? null : string(value, "metadata");
}
export function numberField(raw: JsonObject, name: string): number {
  return integer(raw[name], name);
}
export { errorMessage };
