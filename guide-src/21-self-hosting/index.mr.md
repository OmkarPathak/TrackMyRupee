# Docker सह स्व-होस्टिंग (Self-Hosting with Docker)

सुमारे १० मिनिटांत कोणत्याही लिनक्स सर्व्हरवर TrackMyRupee चा स्वतःचा खाजगी इन्स्टन्स चालवा.

!!! note "हे कोणासाठी आहे?"
    स्व-होस्टिंगमुळे तुम्हाला डेटावर पूर्ण नियंत्रण मिळते. तुमचे व्यवहार तुमच्या सर्व्हरच्या बाहेर जात नाहीत.

---

## 1. रिपोजिटरी क्लोन करा

```bash
git clone <repository-url>
cd django-finance-tracker
```

---

## 2. `.env` फाईल तयार करा

रिपोजिटरी रूटमध्ये `.env` फाईल बनवा:

```env
SECRET_KEY='replace-with-a-long-random-string'
DEBUG=False
```

---

## 3. कंटेनर सुरू करा

```bash
docker-compose up --build
```

ब्राऊझरमध्ये `http://localhost:8000` उघडा.

---

## संबंधित लिंक्स
- [सुरुवात करा](../01-getting-started/index.mr.md)
- [मोबाइल ॲप](../20-mobile-app/index.mr.md)
- [FAQ](../22-faq/index.mr.md)
