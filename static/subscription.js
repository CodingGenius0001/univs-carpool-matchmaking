/**
 * subscription.js — Campus2Air subscription UI helpers
 * Handles: status banners, paywall modals, Stripe checkout redirect,
 * credits indicator, and hamburger badge.
 */

(function () {
  'use strict';

  let _cachedAccess = null;

  async function fetchStatus() {
    if (_cachedAccess) return _cachedAccess;
    try {
      const res = await fetch('/api/subscription/status');
      if (!res.ok) return null;
      _cachedAccess = await res.json();
      return _cachedAccess;
    } catch {
      return null;
    }
  }

  /** Inject subscription tier badge next to the user email in hamburger menu */
  async function initSubscriptionBadge() {
    const access = await fetchStatus();
    if (!access) return;
    const emailEl = document.querySelector('.menu-email');
    if (!emailEl) return;

    const tier = access.tier || 'none';
    const labels = { trial: 'Trial', monthly: 'Pro', annual: 'Annual', search_pack: 'Pack', none: 'Locked' };
    const badge = document.createElement('span');
    badge.className = 'subscription-badge ' + (tier === 'none' ? 'locked' : tier === 'trial' ? 'trial' : tier === 'search_pack' ? 'search-pack' : '');
    badge.textContent = labels[tier] || tier;
    emailEl.after(badge);
  }

  /** Show a trial / status banner at the top of .container main */
  async function initTrialBanner(containerId) {
    const access = await fetchStatus();
    if (!access) return;
    const container = document.getElementById(containerId) || document.querySelector('.container');
    if (!container) return;

    let html = '';
    const tier = access.tier || 'none';

    if (tier === 'trial') {
      const days = access.trial_days_left || 0;
      const urgency = days <= 5 ? 'warning' : '';
      html = `<div class="trial-banner ${urgency}">
        <span class="trial-icon">⏳</span>
        <span>Free trial active &mdash; <strong>${days} day${days !== 1 ? 's' : ''}</strong> remaining.
        <a href="/pricing">Upgrade now</a> to keep access after your trial ends.</span>
      </div>`;
    } else if (tier === 'monthly') {
      html = `<div class="trial-banner">
        <span class="trial-icon">✓</span>
        <span>Monthly plan active. <a href="/account">Manage billing</a></span>
      </div>`;
    } else if (tier === 'annual') {
      html = `<div class="trial-banner">
        <span class="trial-icon">✓</span>
        <span>Annual plan active. <a href="/account">Manage billing</a></span>
      </div>`;
    } else if (tier === 'search_pack') {
      const credits = access.search_credits || 0;
      html = `<div class="trial-banner">
        <span class="trial-icon">🔍</span>
        <span><strong>${credits}</strong> search credit${credits !== 1 ? 's' : ''} remaining.
        <a href="/pricing">Get more</a> or <a href="/pricing">upgrade to monthly</a>.</span>
      </div>`;
    } else {
      html = `<div class="trial-banner locked">
        <span class="trial-icon">🔒</span>
        <span>Your trial has ended. <a href="/pricing">Choose a plan</a> to continue using Campus2Air.</span>
      </div>`;
    }

    if (html) {
      const firstChild = container.querySelector('section, .card');
      if (firstChild) {
        firstChild.insertAdjacentHTML('beforebegin', html);
      } else {
        container.insertAdjacentHTML('afterbegin', html);
      }
    }
  }

  /** Show credits indicator above the search form (find_a_carpool page) */
  async function initCreditsIndicator(insertBeforeId) {
    const access = await fetchStatus();
    if (!access) return;
    const target = document.getElementById(insertBeforeId);
    if (!target) return;

    if (access.tier === 'search_pack') {
      const credits = access.search_credits || 0;
      const el = document.createElement('p');
      el.className = 'credits-indicator';
      el.id = 'credits-display';
      el.textContent = `Search Pack: ${credits} search${credits !== 1 ? 'es' : ''} remaining`;
      target.parentElement.insertBefore(el, target);
    }
  }

  /** Show a paywall overlay over a gated section */
  async function initPaywallOverlay(sectionSelector, tierNeeded, featureName) {
    const access = await fetchStatus();
    if (!access) return;

    const canUse = tierNeeded === 'monthly' ? access.can_create : access.can_search;
    if (canUse) return;

    const section = document.querySelector(sectionSelector);
    if (!section) return;

    const tierLabel = tierNeeded === 'monthly' ? 'Monthly or Annual plan' : 'Search Pack or subscription';
    const overlay = document.createElement('div');
    overlay.className = 'paywall-overlay';
    overlay.innerHTML = `
      <h3>🔒 ${featureName} Locked</h3>
      <p>A <strong>${tierLabel}</strong> is required to use this feature.</p>
      <a href="/pricing" class="btn btn-premium">View Plans</a>
      ${access.tier === 'none' ? '<p style="font-size:0.8rem;margin-top:0.5rem;color:var(--text-muted)">Your 30-day free trial has ended.</p>' : ''}
    `;
    section.appendChild(overlay);
  }

  /** Start Stripe Checkout for a given price + mode */
  async function startCheckout(priceId, mode) {
    const btn = event && event.currentTarget;
    if (btn) { btn.disabled = true; btn.textContent = 'Redirecting…'; }
    try {
      const res = await fetch('/api/subscription/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ price_id: priceId, mode }),
      });
      const json = await res.json();
      if (json.url) {
        window.location.href = json.url;
      } else {
        alert(json.error || 'Could not start checkout. Please try again.');
        if (btn) { btn.disabled = false; btn.textContent = 'Get Started'; }
      }
    } catch (err) {
      alert('Network error. Please try again.');
      if (btn) { btn.disabled = false; btn.textContent = 'Get Started'; }
    }
  }

  /** Open Stripe Customer Portal */
  async function openBillingPortal(btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Loading…'; }
    try {
      const res = await fetch('/api/subscription/portal', { method: 'POST' });
      const json = await res.json();
      if (json.url) {
        window.location.href = json.url;
      } else {
        alert(json.error || 'Could not open billing portal.');
        if (btn) { btn.disabled = false; btn.textContent = 'Manage Billing'; }
      }
    } catch {
      alert('Network error. Please try again.');
      if (btn) { btn.disabled = false; btn.textContent = 'Manage Billing'; }
    }
  }

  /** Confirm and execute cancellation (called from inline cancel panel) */
  async function confirmCancel(btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Cancelling…'; }
    try {
      const res = await fetch('/api/subscription/cancel', { method: 'POST' });
      const json = await res.json();
      if (json.ok) {
        // Show success message in the panel instead of alert
        const panel = document.getElementById('cancel-confirm');
        if (panel) {
          panel.style.borderColor = 'var(--border)';
          panel.style.background = 'transparent';
          panel.innerHTML = '<p style="margin:0;color:var(--text-muted);font-size:0.9rem;">Your subscription has been cancelled. You keep access until your billing period ends.</p>';
        }
      } else {
        if (btn) { btn.disabled = false; btn.textContent = 'Yes, cancel my plan'; }
        const panel = document.getElementById('cancel-confirm');
        const errEl = panel && panel.querySelector('.cancel-error');
        if (panel && !errEl) {
          panel.insertAdjacentHTML('beforeend', `<p class="cancel-error" style="color:var(--danger);font-size:0.85rem;margin:0;">${json.error || 'Could not cancel. Please try again or use Manage Billing.'}</p>`);
        }
      }
    } catch {
      if (btn) { btn.disabled = false; btn.textContent = 'Yes, cancel my plan'; }
    }
  }

  /** @deprecated Use confirmCancel via inline panel instead */
  async function cancelSubscription(btn) {
    document.getElementById('cancel-confirm') ? (document.getElementById('cancel-confirm').hidden = false) : confirmCancel(btn);
  }

  // Expose globally
  window.C2ASub = {
    fetchStatus,
    initSubscriptionBadge,
    initTrialBanner,
    initCreditsIndicator,
    initPaywallOverlay,
    startCheckout,
    openBillingPortal,
    cancelSubscription,
    confirmCancel,
  };
})();
