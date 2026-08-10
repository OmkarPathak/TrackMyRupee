# मोबाइल ॲप (iOS)

Capacitor चा वापर करून तुमच्या iPhone वर TrackMyRupee नेटिव्ह ॲप म्हणून इन्स्टॉल करा.

!!! note "मोबाइल ॲप काय आहे?"
    हे iOS ॲप `https://trackmyrupee.com` लोड करणारे एक शेल आहे. हे लाईव्ह डेटा वापरते आणि यासाठी इंटरनेट कनेक्शन आवश्यक आहे.

---

## 1. पूर्वतयारी

- **macOS** आणि **Xcode**
- **Node.js** (v18+) आणि **npm**
- **CocoaPods**: `sudo gem install cocoapods`

---

## 2. बिल्ड स्टेप्स

```bash
cd mobile
npm install
npm run ios:sync
npm run ios:open
```

---

## 3. Xcode मध्ये साइनिंग निवडणे

1. Xcode मध्ये **App** निवडा.
2. **Signing and Capabilities** वर जा.
3. **Team** मध्ये तुमची Apple ID निवडा.

---

## संबंधित लिंक्स
- [स्व-होस्टिंग](../21-self-hosting/index.mr.md)
- [सुरुवात करा](../01-getting-started/index.mr.md)
