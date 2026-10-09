import { request as httpsRequest } from 'node:https';
import { BlockList, isIP } from 'node:net';
import { Readable } from 'node:stream';

// Python resolves and validates every operation. Pin that result here so DNS
// cannot change between validation and credential dispatch. TLS still verifies
// the original hostname, and redirects never receive credentials.
const denied = new BlockList();
for (const [address, prefix] of [
  ['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10], ['127.0.0.0', 8],
  ['169.254.0.0', 16], ['172.16.0.0', 12], ['192.0.0.0', 24], ['192.0.2.0', 24],
  ['192.88.99.0', 24], ['192.168.0.0', 16], ['198.18.0.0', 15],
  ['198.51.100.0', 24], ['203.0.113.0', 24], ['224.0.0.0', 4], ['240.0.0.0', 4],
]) denied.addSubnet(address, prefix, 'ipv4');
denied.addSubnet('2001::', 32, 'ipv6');
denied.addSubnet('2002::', 16, 'ipv6');
denied.addSubnet('2001:db8::', 32, 'ipv6');

function publicAddress(address) {
  const family = isIP(address);
  if (family === 4) return !denied.check(address, 'ipv4');
  if (family === 6) {
    const globals = new BlockList();
    globals.addSubnet('2000::', 3, 'ipv6');
    return globals.check(address, 'ipv6') && !denied.check(address, 'ipv6');
  }
  return false;
}

export function createPinnedFetch(connection, requestImpl = httpsRequest) {
  const base = new URL(connection.base_url);
  const addresses = connection.endpoint_ips;
  if (base.protocol !== 'https:' || base.username || base.password || base.search || base.hash ||
      !Array.isArray(addresses) || !addresses.length || !addresses.every(publicAddress)) {
    throw new Error('Invalid custom transport');
  }
  const pinned = addresses[0];
  const family = isIP(pinned);
  const hostname = base.hostname.replace(/^\[|\]$/g, '');
  return async (input, init) => {
    const incoming = new Request(input, init);
    const url = new URL(incoming.url);
    if (url.origin !== base.origin || url.username || url.password || url.hash) {
      throw new Error('Custom transport origin mismatch');
    }
    if (incoming.signal.aborted) throw incoming.signal.reason;
    const body = incoming.body ? Buffer.from(await incoming.arrayBuffer()) : undefined;
    if (body && body.length > 32 * 1024 * 1024) throw new Error('Custom request too large');
    const headers = Object.fromEntries(incoming.headers);
    headers.host = url.host;
    headers['accept-encoding'] = 'identity';
    if (body) headers['content-length'] = String(body.length);
    return new Promise((resolve, reject) => {
      const request = requestImpl({
        protocol: 'https:', hostname, port: url.port || 443,
        servername: isIP(hostname) ? undefined : hostname,
        path: url.pathname + url.search, method: incoming.method, headers,
        agent: false, rejectUnauthorized: true,
        lookup: (_name, options, callback) => {
          if (options?.all) callback(null, [{ address: pinned, family }]);
          else callback(null, pinned, family);
        },
      }, response => {
        const status = response.statusCode || 502;
        if (status >= 300 && status < 400) {
          response.destroy(); reject(new Error('Custom redirect rejected')); return;
        }
        const responseHeaders = new Headers();
        for (let index = 0; index < response.rawHeaders.length; index += 2) {
          responseHeaders.append(response.rawHeaders[index], response.rawHeaders[index + 1]);
        }
        const empty = [204, 205, 304].includes(status) || incoming.method === 'HEAD';
        if (empty) response.resume();
        resolve(new Response(empty ? null : Readable.toWeb(response), { status, headers: responseHeaders }));
      });
      const abort = () => request.destroy(new Error('Custom request cancelled'));
      incoming.signal.addEventListener('abort', abort, { once: true });
      request.once('error', reject);
      request.once('close', () => incoming.signal.removeEventListener('abort', abort));
      if (incoming.signal.aborted) abort();
      else request.end(body);
    });
  };
}
