// Service Worker para Legacy Hub
self.addEventListener('install', (e) => {
  console.log('[Service Worker] Install');
});

self.addEventListener('fetch', (e) => {
  // Básico: solo permite que la app funcione online
  e.respondWith(fetch(e.request));
});