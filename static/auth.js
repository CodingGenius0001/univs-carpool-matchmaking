// =============================================================================
// Firebase Configuration
// =============================================================================
// IMPORTANT: Replace these placeholder values with your actual Firebase config.
// Get these from: Firebase Console → Project Settings → General → Your apps → Web app
//
// You can also set these as environment variables and inject them via the template,
// but for simplicity they are hardcoded here as placeholders.
// =============================================================================

const firebaseConfig = {
  apiKey: "AIzaSyBvumZvNJeP5Re0o-N401rrQMBHe1vnDcg",
  authDomain: "campus2air-carpool.firebaseapp.com",
  projectId: "campus2air-carpool",
  storageBucket: "campus2air-carpool.firebasestorage.app",
  messagingSenderId: "121527225683",
  appId: "1:121527225683:web:b6f203b52bfdfadb42a5a6",
  measurementId: "G-RJZP600N14"
};

// Initialize Firebase
firebase.initializeApp(firebaseConfig);
const auth = firebase.auth();
const googleProvider = new firebase.auth.GoogleAuthProvider();

// Force account selection every time (don't auto-pick last account)
googleProvider.setCustomParameters({ prompt: 'select_account' });

// Only allow @ucr.edu accounts
googleProvider.setCustomParameters({
  prompt: 'select_account',
  hd: 'ucr.edu'
});

// --- Terms checkbox gate ---
const termsCheckbox = document.getElementById('terms-agree');
const signinBtn = document.getElementById('google-signin-btn');
const statusEl = document.getElementById('login-status');

function _bothChecked() {
  return !!termsCheckbox?.checked;
}

termsCheckbox?.addEventListener('change', () => {
  signinBtn.disabled = !_bothChecked();
});

// --- Sign in handler ---
signinBtn?.addEventListener('click', async () => {
  if (!termsCheckbox?.checked) {
    if (statusEl) statusEl.textContent = 'You must agree to the Terms of Service, EULA, and Privacy Policy to continue.';
    return;
  }
  signinBtn.disabled = true;
  signinBtn.innerHTML = '<span class="spinner"></span> Signing in...';
  if (statusEl) statusEl.textContent = '';

  try {
    const result = await auth.signInWithPopup(googleProvider);
    const user = result.user;
    const email = (user.email || '').toLowerCase();

    // Client-side domain check
    if (!email.endsWith('@ucr.edu')) {
      await auth.signOut();
      window.location.href = '/?error=ucr_only';
      return;
    }

    // Send to server to set session
    const res = await fetch('/auth/firebase-callback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: email,
        name: user.displayName || '',
        uid: user.uid
      })
    });

    const data = await res.json();

    if (!res.ok) {
      if (res.status === 403) {
        await auth.signOut();
        window.location.href = '/login?error=ucr_only';
        return;
      }
      throw new Error(data.error || 'Authentication failed');
    }

    // Redirect to the app
    window.location.href = data.redirect || '/start-now';

  } catch (err) {
    // User closed popup or other error
    if (err.code === 'auth/popup-closed-by-user' || err.code === 'auth/cancelled-popup-request') {
      if (statusEl) statusEl.textContent = 'Sign-in cancelled.';
    } else if (err.code === 'auth/unauthorized-domain') {
      if (statusEl) statusEl.textContent = 'This domain is not authorized in Firebase. Please add it to your Firebase Console → Authentication → Settings → Authorized domains.';
      statusEl.className = 'login-error';
    } else {
      if (statusEl) {
        statusEl.textContent = err.message || 'Sign-in failed. Please try again.';
        statusEl.className = 'login-error';
      }
    }
  } finally {
    signinBtn.disabled = !_bothChecked();
    signinBtn.innerHTML = `
      <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/>
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
      </svg>
      Sign in with Google`;
  }
});
