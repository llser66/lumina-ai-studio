import assert from 'node:assert/strict';
import { test } from 'node:test';
import { operation } from './service.mjs';
import { createModels } from '@earendil-works/pi-ai';
import { createRuntimeProvider } from './provider-factories.mjs';

function command(op = 'image', connectionId = 'test-connection') {
  return { op, connection: { id: connectionId, user_id: 'test-user', provider: 'openrouter', version: 1, credential: { type: 'api_key', key: 'test-only-pi-key' } }, model: 'black-forest-labs/flux.2-pro', prompt: 'A blue bottle', ratio: '3:2', size: '1536x1024' };
}

test('OpenRouter image generation uses its dedicated Images API and supported parameters', async () => {
  let calls = 0;
  const mockFetch = async (url, init) => {
    calls++;
    assert.equal(new Headers(init.headers).get('authorization'), 'Bearer test-only-pi-key');
    if (calls === 1) {
      assert.equal(String(url), 'https://openrouter.ai/api/v1/images/models');
      assert.equal(init.method, 'GET');
      return new Response(JSON.stringify({ data: [{
        id: 'black-forest-labs/flux.2-pro',
        supported_parameters: {
          aspect_ratio: { type: 'enum', values: ['1:1', '3:2'] },
          n: { type: 'range', min: 1, max: 1 },
          output_format: { type: 'enum', values: ['png'] },
          input_references: { type: 'range', min: 0, max: 10 },
        },
      }] }), { status: 200, headers: { 'content-type': 'application/json' } });
    }
    assert.equal(String(url), 'https://openrouter.ai/api/v1/images');
    assert.equal(init.method, 'POST');
    const body = JSON.parse(init.body);
    assert.equal(body.model, 'black-forest-labs/flux.2-pro');
    assert.equal(body.prompt, 'A blue bottle');
    assert.equal(body.aspect_ratio, '3:2');
    assert.equal(body.output_format, 'png');
    assert.equal(body.n, 1);
    assert.deepEqual(body.input_references, [{ type: 'image_url', image_url: { url: 'data:image/png;base64,aGVsbG8=' } }]);
    return new Response(JSON.stringify({ created: 1791389000, data: [{ b64_json: 'aGVsbG8=', media_type: 'image/png' }], usage: { cost: 0 } }), { status: 200, headers: { 'content-type': 'application/json' } });
  };
  const result = await operation({ ...command('image', 'supported-params'), reference: 'aGVsbG8=' }, AbortSignal.timeout(5000), mockFetch);
  assert.equal(result.ok, true);
  assert.equal(result.image, 'aGVsbG8=');
  assert.equal(result.mime_type, 'image/png');
  assert.equal(calls, 2);
});

test('Ming Image sends only parameters supported by its OpenRouter image endpoint', async () => {
  const calls = [];
  const result = await operation({ ...command('image', 'ming-model'), model: 'inclusionai/ming-image-0.1-design' }, AbortSignal.timeout(5000), async (url, init) => {
    calls.push({ url: String(url), init });
    if (calls.length === 1) return new Response(JSON.stringify({ data: [{
      id: 'inclusionai/ming-image-0.1-design',
      supported_parameters: {
        n: { type: 'range', min: 1, max: 1 },
        input_references: { type: 'range', min: 0, max: 0 },
        size: { type: 'boolean' },
      },
    }] }), { status: 200, headers: { 'content-type': 'application/json' } });
    return new Response(JSON.stringify({ data: [{ b64_json: 'aGVsbG8=' }] }), { status: 200, headers: { 'content-type': 'application/json' } });
  });
  assert.equal(result.ok, true);
  const body = JSON.parse(calls[1].init.body);
  assert.equal(calls[1].url, 'https://openrouter.ai/api/v1/images');
  assert.deepEqual(body, { model: 'inclusionai/ming-image-0.1-design', prompt: 'A blue bottle', n: 1, size: '1536x1024' });
});

test('Ming Image rejects reference edits because the endpoint accepts no input references', async () => {
  let calls = 0;
  const result = await operation({ ...command('image', 'ming-edit'), model: 'inclusionai/ming-image-0.1-design', reference: 'aGVsbG8=' }, AbortSignal.timeout(5000), async () => {
    calls++;
    return new Response(JSON.stringify({ data: [{
      id: 'inclusionai/ming-image-0.1-design',
      supported_parameters: { input_references: { type: 'range', min: 0, max: 0 } },
    }] }), { status: 200, headers: { 'content-type': 'application/json' } });
  });
  assert.equal(result.code, 'edit_unsupported');
  assert.equal(calls, 1);
});

test('OpenRouter reference-only image model requires an uploaded image before generation', async () => {
  let calls = 0;
  const result = await operation({ ...command('image', 'ming-layer'), model: 'inclusionai/ming-image-0.1-design-layer' }, AbortSignal.timeout(5000), async () => {
    calls++;
    return new Response(JSON.stringify({ data: [{
      id: 'inclusionai/ming-image-0.1-design-layer',
      supported_parameters: { input_references: { type: 'range', min: 1, max: 1 } },
    }] }), { status: 200, headers: { 'content-type': 'application/json' } });
  });
  assert.equal(result.code, 'reference_required');
  assert.equal(calls, 1);
});

test('OpenRouter image 500 is uncertain and never automatically retried', async () => {
  let calls = 0;
  const result = await operation(command('image', 'image-server-error'), AbortSignal.timeout(5000), async (url, init) => {
    calls++;
    if (calls === 1) {
      assert.equal(String(url), 'https://openrouter.ai/api/v1/images/models');
      return new Response(JSON.stringify({ data: [{ id: 'black-forest-labs/flux.2-pro', supported_parameters: { n: { type: 'range', min: 1, max: 1 } } }] }), { status: 200, headers: { 'content-type': 'application/json' } });
    }
    assert.equal(String(url), 'https://openrouter.ai/api/v1/images');
    return new Response(JSON.stringify({ error: { message: 'test-only internal detail' } }), { status: 500, headers: { 'content-type': 'application/json' } });
  });
  assert.equal(calls, 2);
  assert.equal(result.code, 'provider_unknown');
  assert.equal(result.uncertain, true);
  assert.ok(!JSON.stringify(result).includes('test-only internal detail'));
});

test('OpenRouter image authentication rejection is safe and non-billable', async () => {
  let calls = 0;
  const result = await operation(command('image', 'image-unauthorized'), AbortSignal.timeout(5000), async (_url, _init) => {
    calls++;
    if (calls === 1) return new Response(JSON.stringify({ data: [{ id: 'black-forest-labs/flux.2-pro' }] }), { status: 200, headers: { 'content-type': 'application/json' } });
    return new Response(JSON.stringify({ error: { message: 'test-only rejected' } }), { status: 401, headers: { 'content-type': 'application/json' } });
  });
  assert.equal(result.code, 'credentials');
  assert.equal(result.uncertain, false);
});

test('OpenRouter image 403 identifies model or account access denial', async () => {
  let calls = 0;
  const result = await operation(command('image', 'image-access-denied'), AbortSignal.timeout(5000), async () => {
    calls++;
    if (calls === 1) return new Response(JSON.stringify({ data: [{ id: 'black-forest-labs/flux.2-pro' }] }), { status: 200, headers: { 'content-type': 'application/json' } });
    return new Response(JSON.stringify({ error: { message: 'test-only access detail' } }), { status: 403, headers: { 'content-type': 'application/json' } });
  });
  assert.equal(result.code, 'provider_access_denied');
  assert.equal(result.uncertain, false);
  assert.ok(!JSON.stringify(result).includes('test-only access detail'));
});

test('OpenRouter model catalog network failure is not marked billable or uncertain', async () => {
  const result = await operation(command('image', 'catalog-network-error'), AbortSignal.timeout(5000), async () => {
    throw new Error('test-only network failure');
  });
  assert.equal(result.code, 'provider_network_error');
  assert.equal(result.uncertain, false);
});

test('OpenRouter generation network failure is marked uncertain', async () => {
  let calls = 0;
  const result = await operation(command('image', 'generation-network-error'), AbortSignal.timeout(5000), async () => {
    calls++;
    if (calls === 1) return new Response(JSON.stringify({ data: [{ id: 'black-forest-labs/flux.2-pro' }] }), { status: 200, headers: { 'content-type': 'application/json' } });
    throw new Error('test-only network failure');
  });
  assert.equal(result.code, 'provider_unknown');
  assert.equal(result.uncertain, true);
});

test('unknown image model is rejected before network access', async () => {
  let called = false;
  const result = await operation({ ...command(), model: 'not-an-image-model' }, AbortSignal.timeout(5000), async () => { called = true; throw new Error('No network allowed'); });
  assert.equal(result.code, 'model_unavailable');
  assert.equal(called, false);
});

function completionResponse() {
  return new Response('data: ' + JSON.stringify({ id: 'test-text', choices: [{ index: 0, delta: { role: 'assistant', content: 'Refined vision prompt' }, finish_reason: null }] }) + '\n\ndata: ' + JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 5, completion_tokens: 3 } }) + '\n\ndata: [DONE]\n\n', { headers: { 'content-type': 'text/event-stream' } });
}

for (const [provider, model, endpoint] of [
  ['deepseek', 'deepseek-flash', 'https://api.deepseek.com/chat/completions'],
  ['qwen', 'qwen-vl-plus', 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'],
]) {
  test(provider + ' uses its own API key and forwards reference image to actual SDK', async () => {
    let calls = 0;
    const result = await operation({ op: 'enhance', connection: { ...command().connection, id: provider + '-test', provider }, model, prompt: 'Keep the bottle shape', reference: 'aGVsbG8=' }, AbortSignal.timeout(5000), async (input, init) => {
      calls++;
      assert.equal(String(input), endpoint);
      assert.equal(new Headers(init.headers).get('authorization'), 'Bearer test-only-pi-key');
      const body = JSON.parse(init.body);
      assert.equal(body.model, model);
      assert.equal(body.messages.at(-1).content[1].image_url.url, 'data:image/png;base64,aGVsbG8=');
      return completionResponse();
    });
    assert.equal(calls, 1);
    assert.equal(result.ok, true);
    assert.equal(result.text, 'Refined vision prompt');
  });
}

test('custom provider executes supplied model through actual compatible SDK adapter', async () => {
  const models = createModels({ authContext: { env: async () => undefined, fileExists: async () => false }, credentials: {
    read: async () => ({ type: 'api_key', key: 'test-only-custom-key' }), list: async () => [], modify: async (_provider, fn) => fn({ type: 'api_key', key: 'test-only-custom-key' }),
  } });
  models.setProvider(createRuntimeProvider({ provider: 'custom', base_url: 'https://model.example.com/v1', model_id: 'custom-vision', supports_image: true }));
  const model = models.getModel('custom', 'custom-vision');
  const result = await models.completeSimple(model, { messages: [{ role: 'user', content: [{ type: 'text', text: 'Describe bottle' }, { type: 'image', data: 'aGVsbG8=', mimeType: 'image/png' }], timestamp: 1 }] }, { maxRetries: 0, fetch: async (input, init) => {
    assert.equal(String(input), 'https://model.example.com/v1/chat/completions');
    assert.equal(new Headers(init.headers).get('authorization'), 'Bearer test-only-custom-key');
    assert.equal(JSON.parse(init.body).model, 'custom-vision');
    return completionResponse();
  } });
  assert.equal(result.stopReason, 'stop');
});

test('Codex credential check never sends subscription token to another provider', async () => {
  let calls = 0;
  const result = await operation({ op: 'check', connection: { id: 'codex-check', user_id: 'test-user', version: 1, provider: 'openai-codex', credential: { type: 'oauth', access: 'test-only-access', refresh: 'test-only-refresh', expires: Date.now() + 600000, accountId: 'test-only-account' } } }, AbortSignal.timeout(5000), async () => { calls++; throw new Error('No network allowed'); });
  assert.equal(result.verification, 'auth_resolved');
  assert.equal(calls, 0);
});

test('Codex text calls use the observed HTTP transport without opening a WebSocket', async t => {
  const websocket = t.mock.method(globalThis, 'WebSocket', function forbiddenSocket() {
    throw new Error('Unexpected WebSocket request');
  });
  const access = ['e30', Buffer.from(JSON.stringify({
    'https://api.openai.com/auth': { chatgpt_account_id: 'test-only-account' },
  })).toString('base64'), 'test-only-signature'].join('.');
  let calls = 0;
  const result = await operation({
    op: 'enhance', model: 'gpt-6-luna', prompt: 'A blue bottle',
    connection: { id: 'codex-http', user_id: 'test-user', version: 1, provider: 'openai-codex',
      credential: { type: 'oauth', access, refresh: 'test-only-refresh', expires: Date.now() + 600000, accountId: 'test-only-account' },
    },
  }, AbortSignal.timeout(5000), async (url, init) => {
    calls++;
    assert.equal(new URL(url).hostname, 'chatgpt.com');
    assert.equal(new Headers(init.headers).get('authorization'), 'Bearer ' + access);
    return new Response(JSON.stringify({ error: { message: 'test-only unauthorized response' } }), { status: 401 });
  });
  assert.equal(calls, 1);
  assert.equal(websocket.mock.callCount(), 0);
  assert.equal(result.code, 'credentials');
  assert.ok(!JSON.stringify(result).includes(access));
});

for (const [networkCode, expected] of [['UND_ERR_CONNECT_TIMEOUT', 'provider_timeout'], ['ENOTFOUND', 'provider_network_error']]) {
  test(`text transport reports ${networkCode} without exposing credentials or retrying`, async () => {
    let calls = 0;
    const result = await operation({
      op: 'enhance', prompt: 'A blue bottle', model: 'deepseek-flash',
      connection: { ...command().connection, id: 'text-network-' + networkCode, provider: 'deepseek' },
    }, AbortSignal.timeout(5000), async () => {
      calls++;
      throw new TypeError('test-only private network detail', { cause: { code: networkCode } });
    });
    assert.equal(calls, 1);
    assert.equal(result.code, expected);
    assert.equal(result.uncertain, false);
    assert.ok(!JSON.stringify(result).includes('test-only private network detail'));
  });
}
