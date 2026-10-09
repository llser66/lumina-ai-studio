import assert from 'node:assert/strict';
import { test } from 'node:test';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import { createPinnedFetch } from './pinned-fetch.mjs';

const connection = { base_url: 'https://model.example.com/v1', endpoint_ips: ['8.8.8.8'] };
function transport(status = 200) {
  let options, body;
  const request = (value, callback) => {
    options = value;
    const req = new EventEmitter();
    req.end = input => {
      body = input;
      const response = Readable.from([Buffer.from('{"ok":true}')]);
      response.statusCode = status;
      response.rawHeaders = ['content-type', 'application/json', 'Location', 'https://other.example.com'];
      callback(response);
    };
    req.destroy = error => { req.emit('error', error); req.emit('close'); };
    return req;
  };
  return { request, options: () => options, body: () => body };
}

test('custom HTTPS pins vetted address while verifying original hostname', async () => {
  const mock = transport();
  const fetch = createPinnedFetch(connection, mock.request);
  const result = await fetch(connection.base_url + '/chat/completions', { method: 'POST', headers: { Authorization: 'Bearer test-only-custom-key' }, body: '{"model":"vision-test"}' });
  const options = mock.options();
  assert.equal(options.hostname, 'model.example.com');
  assert.equal(options.servername, 'model.example.com');
  assert.equal(options.headers.host, 'model.example.com');
  assert.equal(options.rejectUnauthorized, true);
  assert.equal(options.agent, false);
  options.lookup('model.example.com', {}, (_error, address, family) => { assert.equal(address, '8.8.8.8'); assert.equal(family, 4); });
  assert.equal(mock.body().toString(), '{"model":"vision-test"}');
  assert.deepEqual(await result.json(), { ok: true });
});

test('custom transport blocks credential dispatch to another origin', async () => {
  const mock = transport();
  await assert.rejects(createPinnedFetch(connection, mock.request)('https://other.example.com/v1', { headers: { Authorization: 'Bearer test-only-key' } }), /origin mismatch/);
  assert.equal(mock.options(), undefined);
});

test('custom transport rejects redirects without following them', async () => {
  const mock = transport(302);
  await assert.rejects(createPinnedFetch(connection, mock.request)(connection.base_url + '/models'), /redirect rejected/);
});

test('custom transport rejects missing or nonpublic pinned addresses', () => {
  for (const addresses of [undefined, [], ['127.0.0.1'], ['8.8.8.8', '10.0.0.1'], ['::1'], ['2001:db8::1'], ['invalid']]) {
    assert.throws(() => createPinnedFetch({ ...connection, endpoint_ips: addresses }), /Invalid custom transport/);
  }
});
