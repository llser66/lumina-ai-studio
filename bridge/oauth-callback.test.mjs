import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

// Run the installed SDK login implementation in memory with injected transport
// and HTTP server mocks. This test never binds port 1455 or contacts a provider.
const sdkPath = new URL('../node_modules/@earendil-works/pi-ai/dist/auth/oauth/openai-chatgpt.js', import.meta.url);
const sdkSource = await readFile(sdkPath, 'utf8');
assert.equal((sdkSource.match(/^import .*;$/gm) || []).length, 5, 'Review the harness if SDK imports change');
const isolatedSource = sdkSource
  .replace(/^import .*;$/gm, '')
  .replace('export const openaiChatGPTOAuth =', 'const openaiChatGPTOAuth =')
  .replace(/^\/\/# sourceMappingURL=.*$/gm, '');
const loadIsolatedLogin = new Function(
  'randomBytes', 'createServer', 'oauthErrorHtml', 'oauthSuccessHtml',
  'getProviderEnvValue', 'generatePKCE', 'fetch',
  isolatedSource + '\nreturn openaiChatGPTOAuth;',
);

function loginHarness(tokenResponse) {
  const seen = { callbackStatus: null, callbackPage: null, requests: 0, promptAborts: 0, events: [], serverClosed: false };
  let callbackHandler;
  const auth = loadIsolatedLogin(
    () => ({ toString: () => 'test-only-state' }),
    handler => {
      callbackHandler = handler;
      return {
        once() {}, removeListener() {}, on() {},
        listen(_port, _host, ready) { ready(); },
        close() { seen.serverClosed = true; }, closeAllConnections() {},
      };
    },
    () => 'error-page', () => 'success-page', () => undefined,
    async () => ({ verifier: 'test-only-verifier', challenge: 'test-only-challenge' }),
    async () => { seen.requests++; return tokenResponse(); },
  );
  const controller = new AbortController();
  const interaction = {
    signal: controller.signal,
    notify(event) {
      seen.events.push(event.type);
      if (event.type !== 'auth_url') return;
      const state = new URL(event.url).searchParams.get('state');
      queueMicrotask(() => callbackHandler(
        { url: '/auth/callback?' + new URLSearchParams({ code: 'test-only-code', client_id: 'test-only-client', state }) },
        { writeHead(status) { seen.callbackStatus = status; }, end(page) { seen.callbackPage = page; } },
      ));
    },
    prompt(prompt) {
      return new Promise((_resolve, reject) => {
        prompt.signal.addEventListener('abort', () => {
          seen.promptAborts++;
          reject(new Error('Cancelled'));
        }, { once: true });
      });
    },
  };
  return { seen, login: () => auth.login(interaction, { getDeviceId: () => '00000000-0000-0000-0000-000000000000' }) };
}

function tokenBody(scope = 'openid chatgpt.tokens.use.direct') {
  return { access_token: 'test-only-access', refresh_token: 'test-only-refresh', id_token: 'test-only-id', expires_in: 3600, scope };
}

function assertAcceptedCallback(seen) {
  assert.equal(seen.callbackStatus, 200);
  assert.equal(seen.callbackPage, 'success-page');
  assert.deepEqual(seen.events, ['auth_url', 'progress']);
  assert.equal(seen.requests, 1);
  assert.equal(seen.serverClosed, true);
}

test('SDK success callback page does not prove token exchange succeeded', async () => {
  const harness = loginHarness(() => new Response('test-only-sensitive-provider-body', { status: 401 }));
  await assert.rejects(harness.login(), /OpenAI OAuth token request failed \(401\)/);
  assertAcceptedCallback(harness.seen);
});

test('accepted SDK callback may still lack ChatGPT plan permission after exchange', async () => {
  const harness = loginHarness(() => Response.json(tokenBody('openid')));
  await assert.rejects(harness.login(), /OAuth grant did not include chatgpt\.tokens\.use\.direct/);
  assertAcceptedCallback(harness.seen);
});

test('valid SDK token exchange survives cancellation of the unused manual prompt', async () => {
  const harness = loginHarness(() => Response.json(tokenBody()));
  const result = await harness.login();
  assert.equal(result.type, 'oauth');
  assert.equal(result.scopes.includes('chatgpt.tokens.use.direct'), true);
  assert.equal(harness.seen.promptAborts, 1);
  assertAcceptedCallback(harness.seen);
});

test('SDK callback success can precede a token transport failure', async () => {
  const harness = loginHarness(() => { throw new TypeError('fetch failed'); });
  await assert.rejects(harness.login(), /fetch failed/);
  assertAcceptedCallback(harness.seen);
});

test('OAuth classifier preserves token status and stage without forwarding sensitive response text', async () => {
  const { classifyOAuthFailure } = await import('./oauth-errors.mjs');
  const result = classifyOAuthFailure(new Error(
    'OpenAI OAuth token request failed (401): {"access_token":"test-secret-access","refresh_token":"test-secret-refresh","code":"test-secret-code"}',
  ), { stage: 'exchanging_token', http_status: 401 });
  assert.equal(result.code, 'oauth_token_rejected');
  assert.equal(result.stage, 'exchanging_token');
  assert.equal(result.http_status, 401);
  assert.equal(result.retryable, true);
  assert.doesNotMatch(JSON.stringify(result), /test-secret|access_token|refresh_token/);
});

test('OAuth classifier distinguishes missing plan scope from token transport failure', async () => {
  const { classifyOAuthFailure } = await import('./oauth-errors.mjs');
  const context = { stage: 'exchanging_token' };
  const scope = classifyOAuthFailure(new Error('OpenAI OAuth grant did not include chatgpt.tokens.use.direct'), context);
  assert.equal(scope.code, 'oauth_scope_missing');
  assert.equal(scope.stage, 'exchanging_token');
  const transport = classifyOAuthFailure(new TypeError('fetch failed'), { ...context, network_failed: true });
  assert.equal(transport.code, 'oauth_network_error');
  assert.equal(transport.stage, 'exchanging_token');
});

test('OAuth classifier explains unsupported-region rejection without exposing provider response', async () => {
  const { classifyOAuthFailure } = await import('./oauth-errors.mjs');
  const result = classifyOAuthFailure(new Error(
    'OpenAI Codex token exchange failed (403): {"error":{"code":"unsupported_country_region_territory","message":"Country, region, or territory not supported"}}',
  ), { stage: 'exchanging_token', http_status: 403 });
  assert.equal(result.code, 'oauth_region_unsupported');
  assert.equal(result.stage, 'exchanging_token');
  assert.equal(result.http_status, 403);
  assert.doesNotMatch(JSON.stringify(result), /unsupported_country|Country, region|territory not supported/);
});

test('OAuth classifier separates stale callback state and occupied callback listener', async () => {
  const { classifyOAuthFailure } = await import('./oauth-errors.mjs');
  const state = classifyOAuthFailure(new Error('OAuth state mismatch'), { stage: 'waiting_user' });
  assert.equal(state.code, 'oauth_state_mismatch');
  assert.equal(state.stage, 'waiting_user');
  const busy = classifyOAuthFailure(new Error('Port 1455 is in use, probably by an unfinished login in another pi session or by the Codex CLI.'), { stage: 'starting' });
  assert.equal(busy.code, 'oauth_callback_busy');
  assert.equal(busy.stage, 'starting');
});

test('OAuth classifier does not expose malformed token response fields', async () => {
  const { classifyOAuthFailure } = await import('./oauth-errors.mjs');
  const result = classifyOAuthFailure(new Error(
    'OpenAI Codex token exchange response missing fields: {"access_token":"test-secret-access","id_token":"test-secret-id"}',
  ), { stage: 'exchanging_token' });
  assert.equal(result.stage, 'exchanging_token');
  assert.doesNotMatch(JSON.stringify(result), /test-secret|access_token|id_token/);
});
