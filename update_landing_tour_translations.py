"""Hindi and Marathi for the landing page's product tour, merged into locale/{hi,mr}/LC_MESSAGES/django.po.

    python update_landing_tour_translations.py          # add or fill every entry below, then run compilemessages
    python update_landing_tour_translations.py --check  # list tour strings that still have no entry here

Same approach as update_family_translations.py: entries that already have a translation in the catalogue are left
alone. The tour strings are found by scanning the "The whole product" block of templates/landing.html; a test fails
if one of them has no Hindi or Marathi text. (The screens inside the tour imitate the app and stay in English.)
"""
import sys
from pathlib import Path

from update_family_translations import LOCALES, _catalogue, render, template_strings

ROOT = Path(__file__).resolve().parent
START, END = '<!-- THE WHOLE PRODUCT', '<!-- FAMILY SHARING -->'

# (context, msgid): (None, hindi, marathi)
ENTRIES = {
    ('', 'The whole product'): (None, 'पूरा प्रोडक्ट', 'संपूर्ण प्रॉडक्ट'),
    ('', 'See it work, before you sign up.'): (None, 'साइन अप करने से पहले इसे काम करते देखें।', 'साइन अप करण्यापूर्वी ते कसे चालते ते पहा.'),
    ('', 'This is what you get after you log in, on your computer or your phone. Pick a screen, or let it play.'): (None, 'लॉग इन करने के बाद आपको यही मिलता है, कंप्यूटर पर या फ़ोन पर। कोई स्क्रीन चुनें, या इसे अपने-आप चलने दें।', 'लॉग इन केल्यानंतर तुम्हाला हेच मिळते, संगणकावर किंवा फोनवर. एखादी स्क्रीन निवडा, किंवा ती आपोआप चालू द्या.'),
    ('', 'Device'): (None, 'डिवाइस', 'डिव्हाइस'),
    ('', 'Computer'): (None, 'कंप्यूटर', 'संगणक'),
    ('', 'Phone'): (None, 'फ़ोन', 'फोन'),
    ('', 'Pause'): (None, 'रोकें', 'थांबवा'),
    ('', 'Play'): (None, 'चलाएँ', 'चालवा'),
    ('', 'Product tour'): (None, 'प्रोडक्ट टूर', 'प्रॉडक्ट टूर'),
    ('', 'Installs like an app'): (None, 'ऐप की तरह इंस्टॉल होता है', 'अ‍ॅपसारखे इन्स्टॉल होते'),
    ('', 'Add to your home screen. No app store.'): (None, 'अपनी होम स्क्रीन पर जोड़ें। ऐप स्टोर की ज़रूरत नहीं।', 'तुमच्या होम स्क्रीनवर जोडा. अ‍ॅप स्टोअरची गरज नाही.'),
    ('', 'Works offline'): (None, 'ऑफ़लाइन भी चलता है', 'ऑफलाइनही चालते'),
    ('', 'Opens fast, even on a weak signal.'): (None, 'कमज़ोर सिग्नल में भी तेज़ी से खुलता है।', 'कमकुवत सिग्नलवरही पटकन उघडते.'),
    ('', "Type, don't tap through menus"): (None, 'मेन्यू में टैप करने की जगह टाइप करें', 'मेनूमध्ये टॅप करण्याऐवजी टाइप करा'),
    ('', '“swiggy 320” is enough.'): (None, '“swiggy 320” लिखना काफ़ी है।', '“swiggy 320” लिहिणे पुरेसे आहे.'),
    ('', 'And no bank login. You stay in control.'): (None, 'और कोई बैंक लॉगिन नहीं। नियंत्रण आपके हाथ में रहता है।', 'आणि बँक लॉगिन नाही. नियंत्रण तुमच्या हातात राहते.'),
    ('', 'The product tour needs JavaScript.'): (None, 'प्रोडक्ट टूर के लिए JavaScript ज़रूरी है।', 'प्रॉडक्ट टूरसाठी JavaScript आवश्यक आहे.'),
    ('', 'And more, built in'): (None, 'और भी बहुत कुछ, पहले से शामिल', 'आणि बरेच काही, आधीच समाविष्ट'),
    ('', 'English, Hindi and Marathi'): (None, 'अंग्रेज़ी, हिंदी और मराठी', 'इंग्रजी, हिंदी आणि मराठी'),
    ('', 'Any currency next to ₹'): (None, '₹ के साथ कोई भी मुद्रा', '₹ सोबत कोणतेही चलन'),
    ('', 'Recurring items and SIPs'): (None, 'आवर्ती खर्च और SIP', 'आवर्ती खर्च आणि SIP'),
    ('', 'Smart insights'): (None, 'स्मार्ट इनसाइट्स', 'स्मार्ट इनसाइट्स'),
    ('', 'Double-entry ledger'): (None, 'डबल-एंट्री बही-खाता', 'डबल-एंट्री खातेवही'),
    ('', 'CSV export anytime'): (None, 'कभी भी CSV एक्सपोर्ट', 'कधीही CSV एक्सपोर्ट'),
    ('', 'Quick add'): (None, 'त्वरित जोड़ें', 'झटपट जोडा'),
    ('', 'Your month at a glance'): (None, 'आपका महीना, एक नज़र में', 'तुमचा महिना, एका नजरेत'),
    ('', 'Net worth, income, spending and savings rate on one screen, with calm insights alongside.'): (None, 'कुल संपत्ति, आय, खर्च और बचत दर एक ही स्क्रीन पर, साथ में शांत इनसाइट्स।', 'एकूण संपत्ती, उत्पन्न, खर्च आणि बचत दर एकाच स्क्रीनवर, सोबत शांत इनसाइट्स.'),
    ('', "Type it. It's logged."): (None, 'लिखिए, दर्ज हो गया।', 'टाइप करा, नोंद झाली.'),
    ('', 'Write what you spent the way you would say it. Amount, category and account are filled in, and it lands in your ledger.'): (None, 'जैसे बोलते हैं वैसे लिखिए कि क्या खर्च किया। रकम, श्रेणी और खाता अपने-आप भर जाते हैं और यह आपके बही-खाते में दर्ज हो जाता है।', 'तुम्ही बोलता तसे काय खर्च केले ते लिहा. रक्कम, श्रेणी आणि खाते आपोआप भरले जाते आणि ते तुमच्या खातेवहीत नोंदले जाते.'),
    ('', 'Know what is safe to spend today'): (None, 'जानिए आज कितना खर्च करना सुरक्षित है', 'आज किती खर्च करणे सुरक्षित आहे ते जाणा'),
    ('', 'A daily number based on your budget, your pace and the days left in the month. Overspent categories surface first.'): (None, 'आपके बजट, आपकी रफ़्तार और महीने के बचे दिनों पर आधारित एक रोज़ाना की संख्या। जिन श्रेणियों में बजट से ज़्यादा खर्च हुआ, वे सबसे पहले दिखती हैं।', 'तुमचे बजेट, तुमचा वेग आणि महिन्यातील उरलेले दिवस यांवर आधारित दैनिक आकडा. बजेटपेक्षा जास्त खर्च झालेल्या श्रेणी आधी दिसतात.'),
    ('', 'Watch your net worth grow'): (None, 'अपनी कुल संपत्ति को बढ़ते देखें', 'तुमची एकूण संपत्ती वाढताना पहा'),
    ('', 'One real-time number across savings, SIPs, loans and cards, shown as a trajectory you can tap into.'): (None, 'बचत, SIP, लोन और कार्ड पर एक रीयल-टाइम संख्या, एक ऐसे सफ़र के रूप में जिसमें आप टैप करके जा सकते हैं।', 'बचत, SIP, कर्जे आणि कार्ड यांवरील एक रिअल-टाइम आकडा, ज्यात तुम्ही टॅप करून जाऊ शकता असा प्रवास म्हणून.'),
    ('', 'Every day, at a glance'): (None, 'हर दिन, एक नज़र में', 'प्रत्येक दिवस, एका नजरेत'),
    ('', 'Income, spending and investments by day, with salary days starred and busy days shaded darker.'): (None, 'दिन के हिसाब से आय, खर्च और निवेश, जिसमें वेतन के दिन तारे से चिह्नित और व्यस्त दिन गहरे रंग में हैं।', 'दिवसानुसार उत्पन्न, खर्च आणि गुंतवणूक, ज्यात पगाराचे दिवस तार्याने चिन्हांकित आणि व्यस्त दिवस गडद रंगात आहेत.'),
    ('', 'Loans with the full picture'): (None, 'पूरी तस्वीर के साथ लोन', 'संपूर्ण चित्रासह कर्जे'),
    ('', 'See how much is principal, how much is interest and how much is left, for every loan you carry.'): (None, 'आपके हर लोन में कितना मूलधन है, कितना ब्याज है और कितना बाकी है, यह देखें।', 'तुमच्या प्रत्येक कर्जात किती मुद्दल, किती व्याज आणि किती बाकी आहे ते पहा.'),
    ('', 'Goals with a finish date'): (None, 'पूरा होने की तारीख वाले लक्ष्य', 'पूर्ण होण्याच्या तारखेसह उद्दिष्टे'),
    ('', 'Set a target, move money towards it and see the date you will actually reach it at your real saving pace.'): (None, 'एक लक्ष्य तय करें, उसकी ओर पैसा बढ़ाएँ और अपनी असली बचत रफ़्तार से जानें कि आप उसे किस तारीख को पूरा करेंगे।', 'एक उद्दिष्ट ठरवा, त्याकडे पैसे वळवा आणि तुमच्या खऱ्या बचतीच्या वेगाने ते कोणत्या तारखेला पूर्ण होईल ते पहा.'),
}


def tour_block() -> str:
    text = (ROOT / 'templates' / 'landing.html').read_text(encoding='utf-8')
    return text[text.index(START):text.index(END)]


def find_strings():
    return template_strings(tour_block())


def merge(lang: str) -> tuple[int, int]:
    path, blocks, index = _catalogue(lang)
    added = filled = 0
    out = [b['text'] for b in blocks]
    for (ctx, msgid), value in ENTRIES.items():
        values = value[LOCALES[lang]]
        existing = index.get((ctx, msgid))
        entry = render(ctx, msgid, None, values)
        if existing is None:
            out.append('#: templates/landing.html\n' + entry)
            added += 1
        elif not existing['translated']:
            at = out.index(existing['text'])
            comments = [ln for ln in existing['text'].splitlines() if ln.startswith('#') and 'fuzzy' not in ln and not ln.startswith('#|')]
            out[at] = '\n'.join(comments + [entry])
            filled += 1
    path.write_text('\n\n'.join(out) + '\n', encoding='utf-8')
    return added, filled


def missing() -> list[str]:
    """Tour strings with no Hindi or Marathi text, neither here nor already in the catalogue."""
    catalogues = {lang: _catalogue(lang)[2] for lang in LOCALES}
    left = set()
    for ctx, msgid, _plural in find_strings():
        if (ctx, msgid) in ENTRIES:
            continue
        if all(catalogues[lang].get((ctx, msgid)) and catalogues[lang][(ctx, msgid)]['translated'] for lang in LOCALES):
            continue
        left.add(msgid)
    return sorted(left)


if __name__ == '__main__':
    if '--check' in sys.argv:
        left = missing()
        print('\n'.join(left) if left else 'every tour string has an entry')
        sys.exit(1 if left else 0)
    for code in LOCALES:
        added, filled = merge(code)
        print(f'{code}: {added} added, {filled} filled')
