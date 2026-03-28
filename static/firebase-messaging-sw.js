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

messaging.onBackgroundMessage(function(payload) {
  const notificationTitle = payload.notification.title;
  const notificationOptions = {
    body: payload.notification.body,
    icon: '/static/icons/icon-192.png'
  };
  self.registration.showNotification(notificationTitle, notificationOptions);
});
