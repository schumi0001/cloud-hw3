const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '../public');
const output = path.resolve(__dirname, '../../artifacts');
fs.mkdirSync(output, { recursive: true });
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64');
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript' };

const server = http.createServer((request, response) => {
  const pathname = new URL(request.url, 'http://localhost').pathname;
  if (pathname === '/config.js') {
    response.writeHead(200, { 'Content-Type': 'application/javascript' });
    response.end('window.PHOTO_ALBUM_CONFIG = { apiKey: "TEST_ONLY" };');
    return;
  }
  const file = path.resolve(root, '.' + (pathname === '/' ? '/index.html' : pathname));
  if (!file.startsWith(root + path.sep)) {
    response.writeHead(403);
    response.end();
    return;
  }
  fs.readFile(file, (error, data) => {
    response.writeHead(error ? 404 : 200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' });
    response.end(error ? '' : data);
  });
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ||
      (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);
    browser = await chromium.launch({ headless: true, executablePath });
    const page = await browser.newPage({ viewport: { width: 1280, height: 940 } });
    const errors = [];
    const queries = [];
    const uploads = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('https://mock-photo.test/**', route =>
      route.fulfill({ status: 200, contentType: 'image/png', body: png }));
    await page.route('https://ilaz71kqzd.execute-api.us-east-1.amazonaws.com/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const headers = {
        'access-control-allow-origin': '*',
        'access-control-allow-methods': 'GET,PUT,OPTIONS',
        'access-control-allow-headers': 'content-type,x-api-key,x-amz-meta-customLabels',
        'content-type': 'application/json'
      };
      if (request.method() === 'OPTIONS') {
        await route.fulfill({ status: 200, headers, body: '' });
        return;
      }
      assert.equal(request.headers()['x-api-key'], 'TEST_ONLY');
      if (request.method() === 'PUT') {
        uploads.push({ url, headers: request.headers(), body: request.postDataBuffer() });
        const rejected = url.searchParams.get('filename').includes('reject');
        await route.fulfill({
          status: rejected ? 400 : 200, headers,
          body: rejected ? JSON.stringify({ message: 'S3 rejected the upload.' }) : ''
        });
        return;
      }
      const query = url.searchParams.get('q');
      queries.push(query);
      if (query === 'service-error') {
        await route.fulfill({ status: 500, headers, body: JSON.stringify({ message: 'Photo search failed.' }) });
        return;
      }
      const results = query === 'horses' ? [
        { url: 'https://mock-photo.test/horse.png', labels: ['horse', 'sam', 'sally'] }
      ] : query === 'horses and cars' ? [
        { url: 'https://mock-photo.test/horse.png', labels: ['horse'] },
        { url: 'https://mock-photo.test/car.png', labels: ['car'] }
      ] : [];
      await route.fulfill({ status: 200, headers, body: JSON.stringify({ results }) });
    });

    await page.goto('http://127.0.0.1:' + server.address().port);
    assert.equal(await page.locator('h1').textContent(), 'Photo Album');
    await page.screenshot({ path: path.join(output, 'frontend-desktop.png'), fullPage: true });

    async function search(text) {
      await page.locator('#query').fill(text);
      await page.locator('#search-button').click();
      await page.waitForFunction(() => !document.getElementById('search-button').disabled);
    }
    async function upload(name, labels, mimeType = 'image/png', buffer = png) {
      await page.locator('#photo-file').setInputFiles({ name, mimeType, buffer });
      await page.locator('#custom-labels').fill(labels);
      await page.locator('#upload-button').click();
      await page.waitForFunction(() => !document.getElementById('upload-button').disabled);
    }

    await search('horses');
    assert.equal(await page.locator('#results figure').count(), 1);
    assert.match(await page.locator('figcaption').textContent(), /horse, sam, sally/);
    assert.equal(await page.locator('#results a').getAttribute('target'), '_blank');
    await search('horses and cars');
    assert.equal(await page.locator('#results figure').count(), 2);
    await search('show me sam & sally');
    assert.equal(queries.at(-1), 'show me sam & sally');
    assert.equal(await page.locator('#search-status').textContent(), 'No photos found.');
    await search('service-error');
    assert.equal(await page.locator('#search-status').textContent(), 'Photo search failed.');

    await upload('horse space.png', ' Sam, Sally, Sam, , ');
    assert(uploads.at(-1).body.equals(png), 'Image bytes changed during upload');
    assert.equal(uploads.at(-1).headers['x-amz-meta-customlabels'], 'Sam, Sally');
    assert.equal(uploads.at(-1).headers['content-type'], 'image/png');
    assert(uploads.at(-1).url.searchParams.get('filename').endsWith('horse space.png'));
    assert.match(await page.locator('#upload-status').textContent(), /Upload complete/);

    await upload('no-labels.png', '');
    assert(uploads.at(-1).body.equals(png));
    assert.equal(uploads.at(-1).headers['x-amz-meta-customlabels'] || '', '');
    await upload('reject.png', '');
    assert.equal(await page.locator('#upload-status').textContent(), 'S3 rejected the upload.');

    const count = uploads.length;
    await upload('notes.txt', '', 'text/plain', Buffer.from('not a photo'));
    assert.equal(await page.locator('#upload-status').textContent(), 'Choose a JPG or PNG image.');
    assert.equal(uploads.length, count);

    await page.setViewportSize({ width: 390, height: 844 });
    await search('horses and cars');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(output, 'frontend-mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    console.log('PASS: search, results, query encoding, uploads with/without labels, exact image bytes, errors, file validation and mobile layout.');
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch(error => {
  console.error(error);
  server.close();
  process.exitCode = 1;
});
