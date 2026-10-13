import { writeFile } from 'node:fs/promises';
import { createModels } from '@earendil-works/pi-ai';
import { factories } from './provider-factories.mjs';

export { factories } from './provider-factories.mjs';
export function modelInfo(model) {
  return { id: model.id, name: model.name, input: model.input, type: model.type || 'chat', reasoning: !!model.reasoning };
}
export function catalog() {
  const models = createModels({ authContext: { env: async () => undefined, fileExists: async () => false } });
  for (const factory of Object.values(factories)) models.setProvider(factory());
  const providers = models.getProviders().map(provider => ({
    id: provider.id, name: provider.name, api_key: !!provider.auth.apiKey,
    oauth: !!provider.auth.oauth, subscription: !!provider.auth.oauth?.isSubscription,
    login_label: provider.auth.oauth?.loginLabel || provider.auth.oauth?.name,
    chat_models: models.getModels(provider.id).map(modelInfo),
    image_models: models.getModelsOfType('image', provider.id).map(modelInfo),
  }));
  providers.push({
    id: 'cloudflare', name: 'Cloudflare Workers AI', api_key: true, oauth: false, subscription: false,
    chat_models: [{ id: '@cf/meta/llama-3.1-8b-instruct', name: 'Llama 3.1 8B · 文本', input: ['text'], type: 'chat', reasoning: false }],
    image_models: [{ id: '@cf/black-forest-labs/flux-1-schnell', name: 'FLUX.1 schnell', input: ['text'], type: 'image', reasoning: false }],
  });
  return providers;
}
if (process.argv[1]?.endsWith('catalog.mjs')) {
  const output = { sdk: '@earendil-works/pi-ai', version: '1.0.4', providers: catalog() };
  await writeFile(new URL('./catalog.json', import.meta.url), JSON.stringify(output, null, 2) + '\n');
  console.log('pi-ai capability catalog written without credential resolution.');
}
