# Mobile App (iOS)

Install and run TrackMyRupee on your iPhone as a native app using Capacitor, which wraps the live web app.

!!! note "What the mobile app is"
    The iOS app is a Capacitor shell that loads `https://trackmyrupee.com`. It uses your real account and real data in real time. It is not a separate offline app and requires an internet connection. Building it requires a Mac with Xcode.

---

## 1. Prerequisites

Before you begin, make sure you have the following installed on your Mac:

- **macOS** with the latest stable **Xcode** (download from the Mac App Store)
- **Node.js** (version 18 or later) and **npm** from [nodejs.org](https://nodejs.org)
- **CocoaPods**: run `sudo gem install cocoapods` in your terminal
- The repository cloned locally: `git clone <repository-url> && cd django-finance-tracker`

---

## 2. Syncing Capacitor

Navigate to the `mobile/` directory and sync the Capacitor bridge. This copies the necessary plugins and configuration into the iOS project.

```bash
cd mobile
npm install
npm run ios:sync
```

---

## 3. Opening in Xcode

Run the following command to open the project in Xcode automatically:

```bash
npm run ios:open
```

Alternatively, open `mobile/ios/App/App.xcworkspace` manually in Xcode. Always open the `.xcworkspace` file, not the `.xcodeproj` file.

---

## 4. Selecting Your Signing Team

1. In Xcode, click the **App** target in the left panel.
2. Go to **Signing and Capabilities**.
3. Under **Team**, select your Apple ID. A free personal team is sufficient for personal use.
4. If Xcode shows a bundle ID conflict, change the **Bundle Identifier** to something unique such as `com.yourname.trackmyrupee`.

---

## 5. Building and Running

- **Build only**: Press `Cmd + B`
- **Run on a connected iPhone**: Select your device in the top bar and press `Cmd + R`
- **Archive for distribution**: Go to **Product → Archive**, then use **Distribute App** in the Organizer

!!! warning "First launch on iPhone: Untrusted Developer"
    When you install via a free Personal Team, iOS shows an "Untrusted Developer" error on first open. To fix it, go to **Settings → General → VPN and Device Management → [your Apple ID] → Trust**. Make sure your iPhone has an active internet connection during this step because it contacts Apple's servers to verify the certificate.

---

## 6. Free vs. Paid Development

| Feature | Free (Personal Team) | Paid ($99/year Apple Developer Program) |
|---|---|---|
| Cost | Free | $99 USD per year |
| Run on your own iPhone | Yes | Yes |
| App expires on device | Every 7 days | No |
| App Store distribution | No | Yes |
| TestFlight beta | No | Yes |
| Push notifications | No | Yes |
| Maximum active apps on device | 3 | Unlimited |

For personal use on your own phone, the free option is sufficient.

---

## 7. Troubleshooting

### CocoaPods errors

```bash
cd mobile/ios/App
pod install
```

### "Communication with Apple failed" in Signing and Capabilities

1. Make sure your physical iPhone (not a simulator) is selected as the run destination in Xcode's top bar.
2. Change the **Bundle Identifier** to a unique value (see step 4 above).
3. Disable any VPN because VPNs can block Apple's signing servers.
4. Go to **Xcode → Settings → Accounts** and confirm your Apple ID is listed with a valid Development certificate.

### App will not verify on iPhone

1. Ensure the iPhone has Wi-Fi or cellular data (not just a local network without internet access).
2. Temporarily disable VPN, AdGuard, or custom DNS settings.
3. Go to **Settings → General → Date and Time → Set Automatically** and make sure it is enabled.
4. Delete the app from your iPhone, restart the phone, and re-run from Xcode.

!!! example "Real-world use case"
    Ananya builds the app once on her Mac, signs it with her free Apple ID, and installs it on her iPhone. She re-runs it from Xcode every 7 days, which takes about 30 seconds, to renew the certificate. The app opens as a full-screen experience with the bottom navigation bar, identical to the browser version but without the browser chrome.

---

## Related Links
- [Self-Hosting](../21-self-hosting/index.md)
- [Getting Started](../01-getting-started/index.md)
