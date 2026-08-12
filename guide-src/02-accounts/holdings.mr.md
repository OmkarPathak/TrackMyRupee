# गुंतवणूक होल्डिंग्स आणि म्युच्युअल फंड पोर्टफोलिओ (Holdings)

स्वयंचलित NAV सिंक, एसआयपी ट्रॅकिंग आणि पोर्टफोलिओ गणनेसह तुमचे म्युच्युअल फंड, शेअर्स आणि गुंतवणुकीचे थेट मूल्य ट्रॅक करा.

---

## 1. नॅव्हिगेशन आणि माहिती

डेस्कटॉपवर **Sidebar → Net Worth → Holdings** द्वारे किंवा मोबाईलवर **More → Holdings** द्वारे पोर्टफोलिओ पहा (URL: `/holdings/`).

Holdings पेज तुमच्या म्युच्युअल फंड, शेअर्स, NPS आणि पीएफ खात्यांमधील सर्व सक्रिय गुंतवणुकीचा एकच डॅशबोर्ड प्रदान करते.

---

## 2. NAV गणना आणि पोर्टफोलिओ सूत्रे

TrackMyRupee तुमच्या गुंतवणुकीचे चालू मूल्य मोजण्यासाठी थेट NAV प्रणाली वापरते.

### सूत्रे

- **होल्डिंग मूल्य**

  $$\text{Current Valuation} = \text{Units} \times \text{Latest Unit NAV}$$

- **खात्याची एकूण शिल्लक**

  $$\text{Cost Basis Total} = \sum \left( \text{Units}_i \times \text{Average Cost}_i \right)$$

  $$\text{Uninvested Cash} = \max\left(0, \text{Ledger Balance} - \text{Cost Basis Total}\right)$$

  $$\text{Total Account Balance} = \sum \left( \text{Units}_i \times \text{Latest Unit NAV}_i \right) + \text{Uninvested Cash}$$

!!! note "दुरुस्ती लागू"
    ऑगस्ट 2026 पूर्वी, ट्रान्सफर केलेली रक्कम होल्डिंग लॉग केल्यानंतर दुप्पट मोजली जाण्याची शक्यता होती. आता ही गणना दुरुस्त केली आहे.

- **एकूण गुंतवणूक किंमत**

  $$\text{Total Invested} = \sum \left( \text{Units}_i \times \text{Average Cost}_i \right)$$

- **नफा / तोटा**

  $$\text{Unrealized Gain} = \text{Total Portfolio Valuation} - \text{Total Invested Cost}$$

---

## 3. SIP साठी आवर्ती हस्तांतरण (Recurring Transfer) कसे सेट करावे?

एसआयपी (SIP) म्हणजे तुमच्या बँक खात्यातून दरमहा ठराविक रक्कम तुमच्या म्युच्युअल फंड खात्यात हस्तांतरित करणे.

### टप्प्याटप्प्याने SIP सेटअप

1. **Add → Add Subscription** (किंवा **Sidebar → Subscriptions → Add**) वर जा.
2. **Transaction Type** मध्ये `TRANSFER` निवडा.
3. **Description** मध्ये तुमच्या SIP चे नाव टाका (उदा. "Monthly SIP - Parag Parikh").
4. **Amount** मध्ये दरमहा गुंतवणूक रक्कम टाका (उदा. `5000`).
5. **From Account** मध्ये तुमचे बँक खाते निवडा (उदा. HDFC Salary Account).
6. **To Account** मध्ये तुमचे म्युच्युअल फंड खाते निवडा.
7. **Frequency** मध्ये `Monthly` निवडा आणि SIP ची तारीख ठरवा.
8. **Save Subscription** वर क्लिक करा.

!!! info "SIP मुळे शिल्लक रकमेवर काय परिणाम होतो?"
  दरमहा ठराविक तारखेला TrackMyRupee आपोआप अंतर्गत हस्तांतरण (Internal Transfer) नोंदवते. पैसे बँकेतून निघून म्युच्युअल फंड खात्यात लेजर कॅश म्हणून जमा होतात. त्या क्षणी एकूण संपत्ती बदलत नाही, कारण पैसे तुमच्याच खात्यांमध्ये असतात. जेव्हा संबंधित होल्डिंग नोंदवता, तेवढा भाग वेगळ्या कॅशऐवजी होल्डिंगच्या मूल्यात मोजला जातो. जे अजून होल्डिंगला allocate झालेले नाही, तेवढेच uninvested cash म्हणून दिसते.

---

## 4. युनिट्स आणि NAV अपडेट करणे

### युनिट्स आणि खरेदी किंमत अपडेट करणे

ज्यावेळी नवीन युनिट्स जमा होतात:

1. **Holdings** (`/holdings/`) पेजवर जा.
2. **Add Holding** वर क्लिक करा किंवा जुनी होल्डिंग उघडा.
3. तुमचे एकूण **Units** आणि सरासरी खरेदी किंमत (**Avg Cost**) टाका.

### स्वयंचलित दैनिक NAV अपडेट

- **स्वयंचलित बॅकग्राउंड अपडेट**: भारतीय शेअर बाजार बंद झाल्यानंतर (दररोज रात्री ११ च्या सुमारास) ॲप आपोआप नवीनतम NAV अपडेट करते.
- **मॅन्युअल रिफ्रेश**: होल्डिंगच्या बाजूला असलेल्या **Refresh NAV** चिन्हावर क्लिक करून तुम्ही कधीही थेट NAV अपडेट करू शकता.

---

## संबंधित लिंक्स
- [खाती आणि निव्वळ संपत्ती](index.mr.md)
- [आवर्ती व्यवहार आणि सबस्क्रिप्शन](../05-transactions-recurring/index.mr.md)
- [हस्तांतरण](../06-transfers/index.mr.md)
- [विश्लेषण आणि आर्थिक आरोग्य](../11-analytics-and-health/index.mr.md)
