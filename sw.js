// ختمة - Service Worker v3 (يعمل حتى لو نقص أحد الملفات، ويستقبل الإشعارات من الخادم)
const CACHE = 'khatma-v3';
const SHELL = ['./', 'index.html', 'manifest.json', 'icon-192.png', 'icon-512.png'];

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(c => Promise.all(SHELL.map(u => c.add(u).catch(() => null)))).catch(() => null));
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    try { const ks = await caches.keys(); await Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))); } catch (x) {}
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const u = new URL(r.url);
  const sameOrigin = u.origin === self.location.origin;
  const fonts = u.hostname === 'fonts.googleapis.com' || u.hostname === 'fonts.gstatic.com';
  if (!sameOrigin && !fonts) return;
  if (u.pathname.endsWith('/sw.js')) return;
  if (r.mode === 'navigate' || u.pathname.endsWith('index.html') || u.pathname.endsWith('/')) {
    e.respondWith(fetch(r).then(res => { const cp = res.clone(); caches.open(CACHE).then(c => c.put(r, cp)).catch(() => {}); return res; })
      .catch(() => caches.match(r).then(m => m || caches.match('index.html') || caches.match('./'))));
    return;
  }
  e.respondWith(caches.match(r).then(m => m || fetch(r).then(res => {
    if (res && res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(r, cp)).catch(() => {}); }
    return res;
  })));
});

// استقبال الإشعارات من الخادم: {data:{title, body, lang}}
self.addEventListener('push', e => {
  let d = {};
  try {
    const j = e.data ? e.data.json() : {};
    d = (j && (j.data || j.notification)) || j || {};
  } catch (x) {
    try { d = { body: e.data.text() }; } catch (y) {}
  }
  const title = d.title || '🕌 ختمة';
  const body = d.body || 'هيّا أكمل قراءة القرآن 🌙';
  const lang = d.lang === 'en' ? 'en' : 'ar';
  e.waitUntil(self.registration.showNotification(title, {
    body: body, icon: 'icon-192.png', badge: 'icon-192.png', lang: lang, dir: lang === 'en' ? 'ltr' : 'rtl',
    tag: d.tag || 'khatma-push', renotify: true
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    for (const c of list) { if ('focus' in c) return c.focus(); }
    return self.clients.openWindow('./');
  }));
});
