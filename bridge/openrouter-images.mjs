const IMAGE_MODELS_URL = 'https://openrouter.ai/api/v1/images/models';
const IMAGE_GENERATION_URL = 'https://openrouter.ai/api/v1/images';
const CATALOG_TTL_MS = 5 * 60 * 1000;
const catalogs = new Map();

export function clearOpenRouterImageCatalog(cacheKey) {
  if (cacheKey === undefined) catalogs.clear();
  else catalogs.delete(cacheKey);
}

async function imageCatalog({ cacheKey, apiKey, fetch, signal }) {
  const cached = catalogs.get(cacheKey);
  if (cached && cached.expiresAt > Date.now()) return { ok: true, models: cached.models };

  let response;
  try {
    response = await fetch(IMAGE_MODELS_URL, {
      method: 'GET',
      headers: { Authorization: 'Bearer ' + apiKey },
      signal,
      redirect: 'error',
    });
  } catch {
    return { ok: false, billable: false };
  }
  if (!response.ok) {
    await response.body?.cancel();
    return { ok: false, billable: false };
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    return { ok: false, billable: false };
  }
  if (!Array.isArray(payload?.data)) return { ok: false, billable: false };

  const models = new Map(payload.data.filter(model => typeof model?.id === 'string').map(model => [model.id, model]));
  catalogs.set(cacheKey, { expiresAt: Date.now() + CATALOG_TTL_MS, models });
  return { ok: true, models };
}

export async function generateOpenRouterImage({ cacheKey, apiKey, model, prompt, reference, ratio, size, fetch, signal }) {
  const catalog = await imageCatalog({ cacheKey, apiKey, fetch, signal });
  if (!catalog.ok) return catalog;

  const modelInfo = catalog.models.get(model);
  if (!modelInfo) return { ok: false, code: 'model_unavailable', billable: false };

  const supported = modelInfo.supported_parameters || {};
  if (!reference && supported.input_references?.type === 'range' && supported.input_references.min >= 1) {
    return { ok: false, code: 'reference_required', billable: false };
  }
  if (reference && !(supported.input_references?.type === 'range' && supported.input_references.max >= 1)) {
    return { ok: false, code: 'edit_unsupported', billable: false };
  }
  const body = { model, prompt };
  if (supported.n?.type === 'range' && supported.n.min <= 1 && supported.n.max >= 1) body.n = 1;
  if (supported.aspect_ratio?.type === 'enum' && supported.aspect_ratio.values.includes(ratio)) {
    body.aspect_ratio = ratio;
  }
  if (supported.size?.type === 'boolean' && ['1024x1024', '1536x1024', '1024x1536'].includes(size)) {
    body.size = size;
  }
  if (supported.output_format?.type === 'enum' && supported.output_format.values.includes('png')) {
    body.output_format = 'png';
  }
  if (reference && supported.input_references?.type === 'range' && supported.input_references.max >= 1) {
    body.input_references = [{
      type: 'image_url',
      image_url: { url: 'data:image/png;base64,' + reference },
    }];
  }

  let response;
  try {
    response = await fetch(IMAGE_GENERATION_URL, {
      method: 'POST',
      headers: {
        Authorization: 'Bearer ' + apiKey,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal,
      redirect: 'error',
    });
  } catch {
    return { ok: false, billable: true, uncertain: true };
  }
  if (!response.ok) {
    await response.body?.cancel();
    return { ok: false, billable: true };
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    return { ok: false, code: 'invalid_image', billable: true, uncertain: true };
  }
  const image = payload?.data?.[0];
  if (typeof image?.b64_json !== 'string' || !image.b64_json) {
    return { ok: false, code: 'invalid_image', billable: true, uncertain: true };
  }
  return {
    ok: true,
    image: image.b64_json,
    mime_type: image.media_type || 'image/png',
    request_id: payload.id || payload.request_id || null,
  };
}
