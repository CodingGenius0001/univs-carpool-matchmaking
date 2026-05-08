const firebaseConfig = {
  apiKey: "AIzaSyBvumZvNJeP5Re0o-N401rrQMBHe1vnDcg",
  authDomain: "campus2air-carpool.firebaseapp.com",
  projectId: "campus2air-carpool",
  storageBucket: "campus2air-carpool.firebasestorage.app",
  messagingSenderId: "121527225683",
  appId: "1:121527225683:web:b6f203b52bfdfadb42a5a6",
  measurementId: "G-RJZP600N14"
};

if (!firebase.apps.length) {
  firebase.initializeApp(firebaseConfig);
}

const REDIRECT_FLOW_KEY = 'campus2air-auth-flow';
const statusEl = document.getElementById('logout-status');

function setStatus(message) {
  if (statusEl) {
    statusEl.textContent = message;
  }
}

function clearPendingRedirectFlow() {
  try {
    sessionStorage.removeItem(REDIRECT_FLOW_KEY);
  } catch (_) {}

  try {
    localStorage.removeItem(REDIRECT_FLOW_KEY);
  } catch (_) {}
}

async function logout() {
  setStatus('Finalizing sign-out...');
  clearPendingRedirectFlow();

  try {
    await firebase.auth().signOut();
  } catch (_) {
    // The server session is already cleared. Continue to the login page.
  }

  clearPendingRedirectFlow();
  window.location.replace('/login?logged_out=1');
}

logout();
