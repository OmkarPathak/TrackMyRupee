# मोबाइल ऐप (iOS)

Capacitor का उपयोग करके अपने iPhone पर TrackMyRupee को एक नेटिव ऐप के रूप में इंस्टॉल करें और चलाएं।

!!! note "मोबाइल ऐप क्या है"
    iOS ऐप एक Capacitor शेल है जो `https://trackmyrupee.com` लोड करता है। यह वास्तविक समय में आपके वास्तविक खाते और डेटा का उपयोग करता है। यह एक अलग ऑफ़लाइन ऐप नहीं है और इसके लिए इंटरनेट कनेक्शन की आवश्यकता होती है। इसे बनाने के लिए Xcode के साथ Mac की आवश्यकता होती है।

---

## 1. पूर्वपेक्षाएँ

शुरू करने से पहले, सुनिश्चित करें कि आपके मैक पर निम्नलिखित इंस्टॉल हैं:

- नवीनतम स्थिर **Xcode** के साथ **macOS**
- **Node.js** (संस्करण 18 या बाद का) और **npm**
- **CocoaPods**: अपने टर्मिनल में `sudo gem install cocoapods` चलाएं
- रिपॉजिटरी क्लोन की गई: `git clone <repository-url> && cd django-finance-tracker`

---

## 2. Capacitor सिंक करना

`mobile/` डायरेक्टरी में नेविगेट करें और Capacitor ब्रिज को सिंक करें:

```bash
cd mobile
npm install
npm run ios:sync
```

---

## 3. Xcode में खोलना

Xcode में प्रोजेक्ट अपने आप खोलने के लिए निम्नलिखित कमांड चलाएं:

```bash
npm run ios:open
```

---

## 4. अपनी साइनिंग टीम चुनना

1. Xcode में, बाएं पैनल में **App** टारगेट पर क्लिक करें।
2. **Signing and Capabilities** पर जाएं।
3. **Team** के तहत अपनी Apple ID चुनें।
4. यदि Xcode बंडल ID विरोध दिखाता है, तो **Bundle Identifier** को बदलकर `com.yourname.trackmyrupee` जैसा कुछ विशिष्ट करें।

---

## 5. बनाना और चलाना (Build & Run)

- **केवल निर्माण (Build only)**: `Cmd + B` दबाएं
- **कनेक्टेड iPhone पर चलाएं**: शीर्ष बार में अपना डिवाइस चुनें और `Cmd + R` दबाएं

!!! warning "iPhone पर पहला लॉन्च: Untrusted Developer"
    जब आप किसी मुफ़्त व्यक्तिगत टीम के माध्यम से इंस्टॉल करते हैं, तो iOS पहली बार खोलने पर "Untrusted Developer" त्रुटि दिखाता है। इसे ठीक करने के लिए, **Settings → General → VPN and Device Management → [आपकी Apple ID] → Trust** पर जाएं।

---

## 6. मुफ़्त बनाम सशुल्क विकास (Free vs. Paid)

| सुविधा | मुफ़्त (Personal Team) | सशुल्क ($99/वर्ष Apple Developer Program) |
|---|---|---|
| लागत | मुफ़्त | $99 USD प्रति वर्ष |
| अपने iPhone पर चलाएं | हाँ | हाँ |
| ऐप डिवाइस पर समाप्त होता है | हर 7 दिन में | नहीं |
| App Store वितरण | नहीं | हाँ |
| TestFlight बीटा | नहीं | हाँ |

---

## संबंधित लिंक
- [स्व-होस्टिंग](../21-self-hosting/index.hi.md)
- [शुरुआत करें](../01-getting-started/index.hi.md)
