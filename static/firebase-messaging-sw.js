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

const messaging = firebase.messaging();

// Firebase compat SDK background handler (Android/Chrome)
messaging.onBackgroundMessage(function(payload) {
  const notificationTitle = payload.notification?.title || 'Local 3494';
  const notificationOptions = {
    body: payload.notification?.body || '',
    icon: '/static/icons/icon-192.png'
  };
  self.registration.showNotification(notificationTitle, notificationOptions);
});

// Direct push event listener — required for iOS PWA and more reliable across all platforms
self.addEventListener('push', function(event) {
  let title = 'Local 3494';
  let body = '';

  if (event.data) {
    try {
      const data = event.data.json();
      title = data.notification?.title || data.data?.title || title;
      body = data.notification?.body || data.data?.body || body;
    } catch (e) {
      body = event.data.text();
    }
  }

  event.waitUntil(
    self.registration.showNotification(title, {
      body: body,
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png'
    })
  );
});
