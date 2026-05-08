// Firebase Configuration
const firebaseConfig = {
  apiKey: "AIzaSyBvumZvNJeP5Re0o-N401rrQMBHe1vnDcg",
  authDomain: "campus2air-carpool.firebaseapp.com",
  projectId: "campus2air-carpool",
  storageBucket: "campus2air-carpool.firebasestorage.app",
  messagingSenderId: "121527225683",
  appId: "1:121527225683:web:b6f203b52bfdfadb42a5a6",
  measurementId: "G-RJZP600N14"
};

firebase.initializeApp(firebaseConfig);
const auth = firebase.auth();
const googleProvider = new firebase.auth.GoogleAuthProvider();
googleProvider.setCustomParameters({
  prompt: 'select_account',
  hd: 'ucr.edu'
});

const termsCheckbox = document.getElementById('terms-agree');
const signinBtn = document.getElementById('google-signin-btn');
const statusEl = document.getElementById('login-status');
const defaultButtonHtml = signinBtn ? signinBtn.innerHTML : '';
const REDIRECT_FLOW_KEY = 'campus2air-auth-flow';
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
const REDIRECT_FLOW_TIMEOUT_MS = 8000;
let serverLoginPromise = null;
let persistenceReadyPromise = null;

function _bothChecked() {
  return !!termsCheckbox?.checked;
}

function setStatus(message, className = '') {
  if (!statusEl) return;
  statusEl.textContent = message;
  statusEl.className = className;
}

function setButtonBusy(label) {
  if (!signinBtn) return;
  signinBtn.disabled = true;
  signinBtn.innerHTML = `<span class="spinner"></span> ${label}`;
}

function setAuthBusy(label) {
  setStatus('');
  setButtonBusy(label);
}

function resetButton() {
  if (!signinBtn) return;
  signinBtn.disabled = !_bothChecked();
  signinBtn.innerHTML = defaultButtonHtml;
}

function prefersRedirectFlow() {
  const isStandalone = window.matchMedia?.('(display-mode: standalone)').matches;
  const isAndroidTwa = document.referrer.startsWith('android-app://');
  const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || '');
  return !!(isStandalone || isAndroidTwa || isMobile);
}

function markRedirectFlowPending() {
  try {
    sessionStorage.setItem(REDIRECT_FLOW_KEY, 'redirect');
  } catch (_) {}

  try {
    localStorage.setItem(REDIRECT_FLOW_KEY, 'redirect');
  } catch (_) {}
}

function hasPendingRedirectFlow() {
  try {
    if (sessionStorage.getItem(REDIRECT_FLOW_KEY) === 'redirect') {
      return true;
    }
  } catch (_) {}

  try {
    if (localStorage.getItem(REDIRECT_FLOW_KEY) === 'redirect') {
      return true;
    }
  } catch (_) {}

  return false;
}

function clearPendingRedirectFlow() {
  try {
    sessionStorage.removeItem(REDIRECT_FLOW_KEY);
  } catch (_) {}

  try {
    localStorage.removeItem(REDIRECT_FLOW_KEY);
  } catch (_) {}
}

function ensureAuthPersistence() {
  if (persistenceReadyPromise) {
    return persistenceReadyPromise;
  }

  persistenceReadyPromise = auth
    .setPersistence(firebase.auth.Auth.Persistence.LOCAL)
    .catch(() => undefined);

  return persistenceReadyPromise;
}

async function waitForFirebaseUser(timeoutMs = REDIRECT_FLOW_TIMEOUT_MS) {
  if (auth.currentUser) {
    return auth.currentUser;
  }

  return new Promise((resolve) => {
    let finished = false;
    let unsubscribe = () => {};

    const finish = (user) => {
      if (finished) {
        return;
      }
      finished = true;
      window.clearTimeout(timeoutId);
      unsubscribe();
      resolve(user || null);
    };

    const timeoutId = window.setTimeout(() => finish(null), timeoutMs);
    unsubscribe = auth.onAuthStateChanged((user) => finish(user || auth.currentUser || null));
  });
}

async function finishServerLogin(user) {
  if (!user) {
    throw new Error('Authentication completed, but no Firebase user session was available.');
  }

  if (serverLoginPromise) {
    return serverLoginPromise;
  }

serverLoginPromise = (async () => {
  const email = (user.email || '').toLowerCase();
  if (!email.endsWith('@ucr.edu')) {
    await auth.signOut();
    window.location.href = '/login?error=ucr_only';
    return;
  }

  const idToken = await user.getIdToken(true);
  const res = await fetch('/auth/firebase-callback', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify({ idToken, csrf_token: csrfToken })
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    await auth.signOut();
    if (res.status === 403) {
      window.location.href = '/login?error=ucr_only';
      return;
    }
    throw new Error(data.error || 'Authentication failed');
  }

  window.location.href = data.redirect || '/start-now';
  })();

  try {
    return await serverLoginPromise;
  } finally {
    serverLoginPromise = null;
  }
}

function handleAuthError(err) {
  if (err?.code === 'auth/popup-closed-by-user' || err?.code === 'auth/cancelled-popup-request') {
    setStatus('Sign-in cancelled.');
    return;
  }

  if (err?.code === 'auth/unauthorized-domain') {
    setStatus(
      'This domain is not authorized in Firebase. Add it in Firebase Console -> Authentication -> Settings -> Authorized domains.',
      'login-error'
    );
    return;
  }

  setStatus(err?.message || 'Sign-in failed. Please try again.', 'login-error');
}

termsCheckbox?.addEventListener('change', () => {
  resetButton();
});

signinBtn?.addEventListener('click', async () => {
  if (!termsCheckbox?.checked) {
    setStatus('You must agree to the Terms of Service, EULA, and Privacy Policy to continue.');
    return;
  }

  setAuthBusy('Signing in...');

  try {
    await ensureAuthPersistence();

    if (prefersRedirectFlow()) {
      markRedirectFlowPending();
      await auth.signInWithRedirect(googleProvider);
      return;
    }

    const result = await auth.signInWithPopup(googleProvider);
    await finishServerLogin(result.user);
  } catch (err) {
    handleAuthError(err);
    resetButton();
  }
});

async function bootstrapAuth() {
  if (!signinBtn) return;

  setAuthBusy('Logging in...');
  const hadRedirectFlow = hasPendingRedirectFlow();

  await ensureAuthPersistence();

  try {
    const result = await auth.getRedirectResult();
    clearPendingRedirectFlow();

    if (result?.user) {
      await finishServerLogin(result.user);
      return;
    }
  } catch (err) {
    clearPendingRedirectFlow();
    handleAuthError(err);
  }

  if (hadRedirectFlow) {
    setAuthBusy('Finishing sign-in...');
    try {
      const restoredUser = await waitForFirebaseUser();
      if (restoredUser) {
        await finishServerLogin(restoredUser);
        return;
      }
      setStatus('Sign-in did not complete. Please tap Sign in with Google again.', 'login-error');
    } catch (err) {
      handleAuthError(err);
    } finally {
      clearPendingRedirectFlow();
    }
  }

  resetButton();
}

bootstrapAuth();
