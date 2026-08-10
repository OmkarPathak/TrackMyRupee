# Docker के साथ स्व-होस्टिंग (Self-Hosting with Docker)

लगभग 10 मिनट में किसी भी लिनक्स सर्वर पर TrackMyRupee का अपना निजी इंस्टेंस चलाएं।

!!! note "यह किसके लिए है"
    स्व-होस्टिंग आपको पूर्ण डेटा नियंत्रण देती है। आपके लेनदेन कभी भी आपके सर्वर को नहीं छोड़ते हैं। यह मार्गदर्शिका Docker Compose पथ को कवर करती है, जो अनुशंसित दृष्टिकोण है।

---

## 1. रिपॉजिटरी क्लोन करें

```bash
git clone <repository-url>
cd django-finance-tracker
```

---

## 2. अपनी पर्यावरण फ़ाइल बनाएं

रिपॉजिटरी रूट में `.env` नाम की एक फ़ाइल बनाएं। न्यूनतम आपको निम्नलिखित सेटिंग्स की आवश्यकता है:

```env
# Required
SECRET_KEY='replace-with-a-long-random-string'
DEBUG=False

# Database (leave blank to use SQLite)
# DATABASE_URL='postgres://user:password@localhost:5432/dbname'
```

!!! warning ".env को कभी भी git में कमिट न करें"
    आपकी `.env` फ़ाइल में आपकी `SECRET_KEY` और कोई भी API क्रेडेंशियल होते हैं।

---

## 3. कंटेनर शुरू करें

```bash
docker-compose up --build
```

---

## 4. ऐप खोलें

अपने ब्राउज़र में `http://localhost:8000` पर नेविगेट करें।

---

## 5. मैनुअल पायथन सेटअप

यदि आप Docker के बिना चलाना पसंद करते हैं:

```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

---

## संबंधित लिंक
- [शुरुवात करें](../01-getting-started/index.hi.md)
- [मोबाइल ऐप](../20-mobile-app/index.hi.md)
- [FAQ](../22-faq/index.hi.md)
