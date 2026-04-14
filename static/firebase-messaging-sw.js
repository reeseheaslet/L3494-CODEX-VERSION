importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-messaging-compat.js');

firebase.initializeApp({
  apiKey: "AIzaSyC9E1HsFXzkQ8YuIzHd4TSPoZYRt7Zils4",
  authDomain: "local3494-25a9f.firebaseapp.com",
  projectId: "local3494-25a9f",
  storageBucket: "local3494-25a9f.firebasestorage.app",
  messagingSenderId: "567225617879",
  appId: "1:567225617879:web:7ff9d46c8e620598880f92"
});

// Initialize messaging (needed for token registration) but do NOT call
// onBackgroundMessage — its internal push listener conflicts with ours on iOS.
firebase.messaging();

// Force new service worker to activate immediately without waiting
self.addEventListener('install', function(event) {
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(self.clients.claim());
});

// Single, direct push event listener — most compatible across iOS and Android
self.addEventListener('push', function(event) {
  let title = 'Local 3494';
  let body = '';
  let data = null;

  if (event.data) {
    try {
      data = event.data.json();
      // FCM sends notification payload nested under 'notification'
      title = (data.notification && data.notification.title) || (data.data && data.data.title) || title;
      body  = (data.notification && data.notification.body)  || (data.data && data.data.body)  || body;
    } catch (e) {
      body = event.data.text();
    }
  }

  const url = (data && data.data && data.data.url) ? data.data.url : '/';

  event.waitUntil(
    self.registration.showNotification(title, {
      body: body,
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      data: { url: url }
    })
  );
});

// Handle notification tap — open app to the right page
self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url)
    ? event.notification.data.url
    : '/';
  const fullUrl = self.location.origin + targetUrl;

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
      // If app is already open, focus and navigate
      for (const client of clientList) {
        if ('focus' in client) {
          return client.focus().then(() => client.navigate(fullUrl));
        }
      }
      // Otherwise open a new window
      if (clients.openWindow) {
        return clients.openWindow(fullUrl);
      }
    })
  );
});
