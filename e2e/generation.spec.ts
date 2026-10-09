import { expect, Page, test } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

async function signUp(page: Page) {
  await page.goto('/studio');
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '创建账号', exact: true }).click();
  await dialog.getByLabel('你的名字').fill('流程测试');
  await dialog.getByLabel('邮箱', { exact: true }).fill('pipeline-' + crypto.randomUUID() + '@example.com');
  await dialog.getByLabel('密码', { exact: true }).fill('test-only-pipeline-password');
  await dialog.getByRole('button', { name: '创建账号', exact: true }).click();
  await expect(dialog).not.toBeVisible();
}

test('full generation, private download, favorite, edit and task timeline', async ({ page }) => {
  await signUp(page);
  const prompt = 'A blue glass bottle, carefully lit on a mint pedestal';
  await page.getByRole('textbox', { name: '画面描述' }).fill(prompt);
  await page.getByRole('button', { name: /开始生成/ }).click();
  const card = page.locator('.art-card').filter({ has: page.getByRole('button', { name: '查看作品：' + prompt }) });
  await expect(card).toContainText('已完成', { timeout: 15000 });
  await card.getByRole('button', { name: '收藏作品', exact: true }).click();
  await card.getByRole('button', { name: '查看作品：' + prompt }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('作品已保存');
  const downloadPromise = page.waitForEvent('download');
  await dialog.getByRole('link', { name: '下载原图' }).click();
  const download = await downloadPromise;
  const file = await download.path();
  expect((await fs.readFile(file!)).subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  await dialog.getByRole('button', { name: '基于此图编辑' }).click();
  await expect(page.getByRole('button', { name: /参考图片已添加/ })).toBeVisible();
  await page.getByRole('textbox', { name: '画面描述' }).fill('Keep the glass bottle, change the background to a forest');
  await page.getByRole('button', { name: /生成编辑作品/ }).click();
  await expect(page.locator('.art-card').filter({ hasText: 'Keep the glass bottle' })).toContainText('已完成', { timeout: 15000 });
  await page.getByRole('button', { name: '收藏夹', exact: true }).click();
  await expect(page.locator('.art-card')).toHaveCount(1);
  await page.getByRole('button', { name: '任务中心', exact: true }).click();
  await expect(page.locator('.task-row')).toHaveCount(2);
});

test('lost acknowledgement followed by reload reuses the original idempotency key', async ({ page }) => {
  await signUp(page);
  const prompt = 'A small calm garden in morning light';
  await page.getByLabel('选择参考图片').setInputFiles(path.resolve('public/examples/editorial.png'));
  await expect(page.getByRole('button', { name: /参考图片已添加/ })).toBeVisible();
  await page.getByRole('textbox', { name: '画面描述' }).fill(prompt);
  await page.getByRole('button', { name: '排除内容', exact: true }).click();
  await page.getByRole('textbox', { name: '不希望出现的内容' }).fill('watermarks and typography');
  let intercept = true;
  await page.route('**/api/jobs', async route => {
    if (intercept && route.request().method() === 'POST') {
      intercept = false;
      await route.fetch();
      await route.abort('failed');
    } else await route.continue();
  });
  await page.getByRole('button', { name: /生成编辑作品/ }).click();
  await expect(page.getByRole('alert')).toContainText('暂时无法连接服务');
  await page.reload();
  await expect(page.getByRole('button', { name: '账号菜单' })).toBeVisible();
  await expect(page.getByRole('button', { name: /参考图片已添加/ })).toBeVisible();
  await page.getByRole('button', { name: /生成编辑作品/ }).click();
  await expect(page.locator('.art-card')).toHaveCount(1);
  await expect(page.locator('.art-card')).toContainText('已完成', { timeout: 15000 });
  const result = await page.evaluate(async () => (await fetch('/api/jobs')).json());
  expect(result.total).toBe(1);
});

test('a delayed authorized history response cannot restore private data after logout', async ({ page }) => {
  await signUp(page);
  const prompt = 'Private history item for logout isolation';
  await page.getByRole('textbox', { name: '画面描述' }).fill(prompt);
  await page.getByRole('button', { name: /开始生成/ }).click();
  await expect(page.locator('.art-card').filter({ hasText: prompt })).toContainText('已完成', { timeout: 15000 });
  let release!: () => void;
  let captured!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const ready = new Promise<void>(resolve => { captured = resolve; });
  await page.route(url => url.pathname === '/api/jobs', async route => {
    if (route.request().method() !== 'GET') return route.continue();
    const response = await route.fetch();
    captured();
    await held;
    await route.fulfill({ response });
  });
  await page.reload();
  await ready;
  await page.getByRole('button', { name: '账号菜单' }).click();
  await page.getByRole('button', { name: '退出登录' }).click();
  await expect(page.getByRole('button', { name: '登录账号', exact: true })).toBeVisible();
  const response = page.waitForResponse(result => new URL(result.url()).pathname === '/api/jobs');
  release();
  await response;
  await page.getByRole('button', { name: '我的作品', exact: true }).click();
  await expect(page.getByRole('heading', { name: '这里将收藏你的每一次想象' })).toBeVisible();
  await expect(page.locator('.art-card')).toHaveCount(0);
  await expect(page.getByText(prompt, { exact: true })).toHaveCount(0);
});
