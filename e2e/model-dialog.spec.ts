import { expect, Page, test } from '@playwright/test';

const vision = { id: 'gpt-6-sol', name: 'GPT-6 Sol', input: ['text', 'image'], type: 'chat' };
const textOnly = { id: 'text-test', name: 'Text Only Test', input: ['text'], type: 'chat' };

async function setup(page: Page, { failFirst = false, manualPrompt = true, staleFirst = false } = {}) {
  const configuration = {
    owner_id: 'test-owner', sdk: 'pi-ai', sdk_version: 'test',
    providers: [
      { id: 'openai', name: 'OpenAI / ChatGPT', api_key: true, oauth: false, subscription: false, chat_models: [vision, textOnly], image_models: [] },
      { id: 'openai-codex', name: 'OpenAI Codex', api_key: false, oauth: true, subscription: true, chat_models: [vision, textOnly], image_models: [] },
      { id: 'deepseek', name: 'DeepSeek', api_key: true, oauth: false, subscription: false, chat_models: [textOnly], image_models: [] },
      { id: 'qwen', name: '通义千问', api_key: true, oauth: false, subscription: false, chat_models: [vision, textOnly], image_models: [] },
      { id: 'custom', name: '自定义接口', api_key: true, oauth: false, subscription: false, chat_models: [], image_models: [] },
    ],
    connections: [] as Record<string, unknown>[],
    preferences: { image_connection_id: null as string | null, image_model: '', text_connection_id: null as string | null, text_model: '' },
    image_ready: false, active_image_model: '', server_default_available: false,
    image_controls: { quality: false, exact_size: false, edit: false },
  };
  let attempts = 0;
  let submitted = '';
  let submittedProvider = '';
  let saved: Record<string, unknown> | null = null;
  let connected = false;
  await page.route('**/api/models/**', async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const respond = (value: unknown) => route.fulfill({ json: value });
    const pending = {
      id: 'flow-' + attempts, provider: 'openai-codex', status: 'pending', message: '等待授权回调',
      stage: 'awaiting_callback', auth_url: 'https://example.com/authorize?state=fresh-' + attempts,
      expires_at: Date.now() / 1000 + 600, manual_callback: true, manual_input_kind: 'url',
      ...(manualPrompt ? { prompt: { id: 'prompt-' + attempts, type: 'manual_code', message: '粘贴授权码或最终回调地址', placeholder: '粘贴授权码或回调地址' } } : {}),
    };
    if (pathname === '/api/models/config') return respond(configuration);
    if (pathname === '/api/models/oauth' && request.method() === 'POST') {
      if (staleFirst && attempts === 0) {
        attempts += 1;
        return route.fulfill({ status: 409, json: { detail: '已有授权正在进行，请完成或取消后再试' } });
      }
      attempts += 1; connected = false;
      return respond({ ...pending, id: 'flow-' + attempts, auth_url: 'https://example.com/authorize?state=fresh-' + attempts });
    }
    if (/\/oauth\/flow-\d+$/.test(pathname)) {
      if (failFirst && attempts === 1) return respond({ ...pending, status: 'failed', error_code: 'oauth_token_rejected', stage: 'exchanging_token', http_status: 401, retryable: true, auth_url: undefined, manual_callback: false, message: '已收到登录回调，但供应商未接受令牌交换。请重新授权。' });
      return respond(connected ? { ...pending, status: 'connected', stage: 'connected', message: '账号已连接', connection_id: 'new-connection' } : pending);
    }
    if (/\/oauth\/flow-\d+\/(answer|callback)$/.test(pathname)) {
      submitted = request.postDataJSON().answer;
      connected = true;
      configuration.connections = [{ id: 'new-connection', provider: 'openai-codex', name: '我的 Codex', auth_method: 'oauth', hint: '已授权', status: 'authorized', checked_at: null, subscription: true, image_models: [], chat_models: [vision, textOnly] }];
      return respond({ ok: true });
    }
    if (pathname === '/api/models/connections' && request.method() === 'POST') {
      saved = request.postDataJSON();
      submittedProvider = String(saved!.provider);
      const provider = configuration.providers.find(provider => provider.id === submittedProvider)!;
      const models = submittedProvider === 'custom'
        ? [{ id: String(saved!.model_id), name: String(saved!.model_name), input: saved!.supports_image ? ['text', 'image'] : ['text'], type: 'chat' }]
        : provider.chat_models;
      const connection = { id: 'key-connection', provider: submittedProvider, name: String(saved!.name), auth_method: 'api_key', hint: '••••test', status: 'configured', checked_at: null, subscription: false, image_models: [], chat_models: models };
      configuration.connections = [connection];
      return respond(connection);
    }
    if (pathname === '/api/models/preferences' && request.method() === 'PUT') {
      configuration.preferences = request.postDataJSON();
      return respond(configuration);
    }
    if (pathname === '/api/models/oauth/reset' && request.method() === 'POST') return respond({ cancelled: 1 });
    if (pathname.endsWith('/cancel')) return respond({ ok: true });
    return route.abort();
  });
  await page.goto('/studio');
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  const auth = page.getByRole('dialog');
  await auth.getByRole('button', { name: '创建账号', exact: true }).click();
  await auth.getByLabel('你的名字').fill('模型弹窗测试');
  await auth.getByLabel('邮箱', { exact: true }).fill('dialog-' + crypto.randomUUID() + '@example.com');
  await auth.getByLabel('密码', { exact: true }).fill('test-only-dialog-password');
  await auth.getByRole('button', { name: '创建账号', exact: true }).click();
  await expect(auth).not.toBeVisible();
  await page.getByRole('button', { name: '模型与订阅', exact: true }).click();
  await expect(page.locator('.provider-card')).toHaveCount(configuration.providers.length);
  return { configuration, attempts: () => attempts, submitted: () => submitted, saved: () => saved, submittedProvider: () => submittedProvider };
}

test('subscription dialog filters vision models, accepts the current callback and saves the selected model', async ({ page }) => {
  const state = await setup(page);
  await page.locator('.provider-openai-codex').getByRole('button', { name: '添加连接' }).click();
  const dialog = page.getByRole('dialog', { name: '连接模型账号' });
  await page.screenshot({ path: 'docs/screenshot-model-dialog.png', fullPage: true });
  await expect(dialog.getByRole('tab', { name: '订阅登录' })).toHaveAttribute('aria-selected', 'true');
  const models = dialog.getByLabel('视觉模型', { exact: true });
  await expect(models.locator('option')).toHaveCount(1);
  await expect(models).toHaveValue(vision.id);
  await dialog.getByRole('button', { name: '开始账号授权' }).click();
  await expect(dialog.getByRole('link', { name: '打开供应商授权页面' })).toHaveAttribute('href', /fresh-1$/);
  const callback = 'http://localhost:1455/auth/callback?code=test-only-code&state=fresh-1';
  await dialog.getByLabel('授权码或回调地址', { exact: true }).fill(callback);
  await dialog.getByRole('button', { name: '提交授权信息' }).click();
  await expect(dialog).toContainText('账号已连接');
  expect(state.submitted()).toBe(callback);
  await dialog.getByRole('button', { name: '保存所选模型' }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.configuration.preferences.text_model).toBe(vision.id);
  expect(state.configuration.preferences.text_connection_id).toBe('new-connection');
  expect(state.configuration.preferences.image_connection_id).toBeNull();
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  expect(storage).not.toContain('test-only-code');
});

test('failed authorization hides the stale link and retries with a fresh session', async ({ page }) => {
  const state = await setup(page, { failFirst: true });
  await page.locator('.provider-openai-codex').getByRole('button', { name: '添加连接' }).click();
  const dialog = page.getByRole('dialog', { name: '连接模型账号' });
  await dialog.getByRole('button', { name: '开始账号授权' }).click();
  await expect(dialog).toContainText('HTTP 401');
  await expect(dialog.getByRole('link', { name: '打开供应商授权页面' })).toHaveCount(0);
  await expect(dialog.getByLabel('授权码或回调地址', { exact: true })).toHaveCount(0);
  await dialog.getByRole('button', { name: '重新授权' }).click();
  await expect(dialog.getByRole('link', { name: '打开供应商授权页面' })).toHaveAttribute('href', /fresh-2$/);
  expect(state.attempts()).toBe(2);
});

test('stale authorization can be cleared and restarted from the dialog', async ({ page }) => {
  const state = await setup(page, { staleFirst: true });
  await page.locator('.provider-openai-codex').getByRole('button', { name: '添加连接' }).click();
  const dialog = page.getByRole('dialog', { name: '连接模型账号' });
  await dialog.getByRole('button', { name: '开始账号授权' }).click();
  await expect(dialog).toContainText('已有授权正在进行');
  await dialog.getByRole('button', { name: '清理旧授权并重新开始' }).click();
  await expect(dialog.getByRole('link', { name: '打开供应商授权页面' })).toHaveAttribute('href', /fresh-2$/);
  expect(state.attempts()).toBe(2);
});

test('manual input remains available while the backend is waiting for an authorization prompt', async ({ page }) => {
  await setup(page, { manualPrompt: false });
  await page.locator('.provider-openai-codex').getByRole('button', { name: '添加连接' }).click();
  const dialog = page.getByRole('dialog', { name: '连接模型账号' });
  await dialog.getByRole('button', { name: '开始账号授权' }).click();
  await expect(dialog.getByLabel('授权码或回调地址', { exact: true })).toBeVisible();
  await expect(dialog).toContainText('等待授权接收准备完成');
  await dialog.getByLabel('授权码或回调地址', { exact: true }).fill('test-only-code');
  await expect(dialog.getByRole('button', { name: '提交授权信息' })).toBeDisabled();
});

test('DeepSeek, Qwen and custom API settings are selectable and save private credentials', async ({ page }) => {
  const state = await setup(page);
  await page.locator('.provider-deepseek').getByRole('button', { name: '添加连接' }).click();
  const dialog = page.getByRole('dialog', { name: '连接模型账号' });
  await expect(dialog.getByRole('tab')).toHaveCount(3);
  await expect(dialog.getByLabel('模型服务商', { exact: true })).toHaveValue('deepseek');
  await expect(dialog.getByLabel('文本模型', { exact: true })).toHaveValue(textOnly.id);
  await dialog.getByLabel('模型服务商', { exact: true }).selectOption('qwen');
  await expect(dialog.getByLabel('视觉模型', { exact: true })).toHaveValue(vision.id);
  await dialog.getByRole('tab', { name: '自定义接口' }).click();
  await dialog.getByLabel('连接名称').fill('自定义视觉服务');
  await dialog.getByLabel('API Key', { exact: true }).fill('test-only-custom-key');
  await dialog.getByLabel('接口地址', { exact: true }).fill('https://example.com/v1');
  await dialog.getByLabel('模型 ID', { exact: true }).fill('custom-vision');
  await dialog.getByLabel('模型名称', { exact: true }).fill('Custom Vision');
  await dialog.getByRole('checkbox', { name: '支持图片输入（视觉理解）' }).check();
  await dialog.getByRole('button', { name: '保存连接' }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.saved()).toMatchObject({ provider: 'custom', base_url: 'https://example.com/v1', model_id: 'custom-vision', model_name: 'Custom Vision', supports_image: true });
  expect(state.submittedProvider()).toBe('custom');
  await expect(page.locator('.connection-row')).toContainText('自定义视觉服务');
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain('test-only-custom-key');
});

test('configuration tabs support keyboard navigation and fit on a narrow screen', async ({ page }) => {
  await setup(page);
  await page.setViewportSize({ width: 360, height: 740 });
  await page.locator('.provider-openai-codex').getByRole('button', { name: '添加连接' }).click();
  const dialog = page.getByRole('dialog', { name: '连接模型账号' });
  await dialog.getByRole('tab', { name: '订阅登录' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(dialog.getByRole('tab', { name: 'API Key' })).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('ArrowRight');
  await expect(dialog.getByRole('tab', { name: '自定义接口' })).toHaveAttribute('aria-selected', 'true');
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
});
