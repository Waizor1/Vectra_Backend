import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:33083';
const DIRECTUS_URL = __ENV.DIRECTUS_URL || 'http://127.0.0.1:8055';
const LOADTEST_DATA_PATH = __ENV.LOADTEST_DATA_PATH || '/work/artifacts/loadtest_data.json';
const ADMIN_INTEGRATION_TOKEN = __ENV.ADMIN_INTEGRATION_TOKEN || '';
const DIRECTUS_EMAIL = __ENV.DIRECTUS_EMAIL || '';
const DIRECTUS_PASSWORD = __ENV.DIRECTUS_PASSWORD || '';
const ENABLE_MUTATION_CANARIES = String(__ENV.ENABLE_MUTATION_CANARIES || '').trim().toLowerCase() === 'true';
const ENABLE_XFF_SIMULATION = String(__ENV.ENABLE_XFF_SIMULATION || '').trim().toLowerCase() === 'true';
const XFF_POOL_CIDR_BASE = __ENV.XFF_POOL_CIDR_BASE || '198.18.0.0/15';
const XFF_POOL_SIZE = Number.parseInt(__ENV.XFF_POOL_SIZE || '4096', 10) > 0
  ? Number.parseInt(__ENV.XFF_POOL_SIZE || '4096', 10)
  : 4096;
const DIRECTUS_DELETE_CALLBACK = http.expectedStatuses(200, 204, 404);
let directusRuntimeToken = '';
const POOL_STRIDE = 1_000_000;
const LOADTEST_BASE_ID_START = 8_000_000_000;
const LOADTEST_BASE_ID_MODULUS = 900_000;

function ipv4ToInt(ipv4) {
  const parts = String(ipv4).split('.').map((part) => Number.parseInt(part, 10));
  if (parts.length !== 4 || parts.some((part) => Number.isNaN(part) || part < 0 || part > 255)) {
    throw new Error(`Invalid IPv4 address: ${ipv4}`);
  }
  return (((parts[0] << 24) >>> 0) + ((parts[1] << 16) >>> 0) + ((parts[2] << 8) >>> 0) + parts[3]) >>> 0;
}

function intToIpv4(value) {
  return [
    (value >>> 24) & 255,
    (value >>> 16) & 255,
    (value >>> 8) & 255,
    value & 255,
  ].join('.');
}

function parseCidrBase(cidrBase) {
  const [baseIp] = String(cidrBase || '').split('/');
  try {
    return ipv4ToInt(baseIp);
  } catch (_e) {
    return ipv4ToInt('198.18.0.0');
  }
}

function crc32(input) {
  let crc = 0 ^ (-1);
  const source = String(input || '');
  for (let i = 0; i < source.length; i += 1) {
    let c = (crc ^ source.charCodeAt(i)) & 0xFF;
    for (let j = 0; j < 8; j += 1) {
      c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ (-1)) >>> 0;
}

function baseIdForRun(runId) {
  return LOADTEST_BASE_ID_START + (crc32(runId) % LOADTEST_BASE_ID_MODULUS) * POOL_STRIDE;
}

function assertCanaryPoolRange(poolName, ids, minIdInclusive, maxIdExclusive) {
  for (let i = 0; i < ids.length; i += 1) {
    const id = Number(ids[i]);
    const isInteger = Number.isInteger(id);
    if (!isInteger || id < minIdInclusive || id >= maxIdExclusive) {
      throw new Error(
        `Invalid ${poolName} id for run-scoped harness range: id=${ids[i]}, ` +
        `expected=[${minIdInclusive}, ${maxIdExclusive}), index=${i}`,
      );
    }
  }
}

function validateMutationCanaryDeletePools(runId, deleteApiIds, deleteDirectusIds) {
  const normalizedRunId = String(runId || '').trim();
  if (!normalizedRunId) {
    throw new Error('Artifact run_id is required for mutation canary safety validation');
  }

  const baseId = baseIdForRun(normalizedRunId);
  assertCanaryPoolRange(
    'delete_api_user_ids',
    deleteApiIds,
    baseId + (2 * POOL_STRIDE),
    baseId + (3 * POOL_STRIDE),
  );
  assertCanaryPoolRange(
    'delete_directus_user_ids',
    deleteDirectusIds,
    baseId + (3 * POOL_STRIDE),
    baseId + (4 * POOL_STRIDE),
  );
}

function assertNonEmptyDeletePool(poolName, values) {
  if (!Array.isArray(values) || values.length === 0) {
    throw new Error(
      `Artifact ${poolName} pool is empty. Regenerate scripts/loadtest/artifacts/loadtest_data.json ` +
      `via scripts/loadtest/prepare_loadtest_data.py with a non-zero ${poolName} count for this run_id`,
    );
  }
}

const XFF_BASE_IP_INT = parseCidrBase(XFF_POOL_CIDR_BASE);

function loadArtifact(path) {
  try {
    return JSON.parse(open(path));
  } catch (_e) {
    return {
      run_id: 'missing-artifact',
      regular_tokens: [],
      partner_tokens: [],
      delete_api_user_ids: [],
      delete_directus_user_ids: [],
    };
  }
}

const artifact = loadArtifact(LOADTEST_DATA_PATH);

const READ_STAGES = [
  { duration: '10m', target: 120 },
  { duration: '40m', target: 120 },
  { duration: '10m', target: 20 },
];

export const options = {
  scenarios: {
    user_profile: {
      executor: 'ramping-vus',
      exec: 'userProfileScenario',
      startVUs: 0,
      stages: READ_STAGES,
      gracefulRampDown: '30s',
    },
    devices: {
      executor: 'ramping-vus',
      exec: 'devicesScenario',
      startVUs: 0,
      stages: READ_STAGES,
      gracefulRampDown: '30s',
    },
    pay_tariffs: {
      executor: 'ramping-vus',
      exec: 'payTariffsScenario',
      startVUs: 0,
      stages: [
        { duration: '10m', target: 80 },
        { duration: '40m', target: 80 },
        { duration: '10m', target: 10 },
      ],
      gracefulRampDown: '30s',
    },
    partner_profit: {
      executor: 'ramping-vus',
      exec: 'partnerProfitScenario',
      startVUs: 0,
      stages: [
        { duration: '10m', target: 40 },
        { duration: '40m', target: 40 },
        { duration: '10m', target: 5 },
      ],
      gracefulRampDown: '30s',
    },
    ...(ENABLE_MUTATION_CANARIES
      ? {
          admin_delete_api_canary: {
            executor: 'ramping-vus',
            exec: 'adminDeleteApiScenario',
            startVUs: 0,
            stages: [
              { duration: '10m', target: 1 },
              { duration: '40m', target: 1 },
              { duration: '10m', target: 1 },
            ],
            gracefulRampDown: '30s',
          },
          admin_delete_directus_canary: {
            executor: 'ramping-vus',
            exec: 'adminDeleteDirectusScenario',
            startVUs: 0,
            stages: [
              { duration: '10m', target: 1 },
              { duration: '40m', target: 1 },
              { duration: '10m', target: 1 },
            ],
            gracefulRampDown: '30s',
          },
        }
      : {}),
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1500'],
  },
};

function assertRequiredEnv(name, value) {
  if (!value || String(value).trim() === '') {
    throw new Error(`Missing required env: ${name}`);
  }
}

function pickFromPool(values) {
  if (!values || values.length === 0) {
    throw new Error('Token/id pool is empty in loadtest artifact');
  }
  const idx = (__VU + __ITER) % values.length;
  return values[idx];
}

function nextSyntheticClientIp() {
  const stableOffset = (((__VU - 1) * 1000003) + __ITER) % XFF_POOL_SIZE;
  return intToIpv4((XFF_BASE_IP_INT + stableOffset) >>> 0);
}

function appHeaders(extraHeaders = {}) {
  if (!ENABLE_XFF_SIMULATION) {
    return extraHeaders;
  }
  return {
    ...extraHeaders,
    'X-Forwarded-For': nextSyntheticClientIp(),
  };
}

function directusLoginAccessToken() {
  const loginPayload = JSON.stringify({
    email: DIRECTUS_EMAIL,
    password: DIRECTUS_PASSWORD,
  });
  const loginRes = http.post(`${DIRECTUS_URL}/auth/login`, loginPayload, {
    headers: { 'Content-Type': 'application/json' },
    tags: { scenario: 'admin_delete_directus_canary', endpoint: '/auth/login' },
  });
  if (loginRes.status !== 200) {
    return '';
  }
  try {
    return loginRes.json('data.access_token') || '';
  } catch (_e) {
    return '';
  }
}

function ensureDirectusRuntimeToken(seedToken = '') {
  const refreshedToken = directusLoginAccessToken();
  if (refreshedToken) {
    directusRuntimeToken = refreshedToken;
    return refreshedToken;
  }
  if (directusRuntimeToken) {
    return directusRuntimeToken;
  }
  if (seedToken) {
    directusRuntimeToken = seedToken;
    return seedToken;
  }
  return '';
}

function thinkTimeRead() {
  sleep(0.5 + Math.random() * 2.0);
}

function thinkTimeCanary() {
  sleep(30 + Math.random() * 15);
}

export function setup() {
  if (!artifact.regular_tokens?.length || !artifact.partner_tokens?.length) {
    throw new Error('Artifact must contain regular_tokens and partner_tokens');
  }

  if (!ENABLE_MUTATION_CANARIES) {
    return {
      directusToken: '',
      regularTokens: artifact.regular_tokens,
      partnerTokens: artifact.partner_tokens,
      deleteApiIds: [],
      deleteDirectusIds: [],
    };
  }

  assertRequiredEnv('ADMIN_INTEGRATION_TOKEN', ADMIN_INTEGRATION_TOKEN);
  assertRequiredEnv('DIRECTUS_EMAIL', DIRECTUS_EMAIL);
  assertRequiredEnv('DIRECTUS_PASSWORD', DIRECTUS_PASSWORD);

  const deleteApiIds = artifact.delete_api_user_ids || [];
  const deleteDirectusIds = artifact.delete_directus_user_ids || [];

  assertNonEmptyDeletePool('delete_api_user_ids', deleteApiIds);
  assertNonEmptyDeletePool('delete_directus_user_ids', deleteDirectusIds);

  validateMutationCanaryDeletePools(
    artifact.run_id,
    deleteApiIds,
    deleteDirectusIds,
  );

  const loginPayload = JSON.stringify({
    email: DIRECTUS_EMAIL,
    password: DIRECTUS_PASSWORD,
  });

  const loginRes = http.post(`${DIRECTUS_URL}/auth/login`, loginPayload, {
    headers: { 'Content-Type': 'application/json' },
    tags: { scenario: 'setup', endpoint: 'directus_login' },
  });

  const loginOk = check(loginRes, {
    'directus login status is 200': (r) => r.status === 200,
    'directus login has access token': (r) => {
      try {
        return !!r.json('data.access_token');
      } catch (_e) {
        return false;
      }
    },
  });

  if (!loginOk) {
    throw new Error(`Directus login failed with status=${loginRes.status} body=${loginRes.body}`);
  }

  return {
    directusToken: loginRes.json('data.access_token'),
    regularTokens: artifact.regular_tokens,
    partnerTokens: artifact.partner_tokens,
    deleteApiIds,
    deleteDirectusIds,
  };
}

export function userProfileScenario(data) {
  const token = pickFromPool(data.regularTokens);
  const res = http.get(`${BASE_URL}/user`, {
    headers: appHeaders({ Authorization: `Bearer ${token}` }),
    tags: { scenario: 'user_profile', endpoint: '/user' },
  });
  check(res, { 'user profile status is 200': (r) => r.status === 200 });
  thinkTimeRead();
}

export function devicesScenario(data) {
  const token = pickFromPool(data.regularTokens);
  const res = http.get(`${BASE_URL}/devices`, {
    headers: appHeaders({ Authorization: `Bearer ${token}` }),
    tags: { scenario: 'devices', endpoint: '/devices' },
  });
  check(res, { 'devices status is 200': (r) => r.status === 200 });
  thinkTimeRead();
}

export function payTariffsScenario() {
  const res = http.get(`${BASE_URL}/pay/tariffs`, {
    headers: appHeaders(),
    tags: { scenario: 'pay_tariffs', endpoint: '/pay/tariffs' },
  });
  check(res, { 'pay tariffs status is 200': (r) => r.status === 200 });
  thinkTimeRead();
}

export function partnerProfitScenario(data) {
  const token = pickFromPool(data.partnerTokens);
  const res = http.get(`${BASE_URL}/partner/profit?range=year`, {
    headers: appHeaders({ Authorization: `Bearer ${token}` }),
    tags: { scenario: 'partner_profit', endpoint: '/partner/profit' },
  });
  check(res, { 'partner profit status is 200': (r) => r.status === 200 });
  thinkTimeRead();
}

export function adminDeleteApiScenario(data) {
  const idx = __ITER;
  if (idx >= data.deleteApiIds.length) {
    thinkTimeCanary();
    return;
  }
  const userId = data.deleteApiIds[idx];
  const headers = appHeaders({ 'X-Admin-Integration-Token': ADMIN_INTEGRATION_TOKEN });

  const preDeleteRes = http.post(`${BASE_URL}/admin/integration/users/${userId}/pre-delete`, null, {
    headers,
    tags: { scenario: 'admin_delete_api_canary', endpoint: '/admin/integration/users/{id}/pre-delete' },
  });
  check(preDeleteRes, { 'admin pre-delete status is 200': (r) => r.status === 200 });

  const deleteRes = http.del(`${BASE_URL}/admin/integration/users/${userId}`, null, {
    headers,
    tags: { scenario: 'admin_delete_api_canary', endpoint: '/admin/integration/users/{id}' },
  });
  check(deleteRes, { 'admin delete status is 200': (r) => r.status === 200 });

  thinkTimeCanary();
}

export function adminDeleteDirectusScenario(data) {
  const idx = __ITER;
  if (idx >= data.deleteDirectusIds.length) {
    thinkTimeCanary();
    return;
  }
  const userId = data.deleteDirectusIds[idx];
  const currentToken = ensureDirectusRuntimeToken(data.directusToken);
  if (!currentToken) {
    throw new Error('Directus token refresh failed before delete canary attempt');
  }
  let res = http.del(`${DIRECTUS_URL}/items/users/${userId}`, null, {
    headers: {
      Authorization: `Bearer ${currentToken}`,
    },
    responseCallback: DIRECTUS_DELETE_CALLBACK,
    tags: { scenario: 'admin_delete_directus_canary', endpoint: '/items/users/{id}' },
  });

  if (res.status === 401) {
    const refreshedToken = ensureDirectusRuntimeToken(data.directusToken);
    if (refreshedToken) {
      res = http.del(`${DIRECTUS_URL}/items/users/${userId}`, null, {
        headers: {
          Authorization: `Bearer ${refreshedToken}`,
        },
        responseCallback: DIRECTUS_DELETE_CALLBACK,
        tags: { scenario: 'admin_delete_directus_canary', endpoint: '/items/users/{id}' },
      });
    }
  }
  check(res, {
    'directus delete status is 200/204/404': (r) => r.status === 200 || r.status === 204 || r.status === 404,
  });

  thinkTimeCanary();
}
