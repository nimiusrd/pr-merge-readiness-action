/** ローカル設定の検証。Action の実行入口とは分ける。 */
import { loadConfig } from "./config.js";
import { errorMessage, EvaluationError } from "./contracts.js";
try {
  const args = process.argv.slice(2);
  if (
    args.length !== 3 ||
    args[0] !== "validate-config" ||
    args[1] !== "--config" ||
    !args[2]
  )
    throw new EvaluationError("usage: validate-config --config <path>");
  await loadConfig(args[2]);
  console.log(JSON.stringify({ valid: true }));
} catch (error) {
  console.log(JSON.stringify({ error: errorMessage(error) }));
  process.exitCode = 1;
}
