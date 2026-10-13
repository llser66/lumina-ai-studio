import assert from 'node:assert/strict';
import { test } from 'node:test';
import { operation } from './service.mjs';
import { TEXT_MODEL } from './cloudflare-images.mjs';

const accountId = '0123456789abcdef0123456789abcdef';
const credential = { type: 'api_key', key: 'test-only-cloudflare-token', account_id: accountId };
const connection = { id: 'cloudflare-test', user_id: 'test-user', provider: 'cloudflare', version: 1, credential };
const command = { op: 'image', connection, model: '@cf/black-forest-labs/flux-1-schnell', prompt: 'A blue bottle' };

test('Cloudflare verifies a token without generating or exposing the token', async () => {
  let calls = 0;
  const result = await operation({ op: 'check', connection }, AbortSignal.timeout(5000), async (url, init) => {
    calls++;
    assert.equal(String(url), `https://api.cloudflare.com/client/v4/accounts/${accountId}/tokens/verify`);
    assert.equal(init.method, 'GET');
    assert.equal(new Headers(init.headers).get('authorization'), 'Bearer ' + credential.key);
    return new Response(JSON.stringify({ success: true, result: { status: 'active' } }), { status: 200 });
  });
  assert.deepEqual(result, { ok: true, verification: 'token_only' });
  assert.equal(calls, 1);
});

test('Cloudflare image generation uses official REST endpoint and decodes the response envelope', async () => {
  let calls = 0;
  const result = await operation(command, AbortSignal.timeout(5000), async (url, init) => {
    calls++;
    assert.equal(String(url), `https://api.cloudflare.com/client/v4/accounts/${accountId}/ai/run/@cf/black-forest-labs/flux-1-schnell`);
    assert.equal(init.method, 'POST');
    assert.equal(new Headers(init.headers).get('authorization'), 'Bearer ' + credential.key);
    assert.deepEqual(JSON.parse(init.body), { prompt: 'A blue bottle' });
    return new Response(JSON.stringify({ success: true, result: { image: 'aGVsbG8=' } }), { status: 200 });
  });
  assert.deepEqual(result, { ok: true, image: 'aGVsbG8=', mime_type: 'image/jpeg', request_id: null });
  assert.equal(calls, 1);
});

test('Cloudflare text moderation uses the same token and parses the response envelope', async () => {
  const result = await operation({
    op: 'moderate', connection, model: TEXT_MODEL, prompt: 'A blue bottle on a studio table',
  }, AbortSignal.timeout(5000), async (url, init) => {
    assert.equal(String(url), `https://api.cloudflare.com/client/v4/accounts/${accountId}/ai/run/${TEXT_MODEL}`);
    assert.equal(init.method, 'POST');
    assert.equal(new Headers(init.headers).get('authorization'), 'Bearer ' + credential.key);
    const body = JSON.parse(init.body);
    assert.equal(body.messages.at(-1).content, 'A blue bottle on a studio table');
    assert.equal(body.max_tokens, 100);
    return new Response(JSON.stringify({ success: true, result: { response: '{"allowed":true}' } }), { status: 200 });
  });
  assert.deepEqual(result, { ok: true, allowed: true });
});

test('Cloudflare text enhancement returns the model response', async () => {
  const result = await operation({
    op: 'enhance', connection, model: TEXT_MODEL, prompt: 'A blue bottle',
  }, AbortSignal.timeout(5000), async (_url, init) => {
    const body = JSON.parse(init.body);
    assert.equal(body.max_tokens, 800);
    return new Response(JSON.stringify({ success: true, result: { response: 'A polished blue bottle in soft light' } }), { status: 200 });
  });
  assert.deepEqual(result, { ok: true, text: 'A polished blue bottle in soft light' });
});

test('Cloudflare rejects reference editing and unlisted models without network access', async () => {
  let calls = 0;
  const mock = async () => { calls++; throw new Error('Unexpected request'); };
  assert.equal((await operation({ ...command, reference: 'aGVsbG8=' }, AbortSignal.timeout(5000), mock)).code, 'edit_unsupported');
  assert.equal((await operation({ ...command, model: 'other-model' }, AbortSignal.timeout(5000), mock)).code, 'model_unavailable');
  assert.equal(calls, 0);
});

test('Cloudflare quota and uncertain failures preserve useful error classifications', async () => {
  const quota = await operation(command, AbortSignal.timeout(5000), async () => new Response(JSON.stringify({ errors: [{ code: 3036 }] }), { status: 429 }));
  assert.equal(quota.code, 'rate_limit');
  assert.equal(quota.uncertain, false);
  const network = await operation(command, AbortSignal.timeout(5000), async () => { throw new Error('Network failed'); });
  assert.equal(network.code, 'provider_unknown');
  assert.equal(network.uncertain, true);
});
