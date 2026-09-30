/** 実行全体で履歴予算を共有し、公開前に全 PR を観測する。 */
import type { Reader } from "./api.js";
import { ChangeHistoryCollector, collect, targets } from "./collect.js";
import type { Assessment, JsonObject, Policy } from "./contracts.js";
import { assess } from "./evaluate.js";
export async function observe(
  api: Reader,
  policy: Policy,
  event: JsonObject,
  number?: number,
  expectedHead?: string,
): Promise<[number, Assessment][]> {
  const collector = new ChangeHistoryCollector(api);
  const reports: [number, Assessment][] = [];
  for (const target of await targets(api, event, number))
    reports.push([
      target,
      assess(
        await collect(api, target, {
          policy,
          historyCollector: collector,
          expectedHead,
        }),
        policy,
      ),
    ]);
  return reports;
}
