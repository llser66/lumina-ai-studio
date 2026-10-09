const MODEL = '@cf/black-forest-labs/flux-1-schnell';

export async function verifyCloudflareToken(connection, signal, requestFetch = fetch) {
  const credential = connection.credential;
  if (!credential?.key || !/^[0-9a-f]{32}$/i.test(credential.account_id || '')) return { ok: false, code: 'credentials' };
  try {
    const verifyUrl = `https://api.cloudflare.com/client/v4/accounts/${credential.account_id}/tokens/verify`;
    const response = await requestFetch(verifyUrl, {
      method: 'GET', headers: { Authorization: 'Bearer ' + credential.key }, signal, redirect: 'error',
    });
    if (!response.ok) {
      await response.body?.cancel();
      return { ok: false, code: response.status === 429 ? 'rate_limit' : 'credentials' };
    }
    const body = await response.json();
    return body?.success === true && body?.result?.status === 'active'
      ? { ok: true, verification: 'token_only' }
      : { ok: false, code: 'credentials' };
  } catch {
    return { ok: false, code: 'provider_unavailable' };
  }
}

export async function generateCloudflareImage(command, signal, requestFetch = fetch) {
  const credential = command.connection.credential;
  if (!credential?.key || !/^[0-9a-f]{32}$/i.test(credential.account_id || '')) return { ok: false, code: 'credentials' };
  if (command.model !== MODEL) return { ok: false, code: 'model_unavailable' };
  if (command.reference) return { ok: false, code: 'edit_unsupported' };
  if (!command.prompt?.trim() || command.prompt.length > 2048) return { ok: false, code: 'prompt_too_long' };
  const url = `https://api.cloudflare.com/client/v4/accounts/${credential.account_id}/ai/run/${MODEL}`;
  let response;
  try {
    response = await requestFetch(url, {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + credential.key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: command.prompt }),
      signal,
      redirect: 'error',
    });
  } catch {
    return { ok: false, code: 'provider_unknown', uncertain: true };
  }
  if (!response.ok) {
    const code = response.status === 401 ? 'credentials'
      : response.status === 403 ? 'provider_access_denied'
      : response.status === 429 ? 'rate_limit'
      : response.status === 408 || response.status >= 500 ? 'provider_unknown'
      : 'provider_unavailable';
    await response.body?.cancel();
    return { ok: false, code, uncertain: code === 'provider_unknown' };
  }
  try {
    const payload = await response.json();
    const image = payload?.result?.image;
    if (payload?.success !== true || typeof image !== 'string' || !image) throw new Error('Invalid image');
    return { ok: true, image, mime_type: 'image/jpeg', request_id: payload?.result?.request_id || null };
  } catch {
    return { ok: false, code: 'invalid_image', uncertain: true };
  }
}
