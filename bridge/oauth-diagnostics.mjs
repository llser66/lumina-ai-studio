import { AsyncLocalStorage } from 'node:async_hooks';
const flows = new AsyncLocalStorage();
const nativeFetch = globalThis.fetch;
let installed = false;

function requestStage(input) {
  try {
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
    if (/\/(?:oauth\/token|oauth\/access_token|auth\/keys)$/.test(url.pathname)) return 'exchanging_token';
  } catch {}
  return undefined;
}
export function installOAuthDiagnostics() {
  if (installed) return;
  installed = true;
  globalThis.fetch = async (input, options) => {
    const context = flows.getStore();
    const stage = context && requestStage(input);
    if (!context || !stage) return nativeFetch(input, options);
    context.stage = stage;
    context.notify(stage);
    try {
      const response = await nativeFetch(input, options);
      context.http_status = response.status;
      return response;
    } catch (error) {
      context.network_failed = true;
      throw error;
    }
  };
}
export function withOAuthDiagnostics(context, action) {
  return flows.run(context, action);
}
