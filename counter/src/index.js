/**
 * gh-visits — antony138.github.io 两个站点（cc-digest / mainichi-sanku）共用的访问计数器。
 * Cloudflare Worker + D1，免费额度内运行。
 *
 * POST /hit   body: {"site":"cc-digest","nv":1}  → 计一次访问（nv=1 表示新访客），返回 {pv, uv}
 * GET  /get?site=cc-digest                        → 只读当前计数
 *
 * 新访客由前端 localStorage 判定（首访标记），不采集 IP、不种 cookie。
 * POST 用 text/plain 简单请求，避免 CORS 预检的额外往返。
 */
const SITES = new Set(['cc-digest', 'mainichi-sanku']);
const ALLOWED_ORIGINS = new Set([
  'https://antony138.github.io',
  'http://localhost:8642', // 本地调试
]);

function baseHeaders(origin) {
  return {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store',
    'Access-Control-Allow-Origin':
      ALLOWED_ORIGINS.has(origin) ? origin : 'https://antony138.github.io',
    'Vary': 'Origin',
  };
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const headers = baseHeaders(req.headers.get('Origin') || '');

    if (req.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers });
    }

    if (url.pathname === '/hit' && req.method === 'POST') {
      let site = '';
      let nv = 0;
      try {
        ({ site, nv } = JSON.parse(await req.text()));
      } catch (e) { /* 落到下面的 400 */ }
      if (!SITES.has(site)) {
        return new Response('{"error":"unknown site"}', { status: 400, headers });
      }
      const ua = req.headers.get('User-Agent') || '';
      if (!/bot|crawl|spider|slurp|headless/i.test(ua)) {
        await env.DB.prepare(
          'INSERT INTO counts(site, pv, uv) VALUES(?1, 1, ?2) ' +
          'ON CONFLICT(site) DO UPDATE SET pv = pv + 1, uv = uv + ?2'
        ).bind(site, nv ? 1 : 0).run();
      }
      const row = await env.DB.prepare(
        'SELECT pv, uv FROM counts WHERE site = ?1'
      ).bind(site).first();
      return new Response(JSON.stringify(row || { pv: 0, uv: 0 }), { headers });
    }

    if (url.pathname === '/get' && req.method === 'GET') {
      const site = url.searchParams.get('site');
      if (!SITES.has(site)) {
        return new Response('{"error":"unknown site"}', { status: 400, headers });
      }
      const row = await env.DB.prepare(
        'SELECT pv, uv FROM counts WHERE site = ?1'
      ).bind(site).first();
      return new Response(JSON.stringify(row || { pv: 0, uv: 0 }), { headers });
    }

    return new Response('{"error":"not found"}', { status: 404, headers });
  },
};
