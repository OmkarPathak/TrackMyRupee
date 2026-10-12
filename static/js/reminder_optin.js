/* Daily 9 pm reminder opt-in.
 *
 * One place decides what a user sees about push reminders, from facts about this browser:
 *   ask          - can subscribe: show the "Remind me at 9 pm" button
 *   on           - already subscribed (on this or another device)
 *   blocked      - the browser has blocked notifications for the site
 *   ios-install  - iPhone/iPad Safari: push only works once added to the Home Screen
 *   unsupported  - the browser has no push support
 *
 * Markup: any [data-reminder-optin] element. Its children [data-reminder-when="<state>"] are shown for
 * that state only; [data-reminder-enable] starts the browser prompt (always from a tap, never on load);
 * [data-reminder-dismiss] hides a "card" for longer each time it is dismissed.
 * The state logic is a pure function so it can be tested without a browser.
 */
(function (root) {
    'use strict';

    var DISMISS_WAITS_DAYS = [3, 7, 14, 30];   // after the 4th "Not now" the card stays away
    var STORAGE_KEY = 'tmr_reminder_ask';

    function computeState(env) {
        if (!env.supported) {
            return env.ios && !env.standalone ? 'ios-install' : 'unsupported';
        }
        if (env.permission === 'denied') return 'blocked';
        if (env.serverSubscribed) return 'on';
        return 'ask';
    }

    function nextAskAllowed(record, now) {
        if (!record) return true;
        if (record.n >= DISMISS_WAITS_DAYS.length) return false;
        return now >= record.until;
    }

    function recordDismissal(record, now) {
        var n = (record && record.n ? record.n : 0) + 1;
        var days = DISMISS_WAITS_DAYS[Math.min(n, DISMISS_WAITS_DAYS.length) - 1];
        return { n: n, until: now + days * 24 * 60 * 60 * 1000 };
    }

    var logic = {
        computeState: computeState,
        nextAskAllowed: nextAskAllowed,
        recordDismissal: recordDismissal,
        DISMISS_WAITS_DAYS: DISMISS_WAITS_DAYS
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = logic;
        return;
    }

    var win = root;
    var doc = win.document;

    function readRecord() {
        try { return JSON.parse(win.localStorage.getItem(STORAGE_KEY)); } catch (e) { return null; }
    }
    function writeRecord(rec) {
        try { win.localStorage.setItem(STORAGE_KEY, JSON.stringify(rec)); } catch (e) { /* private mode */ }
    }

    function environment() {
        var ua = win.navigator.userAgent || '';
        var ios = /iPad|iPhone|iPod/.test(ua) ||
            (win.navigator.platform === 'MacIntel' && win.navigator.maxTouchPoints > 1);
        return {
            supported: 'Notification' in win && 'serviceWorker' in win.navigator && 'PushManager' in win,
            ios: ios,
            standalone: (win.matchMedia && win.matchMedia('(display-mode: standalone)').matches) ||
                win.navigator.standalone === true,
            permission: 'Notification' in win ? win.Notification.permission : 'default',
            serverSubscribed: win.IS_WEBPUSH_SUBSCRIBED === true
        };
    }

    function urlBase64ToUint8Array(base64String) {
        var cleaned = base64String.trim().replace(/^["']|["']$/g, '');
        var padding = '='.repeat((4 - cleaned.length % 4) % 4);
        var raw = win.atob((cleaned + padding).replace(/-/g, '+').replace(/_/g, '/'));
        var out = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; ++i) out[i] = raw.charCodeAt(i);
        return out;
    }

    // Must be called from a tap/click handler: the permission prompt needs a user gesture.
    function enable() {
        if (!win.VAPID_PUBLIC_KEY) return Promise.reject(new Error('Push is not configured'));
        return win.Notification.requestPermission().then(function (permission) {
            if (permission !== 'granted') throw new Error('permission-' + permission);
            return win.navigator.serviceWorker.ready;
        }).then(function (registration) {
            return registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(win.VAPID_PUBLIC_KEY)
            });
        }).then(function (subscription) {
            return win.fetch(win.TMR_SAVE_PUSH_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    status_type: 'subscribe',
                    subscription: subscription.toJSON(),
                    browser: win.navigator.userAgent.substring(0, 49),
                    user_agent: win.navigator.userAgent,
                    group: ''
                })
            });
        }).then(function (response) {
            if (!response.ok) throw new Error('save-' + response.status);
            win.IS_WEBPUSH_SUBSCRIBED = true;
            // The navbar/banner "Enable" buttons from base.html: show them as on without a reload
            doc.querySelectorAll('.enable-push-btn').forEach(function (btn) {
                btn.innerHTML = '<i class="bi bi-check-circle-fill me-1 text-success"></i> On';
                btn.classList.remove('btn-outline-primary');
                btn.classList.add('btn-light', 'text-success');
                btn.disabled = true;
            });
        });
    }

    function pwaPromoVisible() {
        var promo = doc.getElementById('pwa-promo-card');
        return !!promo && win.getComputedStyle(promo).display !== 'none';
    }

    function visibleStates(variant) {
        if (variant === 'card') return ['ask', 'ios-install'];
        if (variant === 'onboarding') return ['ask', 'ios-install'];
        return ['ask', 'on', 'blocked', 'ios-install', 'unsupported'];   // settings: always say where it stands
    }

    function render(rootEl) {
        var variant = rootEl.getAttribute('data-variant') || 'card';
        var state = computeState(environment());
        var show = visibleStates(variant).indexOf(state) !== -1;

        if (show && variant === 'card') {
            if (!nextAskAllowed(readRecord(), Date.now())) show = false;
            // The install banner already explains the iPhone route; do not stack two cards
            if (state === 'ios-install' && pwaPromoVisible()) show = false;
        }

        rootEl.hidden = !show;
        rootEl.setAttribute('data-reminder-state', state);
        rootEl.querySelectorAll('[data-reminder-when]').forEach(function (el) {
            el.hidden = el.getAttribute('data-reminder-when') !== state;
        });
    }

    function renderAll() {
        doc.querySelectorAll('[data-reminder-optin]').forEach(render);
    }

    function init() {
        renderAll();
        doc.addEventListener('click', function (event) {
            var enableBtn = event.target.closest('[data-reminder-enable]');
            var dismissBtn = event.target.closest('[data-reminder-dismiss]');
            if (enableBtn) {
                var rootEl = enableBtn.closest('[data-reminder-optin]');
                var errorEl = rootEl && rootEl.querySelector('[data-reminder-error]');
                if (errorEl) errorEl.hidden = true;
                enableBtn.disabled = true;
                enable().then(renderAll).catch(function (err) {
                    var denied = /permission-/.test(String(err && err.message));
                    if (denied) {
                        renderAll();
                    } else if (errorEl) {
                        errorEl.hidden = false;
                    }
                }).then(function () { enableBtn.disabled = false; });
            } else if (dismissBtn) {
                writeRecord(recordDismissal(readRecord(), Date.now()));
                renderAll();
            }
        });
    }

    win.TMRReminder = { state: function () { return computeState(environment()); }, enable: enable, render: renderAll };

    // After "load" so the install banner's own script has already decided whether it is showing
    if (doc.readyState === 'complete') init(); else win.addEventListener('load', init);
})(typeof window !== 'undefined' ? window : this);
