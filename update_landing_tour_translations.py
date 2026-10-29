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
    ('', 'Recurring'): (None, 'आवर्ती', 'आवर्ती'),
    ('', 'Set it once. It runs itself.'): (None, 'एक बार सेट करें। यह खुद चलता है।', 'एकदा सेट करा. ते स्वतः चालते.'),
    ('', 'Salary, rent, SIPs and premiums are posted on their due dates, with a reminder before each one. Nothing to remember.'): (None, 'सैलरी, किराया, SIP और प्रीमियम अपनी देय तारीख़ पर दर्ज हो जाते हैं, हर एक से पहले रिमाइंडर के साथ। कुछ याद रखने की ज़रूरत नहीं।', 'पगार, भाडे, SIP आणि प्रीमियम देय तारखेला नोंदवले जातात, प्रत्येकापूर्वी स्मरणासह. काहीही लक्षात ठेवायची गरज नाही.'),
    ('', 'Accounts'): (None, 'खाते', 'खाती'),
    ('', 'Every Indian account in one place'): (None, 'हर भारतीय खाता, एक जगह', 'प्रत्येक भारतीय खाते, एका ठिकाणी'),
    ('', 'Savings, FDs, PPF, EPF, NPS, demat, gold, property and cards, grouped and added up into one net worth.'): (None, 'बचत, FD, PPF, EPF, NPS, डीमैट, सोना, प्रॉपर्टी और कार्ड, समूहों में बँटे और एक कुल संपत्ति में जुड़े हुए।', 'बचत, FD, PPF, EPF, NPS, डीमॅट, सोने, मालमत्ता आणि कार्ड, गटांमध्ये विभागलेली आणि एका एकूण संपत्तीत जोडलेली.'),
    ('', 'TMR Flows'): (None, 'TMR फ़्लोज़', 'TMR फ्लोज'),
    ('', 'Tell it what happened in your life'): (None, 'बताइए आपकी ज़िंदगी में क्या हुआ', 'सांगा तुमच्या आयुष्यात काय घडले'),
    ('', '"I started a new job." "I took a loan." One short form sets up the account, the recurring entry and the reminders linked behind it.'): (None, '"मैंने नई नौकरी शुरू की।" "मैंने लोन लिया।" एक छोटा फ़ॉर्म इसके पीछे जुड़ा खाता, आवर्ती प्रविष्टि और रिमाइंडर सेट कर देता है।', '"मी नवी नोकरी सुरू केली." "मी कर्ज घेतले." एक छोटा फॉर्म त्यामागील खाते, आवर्ती नोंद आणि स्मरणे सेट करतो.'),
    ('', 'Family'): (None, 'परिवार', 'कुटुंब'),
    ('', 'One family. One true net worth.'): (None, 'एक परिवार। एक सच्ची कुल संपत्ति।', 'एक कुटुंब. एकच खरी एकूण संपत्ती.'),
    ('', 'Each of you keeps your own account and shares only what you choose. The family sees the combined picture of what was shared, never a transaction.'): (None, 'आप में से हर कोई अपना खाता रखता है और सिर्फ़ वही साझा करता है जो आप चुनते हैं। परिवार साझा की गई चीज़ों की संयुक्त तस्वीर देखता है, कभी कोई लेन-देन नहीं।', 'तुमच्यापैकी प्रत्येकाचे स्वतःचे खाते असते आणि तुम्ही निवडलेलेच शेअर करता. कुटुंबाला शेअर केलेल्या गोष्टींचे एकत्रित चित्र दिसते, कधीही व्यवहार नाही.'),
    ('', 'Privacy'): (None, 'गोपनीयता', 'गोपनीयता'),
    ('', 'Private by default. Shared by choice.'): (None, 'डिफ़ॉल्ट रूप से निजी। अपनी पसंद से साझा।', 'डिफॉल्टनुसार खाजगी. निवडीनुसार शेअर.'),
    ('', 'No SMS reading, no bank login. In a family, nothing is shared until you switch it on, and you can preview exactly what they will see.'): (None, 'न SMS पढ़ना, न बैंक लॉगिन। परिवार में, जब तक आप चालू न करें कुछ भी साझा नहीं होता, और आप पहले से देख सकते हैं कि उन्हें ठीक क्या दिखेगा।', 'SMS वाचन नाही, बँक लॉगिन नाही. कुटुंबात, तुम्ही चालू करेपर्यंत काहीही शेअर होत नाही आणि त्यांना नेमके काय दिसेल ते तुम्ही आधी पाहू शकता.'),
    ('', 'Open this screen in the live demo'): (None, 'इस स्क्रीन को लाइव डेमो में खोलें', 'ही स्क्रीन लाइव्ह डेमोमध्ये उघडा'),
    ('', 'Quick add'): (None, 'त्वरित जोड़ें', 'झटपट जोडा'),
    ('', "Type it. It's logged."): (None, 'लिखिए, दर्ज हो गया।', 'टाइप करा, नोंद झाली.'),
    ('', 'Write what you spent the way you would say it. Amount, category and account are filled in, and it lands in your ledger.'): (None, 'जैसे बोलते हैं वैसे लिखिए कि क्या खर्च किया। रकम, श्रेणी और खाता अपने-आप भर जाते हैं और यह आपके बही-खाते में दर्ज हो जाता है।', 'तुम्ही बोलता तसे काय खर्च केले ते लिहा. रक्कम, श्रेणी आणि खाते आपोआप भरले जाते आणि ते तुमच्या खातेवहीत नोंदले जाते.'),
    ('', 'Watch your net worth grow'): (None, 'अपनी कुल संपत्ति को बढ़ते देखें', 'तुमची एकूण संपत्ती वाढताना पहा'),
    ('', 'One real-time number across savings, SIPs, loans and cards, shown as a trajectory you can tap into.'): (None, 'बचत, SIP, लोन और कार्ड पर एक रीयल-टाइम संख्या, एक ऐसे सफ़र के रूप में जिसमें आप टैप करके जा सकते हैं।', 'बचत, SIP, कर्जे आणि कार्ड यांवरील एक रिअल-टाइम आकडा, ज्यात तुम्ही टॅप करून जाऊ शकता असा प्रवास म्हणून.'),
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
