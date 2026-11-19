const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../public');
const calls = [];
const context = vm.createContext({
  Blob, File, ArrayBuffer, Uint8Array,
  axios: request => { calls.push(request); return Promise.resolve({ status: 200, data: {} }); }
});
function load(name) {
  vm.runInContext(fs.readFileSync(path.join(root, 'sdk', name), 'utf8'), context, { filename: name });
}
load('lib/apiGatewayCore/utils.js');
load('lib/apiGatewayCore/simpleHttpClient.js');
// No AWS_IAM credentials are used by this website; isolate only that unused client.
context.apiGateway.core.sigV4ClientFactory = { newClient: () => ({}) };
load('lib/apiGatewayCore/apiGatewayClient.js');
load('lib/url-template/url-template.js');
load('apigClient.js');

(async () => {
  const client = context.apigClientFactory.newClient({ apiKey: 'TEST_ONLY' });
  await client.searchGet({ q: 'show me sam & sally' }, null, {});
  assert.equal(new URL(calls.at(-1).url).searchParams.get('q'), 'show me sam & sally');
  assert.equal(calls.at(-1).headers['x-api-key'], 'TEST_ONLY');
  assert.equal(calls.at(-1).method, 'GET');

  const bytes = new Uint8Array([137,80,78,71,13,10,26,10,0,127,128,255]);
  const payloads = [
    new File([bytes], 'photo.png', { type: 'image/png' }),
    new Blob([bytes], { type: 'image/png' }),
    bytes.buffer,
    bytes
  ];
  for (const body of payloads) {
    await client.photosPut({ filename: 'photo space\u202f.png', 'Content-Type': 'image/png', 'x-amz-meta-customLabels': 'Sam, Sally' }, body, {});
    const request = calls.at(-1);
    assert.equal(request.data, body, 'Generated SDK must preserve the exact binary object');
    const roundtrip = body instanceof Blob ? Buffer.from(await body.arrayBuffer()) :
      body instanceof ArrayBuffer ? Buffer.from(body) : Buffer.from(body.buffer, body.byteOffset, body.byteLength);
    assert.deepEqual(roundtrip, Buffer.from(bytes), 'All image bytes, including non-ASCII bytes, must survive');
    assert.equal(request.headers['Content-Type'], 'image/png');
    assert.equal(request.headers['x-amz-meta-customLabels'], 'Sam, Sally');
    assert.equal(request.headers['x-api-key'], 'TEST_ONLY');
    assert.equal(new URL(request.url).searchParams.get('filename'), 'photo space\u202f.png');
    assert.equal(request.method, 'PUT');
  }
  await client.photosPut({ filename: 'no-labels.png', 'Content-Type': 'image/png', 'x-amz-meta-customLabels': '' }, payloads[0], {});
  assert.equal(calls.at(-1).headers['x-amz-meta-customLabels'], '');
  assert.equal(calls.at(-1).data, payloads[0]);
  new vm.Script(fs.readFileSync(path.join(root, 'app.js'), 'utf8'), { filename: 'app.js' });
  console.log('PASS: generated SDK preserves File, Blob, ArrayBuffer, and typed-array bytes; optional empty labels; Unicode filename and search-query encoding; content type and API-key headers; app syntax.');
})().catch(error => { console.error(error); process.exitCode = 1; });
