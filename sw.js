// Service Worker — Расписание ЧРТ
const CACHE_NAME = 'chrt-v2';
const STATIC_FILES = [
  './index.html',
  './manifest.json',
];

// Установка — кешируем статику
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(c => c.addAll(STATIC_FILES))
  );
  self.skipWaiting();
});

// Активация — удаляем старые кеши
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// schedule.json — network-first (важна свежесть)
// остальное    — cache-first (быстро, офлайн)
self.addEventListener('fetch', e => {
  if (e.request.url.includes('schedule.json')) {
    e.respondWith(
      fetch(e.request)
        .then(resp => {
          caches.open(CACHE_NAME).then(c => c.put(e.request, resp.clone()));
          return resp;
        })
        .catch(() => caches.match(e.request))
    );
  } else {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
    );
  }
});
