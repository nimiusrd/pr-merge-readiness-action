import { test } from "node:test";
import assert from "node:assert/strict";
import { GitHub, CollectionError, PublishError } from "../src/api.js";
import type { ApiOptions } from "../src/api.js";
function api(
  fetcher: typeof fetch,
  options: Partial<ApiOptions> = {},
  writing = false,
): GitHub {
  return new GitHub(
    "example/project",
    { token: "test-token", fetch: fetcher, ...options },
    writing,
  );
}
test("通信は token・JSON・API version を送り GraphQL エラーを拒否する", async () => {
  const client = api(async (url, init) => {
    assert.equal(url, "https://api.github.com/graphql");
    assert.equal(init?.method, "POST");
    const headers = init!.headers as Record<string, string>;
    assert.equal(headers.Authorization, "Bearer test-token");
    assert.equal(headers["Content-Type"], "application/json");
    assert.ok(JSON.parse(String(init!.body)).query);
    return new Response('{"errors":[{"message":"DO NOT EXPOSE"}],"data":{}}');
  });
  await assert.rejects(
    client.request("/graphql", { query: "query {}" }),
    /GraphQL returned errors/,
  );
});
test("DELETE の空応答を成功とし、body を送らない", async () => {
  const client = api(
    async (_url, init) => {
      assert.equal(init?.method, "DELETE");
      assert.equal(init?.body, undefined);
      return new Response(null, { status: 204 });
    },
    {},
    true,
  );
  assert.equal(await client.request("/labels/test", undefined, "DELETE"), null);
});
for (const writing of [false, true])
  test(`HTTP・不正 JSON・容量超過を拒否: writer=${writing}`, async () => {
    const ErrorType = writing ? PublishError : CollectionError;
    await assert.rejects(
      api(
        async () => new Response("secret", { status: 403 }),
        {},
        writing,
      ).request("/fail"),
      ErrorType,
    );
    await assert.rejects(
      api(async () => new Response("not-json"), {}, writing).request("/fail"),
      ErrorType,
    );
    await assert.rejects(
      api(
        async () => new Response('{"long":1}'),
        { maxResponseBytes: 4 },
        writing,
      ).request("/long"),
      /exceeds limit/,
    );
  });
test("レスポンスを読み終わるまでタイムアウトを適用する", async () => {
  const client = api(
    async (_url, init) =>
      new Response(
        new ReadableStream({
          start(controller) {
            init!.signal!.addEventListener("abort", () =>
              controller.error(new Error("aborted")),
            );
          },
        }),
      ),
    { timeoutMs: 10 },
  );
  await assert.rejects(client.request("/slow"), CollectionError);
});
test("通信エラー本文に token や応答データを露出しない", async () => {
  await assert.rejects(
    api(async () => {
      throw new Error("test-token private-response");
    }).request("/test"),
    (error) =>
      error instanceof CollectionError &&
      !error.message.includes("test-token") &&
      !error.message.includes("private-response"),
  );
});
test("ページを全取得し、上限と total_count の不一致を拒否する", async () => {
  const urls: string[] = [];
  const client = api(async (url) => {
    urls.push(String(url));
    return Response.json(
      urls.length === 1
        ? Array.from({ length: 100 }, (_, id) => ({ id }))
        : [{ id: 101 }],
    );
  });
  assert.equal((await client.pages("/pulls?state=open")).length, 101);
  assert.ok(urls[1]!.includes("&per_page=100&page=2"));
  await assert.rejects(
    api(async () => Response.json(Array(100).fill({})), { maxPages: 1 }).pages(
      "/pulls",
    ),
    /pagination limit/,
  );
  await assert.rejects(
    api(async () => Response.json({ total_count: 3, records: [{}] })).pages(
      "/test",
      "records",
    ),
    /count mismatch/,
  );
  await assert.rejects(
    api(async () => Response.json({})).pages("/test"),
    /invalid REST page/,
  );
});
test("GraphQL は PR の必要フィールドだけを問い合わせる", async () => {
  const client = api(async (_url, init) => {
    const body = JSON.parse(String(init!.body));
    assert.ok(!body.query.includes("$cursor"));
    assert.deepEqual(body.variables, {
      owner: "example",
      repo: "project",
      number: 7,
    });
    return Response.json({
      data: { repository: { pullRequest: { number: 7 } } },
    });
  });
  assert.deepEqual(await client.graphql(7, "number"), { number: 7 });
});
for (const repository of [
  "../repo",
  "owner",
  "owner/repo/extra",
  "owner/../repo",
  "owner/repo?x=1",
])
  test(`repository を拒否: ${repository}`, () => {
    assert.throws(() => new GitHub(repository, { token: "" }));
  });
