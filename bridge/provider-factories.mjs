import { createProvider, envApiKeyAuth } from '@earendil-works/pi-ai';
import { openAICompletionsApi } from '@earendil-works/pi-ai/api/openai-completions.lazy';
import { openaiProvider } from '@earendil-works/pi-ai/providers/openai';
import { openrouterProvider } from '@earendil-works/pi-ai/providers/openrouter';
import { anthropicProvider } from '@earendil-works/pi-ai/providers/anthropic';
import { googleProvider } from '@earendil-works/pi-ai/providers/google';
import { githubCopilotProvider } from '@earendil-works/pi-ai/providers/github-copilot';
import { deepseekProvider } from '@earendil-works/pi-ai/providers/deepseek';
import { openaiCodexProvider } from '@earendil-works/pi-ai/providers/openai-codex';

export const QWEN_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1';
// Ordinary 百炼 API keys are distinct from the SDK's qwen-token-plan-* keys.
// This bounded catalog uses Alibaba's documented models and OpenAI-compatible API.
// SDK accounting values are placeholders, never a Lumina billing estimate.
const unusedCost = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
const compat = {
  supportsStore: false, supportsDeveloperRole: false, supportsReasoningEffort: false,
  maxTokensField: 'max_tokens', thinkingFormat: 'qwen',
};
function chatModel(provider, baseUrl, id, name, supportsImage, contextWindow, maxTokens, reasoning = false) {
  return {
    provider, baseUrl, id, name, api: 'openai-completions', type: 'chat',
    input: supportsImage ? ['text', 'image'] : ['text'], reasoning,
    contextWindow, maxTokens, cost: { ...unusedCost }, compat: { ...compat },
  };
}

export function qwenProvider(connection = {}) {
  const baseUrl = connection.base_url || QWEN_BASE_URL;
  const model = (id, name, image, window, max, reasoning) =>
    chatModel('qwen', baseUrl, id, name, image, window, max, reasoning);
  return createProvider({
    id: 'qwen', name: '通义千问 / 阿里云百炼', baseUrl,
    auth: { apiKey: envApiKeyAuth('百炼 / DashScope API Key', ['DASHSCOPE_API_KEY']) },
    models: [
      model('qwen-vl-plus', 'Qwen VL Plus · 视觉', true, 131072, 8192),
      model('qwen-vl-max', 'Qwen VL Max · 视觉', true, 131072, 8192),
      model('qwen-plus', 'Qwen Plus', false, 1000000, 32768, true),
      model('qwen-turbo', 'Qwen Turbo', false, 131072, 16384, true),
      model('qwen-max', 'Qwen Max', false, 32768, 8192),
    ],
    api: openAICompletionsApi(),
  });
}

export function customProvider(connection = {}) {
  const modelId = connection.model_id || '';
  const baseUrl = connection.base_url || 'https://invalid.example';
  if (connection.base_url) {
    const endpoint = new URL(connection.base_url);
    if (endpoint.protocol !== 'https:' || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
      throw new Error('Invalid custom endpoint');
    }
  }
  const model = chatModel(
    'custom', baseUrl, modelId, connection.model_name || modelId,
    !!connection.supports_image, 32768, 4096,
  );
  // Custom APIs do not implicitly support Qwen's reasoning parameters.
  model.compat = { ...compat, thinkingFormat: 'openai', supportsUsageInStreaming: false };
  return createProvider({
    id: 'custom', name: '自定义 OpenAI 兼容接口', baseUrl,
    auth: { apiKey: envApiKeyAuth('自定义接口 API Key', []) },
    models: modelId ? [model] : [], api: openAICompletionsApi(),
  });
}

export const factories = {
  openai: openaiProvider, 'openai-codex': openaiCodexProvider,
  openrouter: openrouterProvider, anthropic: anthropicProvider,
  google: googleProvider, 'github-copilot': githubCopilotProvider,
  deepseek: deepseekProvider, qwen: qwenProvider, custom: customProvider,
};

export function createRuntimeProvider(connection) {
  const factory = factories[connection.provider];
  if (!factory) throw new Error('Provider not supported');
  return connection.provider === 'custom' || connection.provider === 'qwen' ? factory(connection) : factory();
}
