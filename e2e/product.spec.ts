import { expect, test, Page } from '@playwright/test';
import path from 'node:path';

async function register(page: Page) {
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '创建账号', exact: true }).click();
  await dialog.getByLabel('你的名字').fill('测试创作者');
  await dialog.getByLabel('邮箱', { exact: true }).fill('qa-' + crypto.randomUUID() + '@example.com');
  await dialog.getByLabel('密码', { exact: true }).fill('test-only-browser-password');
  await dialog.getByRole('button', { name: '创建账号', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole('button', { name: '账号菜单' })).toBeVisible();
}

test('studio opens on working controls with three honest template images', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/studio');
  await expect(page.getByRole('heading', { name: '创作工作台.' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '画面描述' })).toBeVisible();
  await expect(page.getByRole('button', { name: /^查看灵感：/ })).toHaveCount(3);
  await expect(page.getByRole('button', { name: '生图服务待连接' })).toBeVisible();
  await page.waitForFunction(() => Array.from(document.images).every(image => image.complete && image.naturalWidth > 0));
  expect(errors).toEqual([]);
  await page.screenshot({ path: 'docs/screenshot-desktop.png', fullPage: true });
});

test('inspiration fills the real draft; controls and refresh preserve changes', async ({ page }) => {
  await page.goto('/studio');
  await page.getByRole('button', { name: '查看灵感：蓝调香氛' }).click();
  await expect(page.getByRole('dialog')).toContainText('这是一张预置的灵感示例');
  await page.getByRole('button', { name: '使用这个描述' }).click();
  await expect(page.getByRole('textbox', { name: '画面描述' })).toHaveValue(/钴蓝色/);
  await page.getByRole('button', { name: '3:2', exact: true }).click();
  await page.getByLabel('输出画质').selectOption('low');
  await page.getByRole('checkbox', { name: /智能优化描述/ }).uncheck();
  await page.reload();
  await expect(page.getByRole('textbox', { name: '画面描述' })).toHaveValue(/钴蓝色/);
  await expect(page.getByRole('button', { name: '3:2', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByLabel('输出画质')).toHaveValue('low');
  await expect(page.getByRole('checkbox', { name: /智能优化描述/ })).not.toBeChecked();
});

test('registration, session refresh and logout use the real API', async ({ page }) => {
  await page.goto('/studio');
  await register(page);
  await page.reload();
  await expect(page.getByRole('button', { name: '账号菜单' })).toBeVisible();
  await page.getByRole('button', { name: '我的作品', exact: true }).click();
  await expect(page.getByRole('heading', { name: '这里将收藏你的每一次想象' })).toBeVisible();
  await page.getByRole('button', { name: '账号菜单' }).click();
  await page.getByRole('button', { name: '退出登录' }).click();
  await expect(page.getByRole('button', { name: '登录账号', exact: true })).toBeVisible();
});

test('reference upload is private and missing provider never creates a fake result', async ({ page }) => {
  await page.goto('/studio');
  await register(page);
  await page.getByLabel('选择参考图片').setInputFiles(path.resolve('public/examples/editorial.png'));
  await expect(page.getByRole('button', { name: /参考图片已添加/ })).toBeVisible();
  await page.getByRole('textbox', { name: '画面描述' }).fill('保持香水瓶主体，只把背景改成森林。');
  await page.getByRole('button', { name: /生成编辑作品/ }).click();
  await expect(page.getByRole('alert')).toContainText('生图服务尚未连接');
  const jobs = await page.evaluate(async () => (await fetch('/api/jobs')).json());
  expect(jobs.items).toEqual([]);
  await page.getByRole('button', { name: '移除', exact: true }).click();
  await expect(page.getByRole('button', { name: /点击上传或拖入图片/ })).toBeVisible();
});

test('mobile workspace, navigation and templates fit without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/studio');
  await page.waitForFunction(() => Array.from(document.images).every(image => image.complete && image.naturalWidth > 0));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', { name: '打开导航' }).click();
  await page.getByRole('button', { name: '任务中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '还没有创作任务' })).toBeVisible();
  await page.getByRole('button', { name: '去创作', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.sidebar')!.getBoundingClientRect().right <= 1);
  await page.screenshot({ path: 'docs/screenshot-mobile.png', fullPage: true });
});
