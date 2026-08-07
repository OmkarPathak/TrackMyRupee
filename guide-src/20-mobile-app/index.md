# Mobile App (iOS)

Install and run TrackMyRupee on your iPhone as a native app using Capacitor, which wraps the live web app.

!!! note "What the mobile app is"
    The iOS app is a **Capacitor shell** that loads `https://trackmyrupee.com` — your real account, real data, real-time. It is not a separate offline app; it requires internet access. Building it requires a Mac with Xcode.

## Prerequisites

Before you begin, ensure you have:

- A **Mac** running macOS with the latest stable **Xcode** installed (download from the Mac App Store)
- **Node.js** (v18+) and **npm** — install via [nodejs.org](https://nodejs.org)
- **CocoaPods** — install with `sudo gem install cocoapods`
- The repo cloned locally: `git clone <repository-url> && cd django-finance-tracker`

## Build steps

### Step 1 — Sync Capacitor

Navigate to the `mobile/` directory and sync the Capacitor bridge. This copies plugins and config into the iOS project.

```bash
cd mobile
npm install
npm run ios:sync
```

### Step 2 — Open in Xcode

```bash
npm run ios:open
```

Or open `mobile/ios/App/App.xcworkspace` manually in Xcode (use `.xcworkspace`, **not** `.xcodeproj`).

<!-- TODO: screenshot (desktop, 1280x800) of Xcode with the App target selected -->
![Xcode project open](../screenshots/20-mobile-app/xcode-project-desktop.png)

### Step 3 — Select signing team

1. In Xcode, click the **App** target in the left panel
2. Go to **Signing & Capabilities**
3. Under **Team**, select your Apple ID (free personal team is fine for personal use)
4. If Xcode shows a bundle ID conflict, change **Bundle Identifier** to something unique like `com.yourname.trackmyrupee`

### Step 4 — Build and run

- **Build only**: `Cmd + B`
- **Run on a connected iPhone**: select your device in the top bar → `Cmd + R`
- **Archive for distribution**: **Product → Archive**, then **Distribute App** in the Organizer

!!! warning "First launch on iPhone — 'Untrusted Developer'"
    When you install via a free Personal Team, iOS shows an "Untrusted Developer" error on first open.
    Fix it: **Settings → General → VPN & Device Management → [your Apple ID] → Trust**.
    Make sure your iPhone has an active internet connection during this step (it contacts Apple's servers).

## Free vs. paid development

| | Free (Personal Team) | Paid ($99/year Apple Developer Program) |
|---|---|---|
| Cost | Free | $99 USD/year |
| Run on your own iPhone | ✅ | ✅ |
| App expires on device | Every 7 days | No |
| App Store distribution | ❌ | ✅ |
| TestFlight beta | ❌ | ✅ |
| Push notifications | ❌ | ✅ |
| Max active apps on device | 3 | Unlimited |

For personal use on your own phone, the free option is sufficient.

## Troubleshooting

### CocoaPods errors
```bash
cd mobile/ios/App
pod install
```

### "Communication with Apple failed" in Signing & Capabilities
1. Make sure your **physical iPhone** (not a simulator) is selected as the run destination in Xcode's top bar
2. Change the **Bundle Identifier** to a unique value (see Step 3 above)
3. Disable any VPN — they block Apple's signing servers
4. **Xcode → Settings → Accounts**: confirm your Apple ID is listed and shows a valid Development certificate

### App won't verify on iPhone
1. Ensure the iPhone has **Wi-Fi or cellular** (not just a local network without internet)
2. Disable VPN / AdGuard / custom DNS temporarily
3. **Settings → General → Date & Time → Set Automatically**: must be on
4. Delete the app → restart iPhone → re-run from Xcode

!!! example "Real-world use case"
    Ananya builds the app once on her Mac, signs it with her free Apple ID, and installs it on her iPhone. She re-runs it from Xcode every 7 days (takes 30 seconds) to renew the certificate. The app opens as a full-screen PWA-style experience with the bottom navigation bar, exactly like the browser version — but without the browser chrome.

## Related links

- [Self-Hosting](../21-self-hosting/index.md)
- [Getting Started](../01-getting-started/index.md)
