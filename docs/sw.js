/* cc-digest service worker — 页面/数据 network-first（保证 push 即生效），图标等静态资源 cache-first */
const CACHE = 'cc-digest-v1';
const SHELL = [
  '.',
  'index.html',
  'manifest.webmanifest',
  'icon-192.png',
  'icon-512.png',
  'apple-touch-icon.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// 页面本体、数据与 RSS 要新鲜：网络优先，失败回退缓存。
// 这样改 index.html push 后返场用户立即可见（README 的承诺），离线时仍有缓存兜底。
function wantsFresh(req, url) {
  return req.mode === 'navigate'
    || url.pathname.endsWith('/index.html')
    || (url.pathname.includes('/data/') && url.pathname.endsWith('.json'))
    || url.pathname.endsWith('/rss.xml');
}

function fetchAndCache(req) {
  return fetch(req).then((res) => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy));
    }
    return res;
  });
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (wantsFresh(req, url)) {
    e.respondWith(
      fetchAndCache(req).catch(() =>
        caches.match(req, { ignoreSearch: true }).then((hit) => hit || Response.error())
      )
    );
    return;
  }

  // 壳与图标：缓存优先，未命中再走网络并回填
  e.respondWith(
    caches.match(req, { ignoreSearch: true }).then((hit) => hit || fetchAndCache(req))
  );
});
