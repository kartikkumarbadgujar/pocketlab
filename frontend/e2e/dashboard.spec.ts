import { expect, test } from '@playwright/test'

async function createAccount(page: import('@playwright/test').Page) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: 'Create an account' }).click()
  await page.getByLabel('Work email').fill(`pocketlab-e2e-${Date.now()}@example.com`)
  await page.getByRole('textbox', { name: 'Password' }).fill('pocketlab-e2e-password-123')
  await page.getByRole('button', { name: 'Create account', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.locator('.sidebar')).toBeVisible()
}

test('registration opens the API-backed dashboard and every protected route renders', async ({ page }) => {
  await createAccount(page)

  const routes = [
    ['/', 'Overview'],
    ['/projects', 'Projects'],
    ['/devices', 'Device inventory'],
    ['/profiles', 'Condition profile builder'],
    ['/scenarios', 'Scenario library'],
    ['/test-lab', 'Test lab'],
    ['/runs/999/live', 'Live test monitoring'],
    ['/runs/999', 'Test run details'],
    ['/findings', 'Findings'],
    ['/reports', 'Impact reports'],
    ['/help', 'Help & documentation'],
  ] as const

  for (const [path, title] of routes) {
    await page.goto(path)
    await expect(page.locator('.topbar-current')).toHaveText(title)
    await expect(page.locator('main.page-content')).toBeVisible()
  }

  await page.goto('/projects')
  await page.getByRole('button', { name: 'Create your first project' }).click()
  await page.getByLabel('Project name').fill('E2E Android project')
  await page.getByRole('button', { name: 'Create project' }).click()
  await expect(page.getByRole('heading', { name: 'E2E Android project' })).toBeVisible()

  await page.getByRole('button', { name: 'Upload APK' }).click()
  await page.locator('input[type="file"]').setInputFiles({ name: 'invalid-fixture.apk', mimeType: 'application/vnd.android.package-archive', buffer: Buffer.from('not an APK archive') })
  await page.getByLabel('Package name').fill('com.example.e2e')
  await page.getByRole('button', { name: 'Upload to quarantine' }).click()
  await expect(page.getByRole('alert')).toContainText('not a valid ZIP archive')
  await page.getByRole('button', { name: 'Cancel' }).click()

  await page.reload()
  await expect(page.getByRole('heading', { name: 'E2E Android project' })).toBeVisible()

  await page.goto('/profiles')
  await page.getByRole('button', { name: 'New profile' }).click()
  await page.getByLabel('Profile name').fill('E2E network profile')
  await page.getByLabel('Packet loss target (%)').fill('2.5')
  await page.getByRole('button', { name: 'Create profile', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'E2E network profile' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: /E2E network profile/ })).toBeVisible()
})

test('mobile dock navigates and reduced-motion preference is honored', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await createAccount(page)

  const dock = page.getByRole('navigation', { name: 'Mobile navigation' })
  await expect(dock).toBeVisible()
  await page.getByRole('button', { name: /Normal Baseline/ }).click()
  await page.getByRole('button', { name: 'Configure test' }).click()
  await expect(page).toHaveURL(/\/test-lab\?profileVersionId=\d+/)
  await dock.getByRole('link', { name: 'Test Lab' }).click()
  await expect(page).toHaveURL(/\/test-lab$/)
  await expect(dock.getByRole('link', { name: 'Test Lab' })).toHaveClass(/active/)
  await expect(page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).resolves.toBe(true)

  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }))
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport)
})
