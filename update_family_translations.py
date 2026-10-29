"""Hindi and Marathi for family sharing, merged into locale/{hi,mr}/LC_MESSAGES/django.po.

    python update_family_translations.py          # add or fill every entry below, then run compilemessages
    python update_family_translations.py --check  # list family strings that still have no entry here

Same approach as update_insight_translations.py: entries that already have a translation in the catalogue are
left alone (the words "Family", "Accounts" and so on may have been translated long ago). A plural entry gives
(singular, plural) for each language. The strings the family code owns are found by scanning its templates and
Python (find_strings below); a test fails if one of them has no Hindi or Marathi text.
"""
import ast
import re
import sys
from pathlib import Path

from update_insight_translations import (  # noqa: F401  (shared .po helpers)
    parse_blocks,
    quote,
    unquote,
)

ROOT = Path(__file__).resolve().parent
LOCALES = {'hi': 1, 'mr': 2}

# Where the family strings live. Add a path here when a new family template or module is added.
TEMPLATE_GLOBS = ['templates/expenses/family/**/*.html']
PYTHON_FILES = ['expenses/views/family.py', 'expenses/family/*.py']
# Strings in shared templates that family sharing added (the sidebar and the mobile menu)
SHARED_STRINGS = [('', 'Family'), ('', 'New')]

# (context, msgid): (msgid_plural or None, hindi, marathi)   for a plural: ((hi1, hi2), (mr1, mr2))
ENTRIES = {
    # ---- names and nouns ----
    ('', 'Family'): (None, 'परिवार', 'कुटुंब'),
    ('', 'Member'): (None, 'सदस्य', 'सदस्य'),
    ('', 'Members'): (None, 'सदस्य', 'सदस्य'),
    ('', 'Owner'): (None, 'मालिक', 'मालक'),
    ('', 'you'): (None, 'आप', 'तुम्ही'),
    ('', 'Our family'): (None, 'हमारा परिवार', 'आमचे कुटुंब'),
    ('', 'Plan ended'): (None, 'प्लान समाप्त', 'प्लॅन संपला'),
    ('', 'New link'): (None, 'नया लिंक', 'नवीन लिंक'),
    ('', 'Invitation link'): (None, 'आमंत्रण लिंक', 'आमंत्रण लिंक'),
    ('', 'Copy link'): (None, 'लिंक कॉपी करें', 'लिंक कॉपी करा'),
    ('', 'Copied'): (None, 'कॉपी हो गया', 'कॉपी केले'),
    ('', 'Send invite'): (None, 'आमंत्रण भेजें', 'आमंत्रण पाठवा'),
    ('', 'Invite someone'): (None, 'किसी को आमंत्रित करें', 'कोणालातरी आमंत्रित करा'),
    ('', 'Leave family'): (None, 'परिवार छोड़ें', 'कुटुंब सोडा'),
    ('', 'Join family'): (None, 'परिवार से जुड़ें', 'कुटुंबात सामील व्हा'),
    ('', 'Join a family'): (None, 'परिवार से जुड़ें', 'कुटुंबात सामील व्हा'),
    ('', 'Open Family'): (None, 'परिवार खोलें', 'कुटुंब उघडा'),
    ('', 'Create your family'): (None, 'अपना परिवार बनाएँ', 'तुमचे कुटुंब तयार करा'),
    ('', 'Start 7-day Pro trial'): (None, '7 दिन का प्रो ट्रायल शुरू करें', '7 दिवसांचा प्रो ट्रायल सुरू करा'),
    ('', 'Back to your dashboard'): (None, 'अपने डैशबोर्ड पर वापस जाएँ', 'तुमच्या डॅशबोर्डवर परत जा'),
    ('', 'Ends %(date)s'): (None, '%(date)s को समाप्त', '%(date)s रोजी संपते'),
    ('', 'Joined %(date)s'): (None, '%(date)s को जुड़े', '%(date)s रोजी सामील झाले'),
    ('', '%(used)s of %(max)s seats used'): (None, '%(max)s में से %(used)s स्थान भरे हैं', '%(max)s पैकी %(used)s जागा भरल्या आहेत'),
    ('', 'Invitation expires in %(left)s'): (None, 'आमंत्रण %(left)s में समाप्त होगा', 'आमंत्रण %(left)s मध्ये संपेल'),

    # ---- empty state and the family home ----
    ('', 'Share the big picture with your family'): (None, 'पूरी तस्वीर अपने परिवार के साथ साझा करें', 'संपूर्ण चित्र तुमच्या कुटुंबासोबत शेअर करा'),
    ('', "See your family's combined net worth, and who holds which cards, policies and accounts. You choose which of your accounts to share."): (
        None, 'अपने परिवार की संयुक्त कुल संपत्ति देखें, और यह भी कि कौन-से कार्ड, पॉलिसी और खाते किसके पास हैं। आप तय करते हैं कि अपने कौन-से खाते साझा करने हैं।',
        'तुमच्या कुटुंबाची एकत्रित एकूण संपत्ती पहा, आणि कोणते कार्ड, पॉलिसी व खाती कोणाकडे आहेत हेही. तुमची कोणती खाती शेअर करायची हे तुम्ही ठरवता.'),
    ('', "See your family's combined net worth and who holds which cards, policies and accounts. You choose which of your accounts to share."): (
        None, 'अपने परिवार की संयुक्त कुल संपत्ति देखें, और यह भी कि कौन-से कार्ड, पॉलिसी और खाते किसके पास हैं। आप तय करते हैं कि अपने कौन-से खाते साझा करने हैं।',
        'तुमच्या कुटुंबाची एकत्रित एकूण संपत्ती पहा, आणि कोणते कार्ड, पॉलिसी व खाती कोणाकडे आहेत हेही. तुमची कोणती खाती शेअर करायची हे तुम्ही ठरवता.'),
    ('', 'Nothing is shared until you choose it.'): (None, 'जब तक आप न चुनें, कुछ भी साझा नहीं होता।', 'तुम्ही निवडेपर्यंत काहीही शेअर होत नाही.'),
    ('', 'Family sees names, types and amounts, never transactions or account numbers.'): (
        None, 'परिवार को नाम, प्रकार और राशि दिखती है, लेन-देन या खाता नंबर कभी नहीं।',
        'कुटुंबाला नावे, प्रकार आणि रक्कम दिसते, व्यवहार किंवा खाते क्रमांक कधीही नाही.'),
    ('', 'Family sees names, types and amounts, never transactions, categories, notes or account numbers.'): (
        None, 'परिवार को नाम, प्रकार और राशि दिखती है, लेन-देन, श्रेणियाँ, नोट या खाता नंबर कभी नहीं।',
        'कुटुंबाला नावे, प्रकार आणि रक्कम दिसते, व्यवहार, श्रेणी, नोंदी किंवा खाते क्रमांक कधीही नाही.'),
    ('', 'You can stop sharing at any time.'): (None, 'आप कभी भी साझा करना बंद कर सकते हैं।', 'तुम्ही केव्हाही शेअरिंग थांबवू शकता.'),
    ('', 'Family sharing needs a Pro plan, and so does everyone in your family.'): (
        None, 'परिवार के साथ साझा करने के लिए प्रो प्लान चाहिए, और आपके परिवार में सभी को भी।',
        'कुटुंबासोबत शेअर करण्यासाठी प्रो प्लॅन लागतो, आणि तुमच्या कुटुंबातील प्रत्येकालाही.'),
    ('', 'Your invitation link is ready'): (None, 'आपका आमंत्रण लिंक तैयार है', 'तुमचा आमंत्रण लिंक तयार आहे'),
    ('', 'We emailed it to %(email)s. You can also copy it and send it yourself. It works once, and it is shown only now.'): (
        None, 'हमने इसे %(email)s को ईमेल कर दिया है। आप इसे कॉपी करके खुद भी भेज सकते हैं। यह एक ही बार काम करता है और सिर्फ़ अभी दिखाया जा रहा है।',
        'आम्ही ते %(email)s ला ईमेल केले आहे. तुम्ही ते कॉपी करून स्वतःही पाठवू शकता. ते एकदाच चालते आणि फक्त आत्ताच दाखवले आहे.'),
    ('', 'Copy it and send it to them. It works once, and it is shown only now.'): (
        None, 'इसे कॉपी करके उन्हें भेजें। यह एक ही बार काम करता है और सिर्फ़ अभी दिखाया जा रहा है।',
        'ते कॉपी करून त्यांना पाठवा. ते एकदाच चालते आणि फक्त आत्ताच दाखवले आहे.'),
    ('', 'Remove this person from the family? Everything they shared stops being visible straight away.'): (
        None, 'इस व्यक्ति को परिवार से हटाएँ? उन्होंने जो साझा किया वह तुरंत दिखना बंद हो जाएगा।',
        'या व्यक्तीला कुटुंबातून काढायचे? त्यांनी शेअर केलेले सर्व काही लगेच दिसणे बंद होईल.'),
    ('', 'Leave the email empty to get a link you can send yourself. They need their own TrackMyRupee account and a Pro plan. When they join they see nothing until you share an account.'): (
        None, 'ईमेल खाली छोड़ें तो आपको एक लिंक मिलेगा जिसे आप खुद भेज सकते हैं। उन्हें अपना TrackMyRupee खाता और प्रो प्लान चाहिए। जुड़ने पर उन्हें तब तक कुछ नहीं दिखता जब तक आप कोई खाता साझा न करें।',
        'ईमेल रिकामा ठेवला तर तुम्हाला एक लिंक मिळेल जो तुम्ही स्वतः पाठवू शकता. त्यांना स्वतःचे TrackMyRupee खाते आणि प्रो प्लॅन हवा. सामील झाल्यावर तुम्ही एखादे खाते शेअर करेपर्यंत त्यांना काहीही दिसत नाही.'),
    ('', 'What family can and cannot see'): (None, 'परिवार क्या देख सकता है और क्या नहीं', 'कुटुंब काय पाहू शकते आणि काय नाही'),
    ('', 'The same for everyone, always.'): (None, 'सभी के लिए हमेशा एक जैसा।', 'सर्वांसाठी नेहमी सारखे.'),
    ('', 'Can see, for accounts you share'): (None, 'आपके साझा किए खातों के लिए देख सकते हैं', 'तुम्ही शेअर केलेल्या खात्यांसाठी पाहू शकतात'),
    ('', 'Name (or a nickname you give it), type and who holds it'): (
        None, 'नाम (या आपका दिया हुआ उपनाम), प्रकार और यह किसके पास है', 'नाव (किंवा तुम्ही दिलेले टोपणनाव), प्रकार आणि ते कोणाकडे आहे'),
    ('', 'The balance or amount, if you choose to show it'): (None, 'शेष या राशि, अगर आप दिखाना चुनें', 'शिल्लक किंवा रक्कम, तुम्ही दाखवायचे ठरवले तर'),
    ('', 'Credit cards: limit, used credit and billing date'): (
        None, 'क्रेडिट कार्ड: सीमा, इस्तेमाल की गई राशि और बिलिंग तारीख', 'क्रेडिट कार्ड: मर्यादा, वापरलेली रक्कम आणि बिलिंग तारीख'),
    ('', 'Insurance: cover, premium, next due date and who it covers'): (
        None, 'बीमा: कवर, प्रीमियम, अगली देय तारीख और यह किसे कवर करता है', 'विमा: कव्हर, प्रीमियम, पुढील देय तारीख आणि तो कोणाला कव्हर करतो'),
    ('', 'Loans: outstanding amount and EMI'): (None, 'लोन: बकाया राशि और EMI', 'कर्ज: थकबाकी रक्कम आणि EMI'),
    ('', 'Can never see'): (None, 'कभी नहीं देख सकते', 'कधीही पाहू शकत नाहीत'),
    ('', 'Any transaction, category, note or receipt'): (None, 'कोई लेन-देन, श्रेणी, नोट या रसीद', 'कोणताही व्यवहार, श्रेणी, नोंद किंवा पावती'),
    ('', 'Holdings inside an account'): (None, 'खाते के भीतर की होल्डिंग्स', 'खात्याच्या आतील होल्डिंग्ज'),
    ('', 'Account or policy numbers'): (None, 'खाता या पॉलिसी नंबर', 'खाते किंवा पॉलिसी क्रमांक'),
    ('', 'Accounts you have not shared, or that they exist'): (
        None, 'वे खाते जो आपने साझा नहीं किए, या उनका अस्तित्व', 'तुम्ही शेअर न केलेली खाती, किंवा ती अस्तित्वात असल्याची माहिती'),
    ('', 'Leave the family? Everything you shared stops being visible straight away, and so does everything shared with you.'): (
        None, 'परिवार छोड़ें? आपने जो साझा किया वह तुरंत दिखना बंद हो जाएगा, और आपके साथ साझा किया गया सब कुछ भी।',
        'कुटुंब सोडायचे? तुम्ही शेअर केलेले सर्व काही लगेच दिसणे बंद होईल, आणि तुमच्यासोबत शेअर केलेले सर्व काहीही.'),
    ('', 'If you leave, everything you shared stops being visible straight away, and so does everything shared with you.'): (
        None, 'अगर आप छोड़ते हैं, तो आपने जो साझा किया वह तुरंत दिखना बंद हो जाता है, और आपके साथ साझा किया गया सब कुछ भी।',
        'तुम्ही सोडल्यास, तुम्ही शेअर केलेले सर्व काही लगेच दिसणे बंद होते, आणि तुमच्यासोबत शेअर केलेले सर्व काहीही.'),

    # ---- the invitation page ----
    ('', '%(name)s invited you to join a family'): (
        None, '%(name)s ने आपको एक परिवार से जुड़ने के लिए आमंत्रित किया है', '%(name)s यांनी तुम्हाला एका कुटुंबात सामील होण्यासाठी आमंत्रित केले आहे'),
    ('', '%(name)s invited you to share finances'): (
        None, '%(name)s ने आपको वित्त साझा करने के लिए आमंत्रित किया है', '%(name)s यांनी तुम्हाला आर्थिक माहिती शेअर करण्यासाठी आमंत्रित केले आहे'),
    ('', 'You will see the combined net worth of what each member shares, and who holds which cards, policies and accounts.'): (
        None, 'आप हर सदस्य द्वारा साझा की गई चीज़ों की संयुक्त कुल संपत्ति देखेंगे, और यह भी कि कौन-से कार्ड, पॉलिसी और खाते किसके पास हैं।',
        'प्रत्येक सदस्य जे शेअर करतो त्याची एकत्रित एकूण संपत्ती तुम्ही पाहाल, आणि कोणते कार्ड, पॉलिसी व खाती कोणाकडे आहेत हेही.'),
    ('', 'Joining shares nothing. Nobody sees your accounts until you choose.'): (
        None, 'जुड़ने से कुछ भी साझा नहीं होता। जब तक आप न चुनें, कोई आपके खाते नहीं देखता।',
        'सामील झाल्याने काहीही शेअर होत नाही. तुम्ही निवडेपर्यंत कोणीही तुमची खाती पाहत नाही.'),
    ('', 'You can stop sharing, or leave, at any time.'): (None, 'आप कभी भी साझा करना बंद कर सकते हैं, या छोड़ सकते हैं।', 'तुम्ही केव्हाही शेअरिंग थांबवू किंवा सोडू शकता.'),
    ('', 'I understand that the accounts I choose to share will be visible to the members of this family.'): (
        None, 'मुझे पता है कि मेरे चुने हुए साझा खाते इस परिवार के सदस्यों को दिखाई देंगे।',
        'मला हे समजले आहे की मी शेअर करण्यासाठी निवडलेली खाती या कुटुंबातील सदस्यांना दिसतील.'),
    ('', 'To join a family you need a Pro plan, and so does everyone in it.'): (
        None, 'परिवार से जुड़ने के लिए आपको प्रो प्लान चाहिए, और उसमें सभी को भी।', 'कुटुंबात सामील होण्यासाठी तुम्हाला प्रो प्लॅन हवा, आणि त्यातील प्रत्येकालाही.'),
    ('', 'Joining shares nothing. %(name)s cannot see your data, and you see only what they choose to share.'): (
        None, 'जुड़ने से कुछ भी साझा नहीं होता। %(name)s आपका डेटा नहीं देख सकते, और आप वही देखेंगे जो वे साझा करना चुनें।',
        'सामील झाल्याने काहीही शेअर होत नाही. %(name)s तुमचा डेटा पाहू शकत नाहीत, आणि ते जे शेअर करायचे निवडतील तेच तुम्ही पाहाल.'),
    ('', 'You are already in this family'): (None, 'आप पहले से इस परिवार में हैं', 'तुम्ही आधीच या कुटुंबात आहात'),
    ('', 'You are already in a family'): (None, 'आप पहले से एक परिवार में हैं', 'तुम्ही आधीच एका कुटुंबात आहात'),
    ('', 'A person can be in one family at a time. Leave your current family first if you want to join this one.'): (
        None, 'एक व्यक्ति एक समय में एक ही परिवार में रह सकता है। इसमें जुड़ना चाहते हैं तो पहले अपना मौजूदा परिवार छोड़ें।',
        'एक व्यक्ती एका वेळी एकाच कुटुंबात राहू शकते. यात सामील व्हायचे असल्यास आधी तुमचे सध्याचे कुटुंब सोडा.'),
    ('', 'This invitation has expired'): (None, 'यह आमंत्रण समाप्त हो चुका है', 'या आमंत्रणाची मुदत संपली आहे'),
    ('', 'This invitation has already been used'): (None, 'यह आमंत्रण पहले ही इस्तेमाल हो चुका है', 'हे आमंत्रण आधीच वापरले गेले आहे'),
    ('', 'This invitation link is not valid'): (None, 'यह आमंत्रण लिंक मान्य नहीं है', 'हा आमंत्रण लिंक वैध नाही'),
    ('', 'Ask %(name)s to send you a new link.'): (None, '%(name)s से नया लिंक भेजने को कहें।', '%(name)s यांना नवीन लिंक पाठवायला सांगा.'),
    ('', 'Each link works once. Ask the person who invited you for a new one.'): (
        None, 'हर लिंक एक ही बार काम करता है। आमंत्रित करने वाले व्यक्ति से नया लिंक माँगें।', 'प्रत्येक लिंक एकदाच चालते. आमंत्रित करणाऱ्या व्यक्तीकडून नवीन लिंक मागा.'),
    ('', 'It may have been cancelled or copied wrongly. Ask the person who invited you for a new link.'): (
        None, 'हो सकता है इसे रद्द कर दिया गया हो या गलत कॉपी हुआ हो। आमंत्रित करने वाले व्यक्ति से नया लिंक माँगें।',
        'ते रद्द झाले असेल किंवा चुकीचे कॉपी झाले असेल. आमंत्रित करणाऱ्या व्यक्तीकडून नवीन लिंक मागा.'),

    # ---- messages ----
    ('', 'Your family is ready. Invite the people you want to share with.'): (
        None, 'आपका परिवार तैयार है। जिनके साथ साझा करना चाहते हैं उन्हें आमंत्रित करें।', 'तुमचे कुटुंब तयार आहे. ज्यांच्यासोबत शेअर करायचे आहे त्यांना आमंत्रित करा.'),
    ('', 'That email address does not look right.'): (None, 'यह ईमेल पता सही नहीं लग रहा।', 'हा ईमेल पत्ता बरोबर दिसत नाही.'),
    ('', 'Invitation sent to %(email)s.'): (None, 'आमंत्रण %(email)s को भेजा गया।', 'आमंत्रण %(email)s ला पाठवले.'),
    ('', 'Invitation link created. Copy it and send it to them.'): (
        None, 'आमंत्रण लिंक बन गया। इसे कॉपी करके उन्हें भेजें।', 'आमंत्रण लिंक तयार झाला. तो कॉपी करून त्यांना पाठवा.'),
    ('', 'Invitation cancelled.'): (None, 'आमंत्रण रद्द किया गया।', 'आमंत्रण रद्द केले.'),
    ('', 'A new invitation link is ready.'): (None, 'नया आमंत्रण लिंक तैयार है।', 'नवीन आमंत्रण लिंक तयार आहे.'),
    ('', 'Please tick the box to confirm you understand what joining means.'): (
        None, 'कृपया बॉक्स पर टिक करके पुष्टि करें कि आप समझते हैं कि जुड़ने का क्या मतलब है।',
        'सामील होण्याचा अर्थ काय आहे हे तुम्हाला समजले आहे याची खात्री करण्यासाठी कृपया बॉक्सवर खूण करा.'),
    ('', 'You have joined the family. Nothing is shared until you choose.'): (
        None, 'आप परिवार से जुड़ गए हैं। जब तक आप न चुनें, कुछ भी साझा नहीं होता।', 'तुम्ही कुटुंबात सामील झाला आहात. तुम्ही निवडेपर्यंत काहीही शेअर होत नाही.'),
    ('', 'You have left the family. Nothing of yours is shared any more.'): (
        None, 'आपने परिवार छोड़ दिया है। आपका कुछ भी अब साझा नहीं है।', 'तुम्ही कुटुंब सोडले आहे. तुमचे काहीही आता शेअर केलेले नाही.'),
    ('', 'Removed from the family.'): (None, 'परिवार से हटा दिया गया।', 'कुटुंबातून काढले.'),
    ('', 'You are already in a family.'): (None, 'आप पहले से एक परिवार में हैं।', 'तुम्ही आधीच एका कुटुंबात आहात.'),
    ('', 'You are already in a family. Leave it first to join another.'): (
        None, 'आप पहले से एक परिवार में हैं। दूसरे में जुड़ने के लिए पहले उसे छोड़ें।', 'तुम्ही आधीच एका कुटुंबात आहात. दुसऱ्यात सामील होण्यासाठी आधी ते सोडा.'),
    ('', 'You are not in a family.'): (None, 'आप किसी परिवार में नहीं हैं।', 'तुम्ही कोणत्याही कुटुंबात नाही.'),
    ('', 'Family sharing needs a Pro plan.'): (None, 'परिवार के साथ साझा करने के लिए प्रो प्लान चाहिए।', 'कुटुंबासोबत शेअर करण्यासाठी प्रो प्लॅन लागतो.'),
    ('', 'Your family is full. Remove a member or cancel an invitation first.'): (
        None, 'आपका परिवार भर चुका है। पहले किसी सदस्य को हटाएँ या कोई आमंत्रण रद्द करें।', 'तुमचे कुटुंब भरले आहे. आधी एखाद्या सदस्याला काढा किंवा एखादे आमंत्रण रद्द करा.'),
    ('', 'That invitation has already been used.'): (None, 'वह आमंत्रण पहले ही इस्तेमाल हो चुका है।', 'ते आमंत्रण आधीच वापरले गेले आहे.'),
    ('', 'This invitation can no longer be used.'): (None, 'यह आमंत्रण अब इस्तेमाल नहीं किया जा सकता।', 'हे आमंत्रण आता वापरता येत नाही.'),
    ('', 'This family is full.'): (None, 'यह परिवार भर चुका है।', 'हे कुटुंब भरले आहे.'),
    ('', 'Only the person who started the family can remove someone.'): (
        None, 'केवल परिवार शुरू करने वाला व्यक्ति ही किसी को हटा सकता है।', 'कुटुंब सुरू करणारी व्यक्तीच कोणाला तरी काढू शकते.'),
    ('', 'You cannot remove yourself. Leave the family instead.'): (
        None, 'आप खुद को नहीं हटा सकते। इसके बजाय परिवार छोड़ें।', 'तुम्ही स्वतःला काढू शकत नाही. त्याऐवजी कुटुंब सोडा.'),

    # ---- the plan banner ----
    ('', 'Family view is paused because your Pro plan has ended. It resumes as soon as everyone is on Pro. Your own data and your sharing choices are untouched.'): (
        None, 'आपका प्रो प्लान समाप्त हो गया है, इसलिए परिवार दृश्य रुका हुआ है। सभी के प्रो पर आते ही यह फिर शुरू हो जाएगा। आपका अपना डेटा और आपकी साझा करने की पसंद जस की तस हैं।',
        'तुमचा प्रो प्लॅन संपला आहे, म्हणून कुटुंब दृश्य थांबले आहे. सर्वजण प्रो वर येताच ते पुन्हा सुरू होईल. तुमचा स्वतःचा डेटा आणि तुमच्या शेअरिंगच्या निवडी जशाच्या तशा आहेत.'),
    ('', 'Family view is paused. The Pro plan of %(names)s has ended. It resumes as soon as everyone is on Pro. Your own data and your sharing choices are untouched.'): (
        None, 'परिवार दृश्य रुका हुआ है। %(names)s का प्रो प्लान समाप्त हो गया है। सभी के प्रो पर आते ही यह फिर शुरू हो जाएगा। आपका अपना डेटा और आपकी साझा करने की पसंद जस की तस हैं।',
        'कुटुंब दृश्य थांबले आहे. %(names)s यांचा प्रो प्लॅन संपला आहे. सर्वजण प्रो वर येताच ते पुन्हा सुरू होईल. तुमचा स्वतःचा डेटा आणि तुमच्या शेअरिंगच्या निवडी जशाच्या तशा आहेत.'),
    ('', 'Your Pro plan ends in %(days)d day. Family view will pause on %(date)s unless you renew. Everything you share stays exactly as it is.'): (
        'Your Pro plan ends in %(days)d days. Family view will pause on %(date)s unless you renew. Everything you share stays exactly as it is.',
        ('आपका प्रो प्लान %(days)d दिन में समाप्त हो रहा है। अगर आप नवीनीकरण नहीं करते तो %(date)s को परिवार दृश्य रुक जाएगा। आप जो साझा करते हैं वह जस का तस रहता है।',) * 2,
        ('तुमचा प्रो प्लॅन %(days)d दिवसात संपत आहे. तुम्ही नूतनीकरण न केल्यास %(date)s रोजी कुटुंब दृश्य थांबेल. तुम्ही शेअर केलेले सर्व काही जसेच्या तसे राहते.',) * 2),
    ('', 'The Pro plan of %(name)s ends in %(days)d day. Family view will pause on %(date)s unless they renew. Everything you share stays exactly as it is.'): (
        'The Pro plan of %(name)s ends in %(days)d days. Family view will pause on %(date)s unless they renew. Everything you share stays exactly as it is.',
        ('%(name)s का प्रो प्लान %(days)d दिन में समाप्त हो रहा है। अगर वे नवीनीकरण नहीं करते तो %(date)s को परिवार दृश्य रुक जाएगा। आप जो साझा करते हैं वह जस का तस रहता है।',) * 2,
        ('%(name)s यांचा प्रो प्लॅन %(days)d दिवसात संपत आहे. त्यांनी नूतनीकरण न केल्यास %(date)s रोजी कुटुंब दृश्य थांबेल. तुम्ही शेअर केलेले सर्व काही जसेच्या तसे राहते.',) * 2),
    ('', 'Family view is on. %(count)d member is on Pro, so the Me / Family toggle is available.'): (
        'Family view is on. All %(count)d members are on Pro, so the Me / Family toggle is available to everyone.',
        ('परिवार दृश्य चालू है। %(count)d सदस्य प्रो पर है, इसलिए मैं / परिवार टॉगल उपलब्ध है।',
         'परिवार दृश्य चालू है। सभी %(count)d सदस्य प्रो पर हैं, इसलिए मैं / परिवार टॉगल सभी के लिए उपलब्ध है।'),
        ('कुटुंब दृश्य सुरू आहे. %(count)d सदस्य प्रो वर आहे, म्हणून मी / कुटुंब पर्याय उपलब्ध आहे.',
         'कुटुंब दृश्य सुरू आहे. सर्व %(count)d सदस्य प्रो वर आहेत, म्हणून मी / कुटुंब पर्याय सर्वांसाठी उपलब्ध आहे.')),

    # ---- Phase 2: what I share, the sharing dialog and the preview ----
    ('', 'What I share'): (None, 'मैं क्या साझा करता हूँ', 'मी काय शेअर करतो'),
    ('', 'Family pages'): (None, 'परिवार के पन्ने', 'कुटुंबाची पाने'),
    ('', 'Choose what to share'): (None, 'चुनें क्या साझा करना है', 'काय शेअर करायचे ते निवडा'),
    ('', 'Choose what your family can see.'): (None, 'चुनें कि आपका परिवार क्या देख सकता है।', 'तुमचे कुटुंब काय पाहू शकते ते निवडा.'),
    ('', 'Create or join a family first, then choose what to share.'): (None, 'पहले परिवार बनाएँ या उसमें जुड़ें, फिर चुनें कि क्या साझा करना है।', 'आधी कुटुंब तयार करा किंवा त्यात सामील व्हा, मग काय शेअर करायचे ते निवडा.'),
    ('', 'That account cannot be shared.'): (None, 'यह खाता साझा नहीं किया जा सकता।', 'हे खाते शेअर करता येत नाही.'),
    ('', 'Now shared with your family.'): (None, 'अब आपके परिवार के साथ साझा है।', 'आता तुमच्या कुटुंबासोबत शेअर केले आहे.'),
    ('', 'Sharing saved.'): (None, 'साझा करने की सेटिंग सहेजी गई।', 'शेअरिंग सेव्ह केले.'),
    ('', '%(name)s is now shared with your family.'): (None, '%(name)s अब आपके परिवार के साथ साझा है।', '%(name)s आता तुमच्या कुटुंबासोबत शेअर केले आहे.'),
    ('', 'Stopped sharing %(name)s.'): (None, '%(name)s साझा करना बंद किया।', '%(name)s शेअर करणे थांबवले.'),
    ('', '%(name)s shared something new'): (None, '%(name)s ने कुछ नया साझा किया', '%(name)s यांनी काहीतरी नवीन शेअर केले'),
    ('', '%(name)s is now shared with the family.'): (None, '%(name)s अब परिवार के साथ साझा है।', '%(name)s आता कुटुंबासोबत शेअर केले आहे.'),
    ('', 'Switch on the accounts, cards, policies and loans your family should see. Everything else stays private.'): (None, 'वे खाते, कार्ड, पॉलिसी और लोन चालू करें जो आपके परिवार को दिखने चाहिए। बाकी सब निजी रहता है।', 'तुमच्या कुटुंबाला दिसावेत ती खाती, कार्ड, पॉलिसी आणि कर्जे चालू करा. बाकी सर्व खाजगी राहते.'),
    ('', 'Preview as a family member'): (None, 'परिवार के सदस्य की तरह देखें', 'कुटुंबातील सदस्य म्हणून पूर्वावलोकन'),
    ('', 'Sharing %(shared)s of your %(total)s items.'): (None, 'आपके %(total)s में से %(shared)s आइटम साझा हो रहे हैं।', 'तुमच्या %(total)s पैकी %(shared)s गोष्टी शेअर होत आहेत.'),
    ('', 'Family sees names, types and amounts. They never see transactions, categories, notes or account numbers, and they cannot tell a private item exists.'): (None, 'परिवार को नाम, प्रकार और रकम दिखती है। उन्हें लेन-देन, श्रेणियाँ, नोट या खाता संख्या कभी नहीं दिखती, और वे यह भी नहीं जान सकते कि कोई निजी आइटम है।', 'कुटुंबाला नावे, प्रकार आणि रक्कम दिसते. त्यांना व्यवहार, वर्ग, नोंदी किंवा खाते क्रमांक कधीही दिसत नाहीत, आणि खाजगी गोष्ट अस्तित्वात आहे हेही त्यांना कळत नाही.'),
    ('', 'Your accounts'): (None, 'आपके खाते', 'तुमची खाती'),
    ('', 'You have no accounts yet. Add one on the Accounts page, then come back to share it.'): (None, 'आपका अभी कोई खाता नहीं है। खाते वाले पन्ने पर एक जोड़ें, फिर उसे साझा करने वापस आएँ।', 'तुमचे अजून कोणतेही खाते नाही. खाती पानावर एक जोडा, मग ते शेअर करायला परत या.'),
    ('', 'Share %(name)s'): (None, '%(name)s साझा करें', '%(name)s शेअर करा'),
    ('', 'shown as %(alias)s'): (None, '%(alias)s के नाम से दिखता है', '%(alias)s नावाने दिसते'),
    ('', 'Family sees that it exists, with no amounts'): (None, 'परिवार को केवल यह दिखता है कि यह है, रकम नहीं', 'कुटुंबाला फक्त हे आहे एवढेच दिसते, रक्कम नाही'),
    ('', 'Listed only'): (None, 'केवल सूची में', 'फक्त यादीत'),
    ('', 'Details shown'): (None, 'विवरण दिखता है', 'तपशील दिसतात'),
    ('', 'Amount counts'): (None, 'रकम गिनी जाती है', 'रक्कम मोजली जाते'),
    ('', 'Private'): (None, 'निजी', 'खाजगी'),
    ('', 'Edit sharing'): (None, 'साझा करना बदलें', 'शेअरिंग बदला'),
    ('', 'Share…'): (None, 'साझा करें…', 'शेअर करा…'),
    ('', 'Sharing activity'): (None, 'साझा करने की गतिविधि', 'शेअरिंगची हालचाल'),
    ('', 'Only you see this list.'): (None, 'यह सूची केवल आपको दिखती है।', 'ही यादी फक्त तुम्हाला दिसते.'),
    ('', 'You shared <b>%(name)s</b> as listed only'): (None, 'आपने <b>%(name)s</b> केवल सूची में साझा किया', 'तुम्ही <b>%(name)s</b> फक्त यादीत शेअर केले'),
    ('', 'You shared <b>%(name)s</b> with its amount'): (None, 'आपने <b>%(name)s</b> रकम के साथ साझा किया', 'तुम्ही <b>%(name)s</b> रकमेसह शेअर केले'),
    ('', 'You changed <b>%(name)s</b> to listed only'): (None, 'आपने <b>%(name)s</b> को केवल सूची में कर दिया', 'तुम्ही <b>%(name)s</b> फक्त यादीत केले'),
    ('', 'You changed <b>%(name)s</b> to show its amount'): (None, 'आपने <b>%(name)s</b> की रकम दिखाना चुना', 'तुम्ही <b>%(name)s</b> ची रक्कम दाखवणे निवडले'),
    ('', 'You stopped sharing <b>%(name)s</b>'): (None, 'आपने <b>%(name)s</b> साझा करना बंद किया', 'तुम्ही <b>%(name)s</b> शेअर करणे थांबवले'),
    ('', 'Nothing yet. When you share, change or stop sharing something, it shows up here.'): (None, 'अभी कुछ नहीं। जब आप कुछ साझा करेंगे, बदलेंगे या बंद करेंगे, वह यहाँ दिखेगा।', 'अजून काही नाही. तुम्ही काही शेअर केले, बदलले किंवा थांबवले की ते इथे दिसेल.'),
    ('', 'Tell family when I share something new'): (None, 'कुछ नया साझा करने पर परिवार को बताएँ', 'काही नवीन शेअर केल्यावर कुटुंबाला कळवा'),
    ('', 'Off by default. A short note in their notifications, with the name you chose and nothing else.'): (None, 'डिफ़ॉल्ट रूप से बंद। उनकी सूचनाओं में एक छोटा संदेश, आपके चुने नाम के साथ और कुछ नहीं।', 'डिफॉल्टनुसार बंद. त्यांच्या सूचनांमध्ये एक छोटी नोंद, तुम्ही निवडलेल्या नावासह आणि दुसरे काहीही नाही.'),
    ('', 'Share %(name)s'): (None, '%(name)s साझा करें', '%(name)s शेअर करा'),
    ('', 'With %(names)s. Everyone in the family sees the same.'): (None, '%(names)s के साथ। परिवार में सभी को एक जैसा दिखता है।', '%(names)s यांच्यासोबत. कुटुंबातील सर्वांना सारखेच दिसते.'),
    ('', 'With your family. Nobody else has joined yet, so for now only you see it.'): (None, 'आपके परिवार के साथ। अभी कोई और नहीं जुड़ा है, इसलिए फ़िलहाल केवल आप इसे देखते हैं।', 'तुमच्या कुटुंबासोबत. अजून कोणी सामील झाले नाही, म्हणून सध्या फक्त तुम्हीच हे पाहता.'),
    ('', 'What can they see?'): (None, 'वे क्या देख सकते हैं?', 'त्यांना काय दिसू शकते?'),
    ('', 'Show amounts'): (None, 'रकम दिखाएँ', 'रक्कम दाखवा'),
    ('', 'List only'): (None, 'केवल सूची', 'फक्त यादी'),
    ('', 'They see the cover, premium, next due date and who it covers.'): (None, 'उन्हें कवर, प्रीमियम, अगली देय तारीख और पॉलिसी किसे कवर करती है, यह दिखता है।', 'त्यांना कव्हर, प्रीमियम, पुढील देय तारीख आणि पॉलिसी कोणाला कव्हर करते हे दिसते.'),
    ('', 'What you owe counts towards the family net worth.'): (None, 'आपका बकाया परिवार की कुल संपत्ति में गिना जाता है।', 'तुमची थकबाकी कुटुंबाच्या एकूण संपत्तीत मोजली जाते.'),
    ('', 'The amount counts towards the family net worth.'): (None, 'रकम परिवार की कुल संपत्ति में गिनी जाती है।', 'रक्कम कुटुंबाच्या एकूण संपत्तीत मोजली जाते.'),
    ('', 'They see that you hold this, with no amounts. It is not counted in the family net worth.'): (None, 'उन्हें दिखता है कि यह आपके पास है, रकम नहीं। यह परिवार की कुल संपत्ति में नहीं गिना जाता।', 'हे तुमच्याकडे आहे एवढेच त्यांना दिसते, रक्कम नाही. हे कुटुंबाच्या एकूण संपत्तीत मोजले जात नाही.'),
    ('', 'Shown to family as'): (None, 'परिवार को इस नाम से दिखेगा', 'कुटुंबाला या नावाने दिसेल'),
    ('', 'Rename it if the account name is personal. Your own name for it does not change.'): (None, 'अगर खाते का नाम निजी है तो उसे बदल दें। आपके अपने पन्नों में नाम वही रहता है।', 'खात्याचे नाव खाजगी असेल तर ते बदला. तुमच्या स्वतःच्या पानांवर नाव तेच राहते.'),
    ('', 'Who does this policy cover?'): (None, 'यह पॉलिसी किसे कवर करती है?', 'ही पॉलिसी कोणाला कव्हर करते?'),
    ('', 'For example: Arjun, Priya'): (None, 'उदाहरण: अर्जुन, प्रिया', 'उदाहरण: अर्जुन, प्रिया'),
    ('', 'Add a name'): (None, 'नाम जोड़ें', 'नाव जोडा'),
    ('', 'Only shown to family if you show amounts. Type names, separated by commas.'): (None, 'परिवार को तभी दिखता है जब आप रकम दिखाएँ। नाम कॉमा से अलग करके लिखें।', 'तुम्ही रक्कम दाखवली तरच कुटुंबाला दिसते. नावे स्वल्पविरामाने वेगळी करून लिहा.'),
    ('', 'Family will see'): (None, 'परिवार को यह दिखेगा', 'कुटुंबाला हे दिसेल'),
    ('', 'Outstanding'): (None, 'बकाया', 'थकबाकी'),
    ('', 'Limit'): (None, 'सीमा', 'मर्यादा'),
    ('', 'Available'): (None, 'उपलब्ध', 'उपलब्ध'),
    ('', 'Next bill'): (None, 'अगला बिल', 'पुढील बिल'),
    ('', 'Cover'): (None, 'कवर', 'कव्हर'),
    ('', 'Premium'): (None, 'प्रीमियम', 'प्रीमियम'),
    ('', 'Next due'): (None, 'अगली देय तिथि', 'पुढील देय तारीख'),
    ('', 'EMI'): (None, 'ईएमआई', 'ईएमआय'),
    ('', 'Amount'): (None, 'रकम', 'रक्कम'),
    ('', 'Just the name, the type and who holds it. No amounts.'): (None, 'केवल नाम, प्रकार और किसके पास है। कोई रकम नहीं।', 'फक्त नाव, प्रकार आणि कोणाकडे आहे. कोणतीही रक्कम नाही.'),
    ('', 'Never shared:'): (None, 'कभी साझा नहीं होता:', 'कधीही शेअर होत नाही:'),
    ('', 'transactions, categories, notes, the account or policy number, or anything else inside this account. Family history starts today and uses month-end amounts only.'): (None, 'लेन-देन, श्रेणियाँ, नोट, खाता या पॉलिसी संख्या, या इस खाते के अंदर की कोई भी और चीज़। परिवार का इतिहास आज से शुरू होता है और केवल महीने के अंत की रकम का उपयोग करता है।', 'व्यवहार, वर्ग, नोंदी, खाते किंवा पॉलिसी क्रमांक, किंवा या खात्यातील इतर काहीही. कुटुंबाचा इतिहास आजपासून सुरू होतो आणि फक्त महिन्याच्या शेवटची रक्कम वापरतो.'),
    ('', 'Stop sharing'): (None, 'साझा करना बंद करें', 'शेअर करणे थांबवा'),
    ('', 'Save sharing'): (None, 'साझा करना सहेजें', 'शेअरिंग सेव्ह करा'),
    ('', 'Cancel'): (None, 'रद्द करें', 'रद्द करा'),
    ('', "%(name)s's shared items"): (None, '%(name)s के साझा आइटम', '%(name)s यांच्या शेअर केलेल्या गोष्टी'),
    ('', 'Names, types and amounts only.'): (None, 'केवल नाम, प्रकार और रकम।', 'फक्त नावे, प्रकार आणि रक्कम.'),
    ('', '<b>Previewing as %(name)s.</b> This is exactly what they see of what you share.'): (None, '<b>%(name)s के रूप में पूर्वावलोकन।</b> आप जो साझा करते हैं, उसमें से उन्हें ठीक यही दिखता है।', '<b>%(name)s म्हणून पूर्वावलोकन.</b> तुम्ही शेअर केलेल्यापैकी त्यांना नेमके हेच दिसते.'),
    ('', 'Previewing as a family member. This is exactly what they will see of what you share.'): (None, 'परिवार के सदस्य के रूप में पूर्वावलोकन। आप जो साझा करते हैं, उसमें से उन्हें ठीक यही दिखेगा।', 'कुटुंबातील सदस्य म्हणून पूर्वावलोकन. तुम्ही शेअर केलेल्यापैकी त्यांना नेमके हेच दिसेल.'),
    ('', 'Switch to %(name)s'): (None, '%(name)s पर जाएँ', '%(name)s कडे जा'),
    ('', 'Exit preview'): (None, 'पूर्वावलोकन बंद करें', 'पूर्वावलोकन बंद करा'),
    ('', 'What %(name)s sees'): (None, '%(name)s को क्या दिखता है', '%(name)s यांना काय दिसते'),
    ('', 'What family sees'): (None, 'परिवार को क्या दिखता है', 'कुटुंबाला काय दिसते'),
    ('', 'You have not shared anything yet, so family sees nothing of yours.'): (None, 'आपने अभी कुछ साझा नहीं किया, इसलिए परिवार को आपका कुछ नहीं दिखता।', 'तुम्ही अजून काही शेअर केले नाही, म्हणून कुटुंबाला तुमचे काहीही दिसत नाही.'),
    ('', 'Only you see this note'): (None, 'यह नोट केवल आपको दिखता है', 'ही नोंद फक्त तुम्हाला दिसते'),
    ('', '%(name)s cannot see any of this, and nothing hints that it exists:'): (None, '%(name)s इनमें से कुछ नहीं देख सकते, और कुछ भी इसके होने का संकेत नहीं देता:', '%(name)s यांना यातले काहीही दिसत नाही, आणि हे अस्तित्वात आहे याचा काहीही इशारा मिळत नाही:'),
    ('', 'Family cannot see any of this, and nothing hints that it exists:'): (None, 'परिवार इनमें से कुछ नहीं देख सकता, और कुछ भी इसके होने का संकेत नहीं देता:', 'कुटुंबाला यातले काहीही दिसत नाही, आणि हे अस्तित्वात आहे याचा काहीही इशारा मिळत नाही:'),
    ('', '%(name)s (private): not listed, not counted'): (None, '%(name)s (निजी): सूची में नहीं, गिना नहीं जाता', '%(name)s (खाजगी): यादीत नाही, मोजले जात नाही'),
    ('', 'The amounts on %(name)s'): (None, '%(name)s की रकम', '%(name)s ची रक्कम'),
    ('', 'Every transaction, category and note, in every account'): (None, 'हर खाते का हर लेन-देन, श्रेणी और नोट', 'प्रत्येक खात्यातील प्रत्येक व्यवहार, वर्ग आणि नोंद'),
    ('', 'Account, card and policy numbers'): (None, 'खाता, कार्ड और पॉलिसी संख्याएँ', 'खाते, कार्ड आणि पॉलिसी क्रमांक'),
    ('', 'Amounts not shared'): (None, 'रकम साझा नहीं', 'रक्कम शेअर केलेली नाही'),
    ('', 'Cover %(amount)s'): (None, 'कवर %(amount)s', 'कव्हर %(amount)s'),
    ('', 'Limit %(limit)s'): (None, 'सीमा %(limit)s', 'मर्यादा %(limit)s'),
    ('', 'next bill %(date)s'): (None, 'अगला बिल %(date)s', 'पुढील बिल %(date)s'),
    ('', 'covers %(names)s'): (None, 'कवर: %(names)s', 'कव्हर: %(names)s'),
    ('', 'premium %(amount)s %(per)s'): (None, 'प्रीमियम %(amount)s %(per)s', 'प्रीमियम %(amount)s %(per)s'),
    ('', 'next due %(date)s'): (None, 'अगली देय तिथि %(date)s', 'पुढील देय तारीख %(date)s'),
    ('', 'EMI %(amount)s a month'): (None, 'ईएमआई %(amount)s प्रति माह', 'ईएमआय %(amount)s दरमहा'),
    ('', 'a year'): (None, 'प्रति वर्ष', 'दरवर्षी'),
    ('', 'a month'): (None, 'प्रति माह', 'दरमहा'),
    ('', 'a quarter'): (None, 'प्रति तिमाही', 'दर तिमाही'),
    ('', 'every 6 months'): (None, 'हर 6 महीने', 'दर 6 महिन्यांनी'),
    ('', 'Bank and cash'): (None, 'बैंक और नकद', 'बँक आणि रोख'),
    ('', 'Credit cards'): (None, 'क्रेडिट कार्ड', 'क्रेडिट कार्ड'),
    ('', 'Insurance'): (None, 'बीमा', 'विमा'),
    ('', 'Loans and property'): (None, 'लोन और संपत्ति', 'कर्जे आणि मालमत्ता'),
    ('', 'Investments'): (None, 'निवेश', 'गुंतवणूक'),

    # ---- the kinds of account, in short ----
    ('', 'Account'): (None, 'खाता', 'खाते'),
    ('', 'Cash wallet'): (None, 'नकद वॉलेट', 'रोख वॉलेट'),
    ('', 'Savings account'): (None, 'बचत खाता', 'बचत खाते'),
    ('', 'Salary account'): (None, 'वेतन खाता', 'पगार खाते'),
    ('', 'Current account'): (None, 'चालू खाता', 'चालू खाते'),
    ('', 'Digital wallet'): (None, 'डिजिटल वॉलेट', 'डिजिटल वॉलेट'),
    ('', 'Fixed deposit'): (None, 'सावधि जमा', 'मुदत ठेव'),
    ('', 'Recurring deposit'): (None, 'आवर्ती जमा', 'आवर्ती ठेव'),
    ('', 'Public Provident Fund'): (None, 'सार्वजनिक भविष्य निधि (पीपीएफ)', 'सार्वजनिक भविष्य निर्वाह निधी (पीपीएफ)'),
    ("", "Employees' Provident Fund"): (None, 'कर्मचारी भविष्य निधि (ईपीएफ)', 'कर्मचारी भविष्य निर्वाह निधी (ईपीएफ)'),
    ('', 'Voluntary Provident Fund'): (None, 'स्वैच्छिक भविष्य निधि (वीपीएफ)', 'ऐच्छिक भविष्य निर्वाह निधी (व्हीपीएफ)'),
    ('', 'National Pension System'): (None, 'राष्ट्रीय पेंशन प्रणाली (एनपीएस)', 'राष्ट्रीय निवृत्तिवेतन प्रणाली (एनपीएस)'),
    ('', 'Sukanya Samriddhi'): (None, 'सुकन्या समृद्धि', 'सुकन्या समृद्धी'),
    ('', 'Post Office savings'): (None, 'डाकघर बचत', 'पोस्ट ऑफिस बचत'),
    ('', 'Atal Pension Yojana'): (None, 'अटल पेंशन योजना', 'अटल पेन्शन योजना'),
    ('', 'Demat account'): (None, 'डीमैट खाता', 'डिमॅट खाते'),
    ('', 'Mutual funds'): (None, 'म्यूचुअल फंड', 'म्युच्युअल फंड'),
    ('', 'ETFs'): (None, 'ईटीएफ', 'ईटीएफ'),
    ('', 'Sovereign Gold Bonds'): (None, 'सॉवरेन गोल्ड बॉन्ड', 'सॉवरेन गोल्ड बाँड'),
    ('', 'ULIP'): (None, 'यूलिप', 'युलिप'),
    ('', 'Gold'): (None, 'सोना', 'सोने'),
    ('', 'Credit card'): (None, 'क्रेडिट कार्ड', 'क्रेडिट कार्ड'),
    ('', 'Buy now pay later'): (None, 'अभी खरीदें, बाद में चुकाएँ', 'आता खरेदी करा, नंतर पैसे द्या'),
    ('', 'Overdraft'): (None, 'ओवरड्राफ्ट', 'ओव्हरड्राफ्ट'),
    ('', 'Home loan'): (None, 'होम लोन', 'गृहकर्ज'),
    ('', 'Vehicle loan'): (None, 'वाहन लोन', 'वाहन कर्ज'),
    ('', 'Education loan'): (None, 'शिक्षा लोन', 'शैक्षणिक कर्ज'),
    ('', 'Personal loan'): (None, 'पर्सनल लोन', 'वैयक्तिक कर्ज'),
    ('', 'Business loan'): (None, 'बिज़नेस लोन', 'व्यवसाय कर्ज'),
    ('', 'Loan against property'): (None, 'संपत्ति पर लोन', 'मालमत्तेवरील कर्ज'),
    ('', 'Gold loan'): (None, 'गोल्ड लोन', 'सोनेतारण कर्ज'),
    ('', 'Property'): (None, 'संपत्ति', 'मालमत्ता'),
    ('', 'Vehicle'): (None, 'वाहन', 'वाहन'),
    ('', 'Life insurance'): (None, 'जीवन बीमा', 'जीवन विमा'),
    ('', 'Health insurance'): (None, 'स्वास्थ्य बीमा', 'आरोग्य विमा'),
    ('', 'Cash'): (None, 'नकद', 'रोख'),
    ('', 'Bank account'): (None, 'बैंक खाता', 'बँक खाते'),
    ('', 'Investment'): (None, 'निवेश', 'गुंतवणूक'),
    ('', 'Other'): (None, 'अन्य', 'इतर'),

    # ---- Phase 3: the family's net worth and inventory ----
    ('', 'Net worth'): (None, 'कुल संपत्ति', 'एकूण संपत्ती'),
    ('', 'Me'): (None, 'मैं', 'मी'),
    ('', 'Family view is paused until everyone is on Pro'): (None, 'जब तक सभी प्रो पर नहीं होते, परिवार दृश्य रुका है', 'सर्वजण प्रो वर येईपर्यंत कुटुंब दृश्य थांबले आहे'),
    ('', 'Create or join a family first to see its combined net worth.'): (None, 'पहले परिवार बनाएँ या उसमें जुड़ें, फिर उसकी संयुक्त कुल संपत्ति देखें।', 'आधी कुटुंब तयार करा किंवा त्यात सामील व्हा, मग त्याची एकत्रित एकूण संपत्ती पहा.'),
    ('', 'Create or join a family first to see what it holds.'): (None, 'पहले परिवार बनाएँ या उसमें जुड़ें, फिर देखें कि उसके पास क्या है।', 'आधी कुटुंब तयार करा किंवा त्यात सामील व्हा, मग त्यांच्याकडे काय आहे ते पहा.'),
    ('', 'Family view: the combined net worth of what your family has shared.'): (None, 'परिवार दृश्य: आपके परिवार द्वारा साझा की गई चीज़ों की संयुक्त कुल संपत्ति।', 'कुटुंब दृश्य: तुमच्या कुटुंबाने शेअर केलेल्या गोष्टींची एकत्रित एकूण संपत्ती.'),
    ('', 'Family view: who in the family holds which cards, policies and accounts.'): (None, 'परिवार दृश्य: परिवार में किसके पास कौन से कार्ड, पॉलिसी और खाते हैं।', 'कुटुंब दृश्य: कुटुंबात कोणाकडे कोणती कार्डे, पॉलिसी आणि खाती आहेत.'),
    ('', 'Family view is paused'): (None, 'परिवार दृश्य रुका हुआ है', 'कुटुंब दृश्य थांबले आहे'),
    ('', "It resumes as soon as everyone in the family is on Pro. Your own data and everyone's sharing choices are untouched."): (None, 'परिवार में सभी के प्रो पर आते ही यह फिर चालू हो जाता है। आपका अपना डेटा और सभी के साझा करने के चुनाव जस के तस हैं।', 'कुटुंबातील सर्वजण प्रो वर येताच हे पुन्हा सुरू होते. तुमचा स्वतःचा डेटा आणि सर्वांच्या शेअरिंगच्या निवडी जशाच्या तशा आहेत.'),
    ('', 'See members and plans'): (None, 'सदस्य और प्लान देखें', 'सदस्य आणि प्लॅन पहा'),
    ('', 'Based on what is shared.'): (None, 'जो साझा किया गया है, उसके आधार पर।', 'जे शेअर केले आहे त्यावर आधारित.'),
    ('', 'The same for every member.'): (None, 'हर सदस्य के लिए एक जैसा।', 'प्रत्येक सदस्यासाठी सारखेच.'),
    ('', '%(counter)s item is listed without amounts, so it is not counted.'): ('%(counter)s items are listed without amounts, so they are not counted.', ('%(counter)s आइटम बिना रकम के सूचीबद्ध है, इसलिए गिना नहीं गया।', '%(counter)s आइटम बिना रकम के सूचीबद्ध हैं, इसलिए गिने नहीं गए।'), ('%(counter)s गोष्ट रकमेशिवाय यादीत आहे, म्हणून मोजली नाही.', '%(counter)s गोष्टी रकमेशिवाय यादीत आहेत, म्हणून मोजल्या नाहीत.')),
    ('', '%(counter)s item is in another currency, so it is not counted.'): ('%(counter)s items are in another currency, so they are not counted.', ('%(counter)s आइटम दूसरी मुद्रा में है, इसलिए गिना नहीं गया।', '%(counter)s आइटम दूसरी मुद्रा में हैं, इसलिए गिने नहीं गए।'), ('%(counter)s गोष्ट दुसऱ्या चलनात आहे, म्हणून मोजली नाही.', '%(counter)s गोष्टी दुसऱ्या चलनात आहेत, म्हणून मोजल्या नाहीत.')),
    ('', 'Savings rate and the health check are not shown here.'): (None, 'बचत दर और हेल्थ चेक यहाँ नहीं दिखाए जाते।', 'बचत दर आणि हेल्थ चेक इथे दाखवले जात नाहीत.'),
    ('', 'Family net worth'): (None, 'परिवार की कुल संपत्ति', 'कुटुंबाची एकूण संपत्ती'),
    ('', '%(counter)s shared item from %(members)s members'): ('%(counter)s shared items from %(members)s members', ('%(members)s सदस्यों से %(counter)s साझा आइटम', '%(members)s सदस्यों से %(counter)s साझा आइटम'), ('%(members)s सदस्यांकडून %(counter)s शेअर केलेली गोष्ट', '%(members)s सदस्यांकडून %(counter)s शेअर केलेल्या गोष्टी')),
    ('', '3M'): (None, '3 माह', '3 महिने'),
    ('', 'Since sharing'): (None, 'साझा करने के बाद से', 'शेअर केल्यापासून'),
    ('', 'What you own'): (None, 'आपके पास क्या है', 'तुमच्याकडे काय आहे'),
    ('', 'What you owe'): (None, 'आप पर क्या बकाया है', 'तुमच्यावर काय थकबाकी आहे'),
    ('', 'Now'): (None, 'अभी', 'आता'),
    ('', 'Family net worth by month-end since sharing started, from %(first)s to %(last)s now'): (None, 'साझा करना शुरू होने से अब तक महीने के अंत की परिवार की कुल संपत्ति, %(first)s से %(last)s', 'शेअरिंग सुरू झाल्यापासून महिन्याच्या शेवटची कुटुंबाची एकूण संपत्ती, %(first)s पासून आता %(last)s'),
    ('', 'Month-end points, starting the day each item was shared. Nothing from before is revealed, and daily movements are never shown.'): (None, 'महीने के अंत के बिंदु, जिस दिन हर आइटम साझा हुआ उसी दिन से। उससे पहले का कुछ नहीं दिखता, और रोज़ के उतार-चढ़ाव कभी नहीं दिखाए जाते।', 'महिन्याच्या शेवटचे बिंदू, प्रत्येक गोष्ट शेअर केलेल्या दिवसापासून. त्याआधीचे काहीही उघड होत नाही, आणि रोजचे चढ-उतार कधीही दाखवले जात नाहीत.'),
    ('', 'Sharing started %(date)s.'): (None, 'साझा करना %(date)s को शुरू हुआ।', 'शेअरिंग %(date)s रोजी सुरू झाले.'),
    ('', "The history line appears after the first month-end, using only the points from when each item was shared. Until then there is just today's number."): (None, 'इतिहास की रेखा पहले महीने के अंत के बाद दिखती है, और केवल उन बिंदुओं से बनती है जब से हर आइटम साझा हुआ। तब तक सिर्फ़ आज का आँकड़ा है।', 'इतिहासाची रेषा पहिल्या महिन्याच्या शेवटानंतर दिसते, आणि प्रत्येक गोष्ट शेअर केल्यापासूनचे बिंदूच वापरते. तोपर्यंत फक्त आजचा आकडा असतो.'),
    ('', 'What the family owns'): (None, 'परिवार के पास क्या है', 'कुटुंबाकडे काय आहे'),
    ('', 'Nothing with an amount is shared yet.'): (None, 'रकम वाला कुछ भी अभी साझा नहीं है।', 'रक्कम असलेले काहीही अजून शेअर केलेले नाही.'),
    ('', 'By member'): (None, 'सदस्य के अनुसार', 'सदस्यानुसार'),
    ('', 'Amounts in another currency, not counted'): (None, 'दूसरी मुद्रा में रकम, गिनी नहीं गई', 'दुसऱ्या चलनातील रक्कम, मोजलेली नाही'),
    ('', 'Nothing with an amount shared'): (None, 'रकम वाला कुछ साझा नहीं', 'रक्कम असलेले काही शेअर केलेले नाही'),
    ('', 'Owns %(owns)s · owes nothing'): (None, 'के पास %(owns)s · कुछ बकाया नहीं', 'कडे %(owns)s · काही थकबाकी नाही'),
    ('', 'Owns %(owns)s · owes %(owes)s'): (None, 'के पास %(owns)s · बकाया %(owes)s', 'कडे %(owns)s · थकबाकी %(owes)s'),
    ('', 'Net of what each person has chosen to share, not of everything they own.'): (None, 'हर व्यक्ति ने जो साझा करना चुना उसका शुद्ध मूल्य, उनकी सारी संपत्ति का नहीं।', 'प्रत्येकाने जे शेअर करणे निवडले त्याचे निव्वळ मूल्य, त्यांच्या सर्व संपत्तीचे नाही.'),
    ('', 'Who holds what'): (None, 'किसके पास क्या है', 'कोणाकडे काय आहे'),
    ('', 'Nothing is shared yet.'): (None, 'अभी कुछ साझा नहीं है।', 'अजून काही शेअर केलेले नाही.'),
    ('', 'See the family inventory'): (None, 'परिवार की सूची देखें', 'कुटुंबाची यादी पहा'),
    ('', 'If someone has not shared an item, it does not appear here. Numbers add up only what is listed.'): (None, 'अगर किसी ने कोई आइटम साझा नहीं किया है तो वह यहाँ नहीं दिखता। आँकड़े केवल वही जोड़ते हैं जो सूचीबद्ध है।', 'कोणीही एखादी गोष्ट शेअर केली नसेल तर ती इथे दिसत नाही. आकडे फक्त यादीत असलेल्या गोष्टींचीच बेरीज करतात.'),
    ('', 'Item'): (None, 'आइटम', 'गोष्ट'),
    ('', 'Family'): (None, 'परिवार', 'कुटुंब'),
    ('', 'None'): (None, 'कोई नहीं', 'काहीही नाही'),
    ('', '"None" means none shared.'): (None, '"कोई नहीं" का मतलब है कुछ साझा नहीं।', '"काहीही नाही" म्हणजे काही शेअर केलेले नाही.'),
    ('', 'Coming up in the next 30 days'): (None, 'अगले 30 दिनों में आने वाला', 'पुढील 30 दिवसांत येणारे'),
    ('', 'Card bills and premiums for what is shared.'): (None, 'साझा की गई चीज़ों के कार्ड बिल और प्रीमियम।', 'शेअर केलेल्या गोष्टींची कार्ड बिले आणि प्रीमियम.'),
    ('', '%(name)s bill'): (None, '%(name)s बिल', '%(name)s बिल'),
    ('', '%(name)s premium'): (None, '%(name)s प्रीमियम', '%(name)s प्रीमियम'),
    ('', 'Type'): (None, 'प्रकार', 'प्रकार'),
    ('', 'Cards'): (None, 'कार्ड', 'कार्ड'),
    ('', 'Insurance policies'): (None, 'बीमा पॉलिसियाँ', 'विमा पॉलिसी'),
    ('', 'Loans'): (None, 'लोन', 'कर्जे'),
    ('', 'Bank accounts and deposits'): (None, 'बैंक खाते और जमा', 'बँक खाती आणि ठेवी'),
    ('', 'Card'): (None, 'कार्ड', 'कार्ड'),
    ('', 'Held by'): (None, 'किसके पास', 'कोणाकडे'),
    ('', 'Used'): (None, 'उपयोग', 'वापर'),
    ('', 'Not shared'): (None, 'साझा नहीं', 'शेअर केलेले नाही'),
    ('', 'Cards with amounts shared'): (None, 'रकम साझा वाले कार्ड', 'रक्कम शेअर केलेली कार्डे'),
    ('', '%(pct)s% used'): (None, '%(pct)s% उपयोग', '%(pct)s% वापरले'),
    ('', 'Policy'): (None, 'पॉलिसी', 'पॉलिसी'),
    ('', 'Covers'): (None, 'कवर करती है', 'कव्हर करते'),
    ('', 'Premium a year'): (None, 'प्रति वर्ष प्रीमियम', 'वार्षिक प्रीमियम'),
    ('', 'Details not shared'): (None, 'विवरण साझा नहीं', 'तपशील शेअर केलेले नाहीत'),
    ('', 'due %(date)s'): (None, 'देय %(date)s', 'देय %(date)s'),
    ('', 'Policy numbers are never shared. Held by is whose policy it is. Covers is who it protects.'): (None, 'पॉलिसी संख्याएँ कभी साझा नहीं होतीं। "किसके पास" का मतलब पॉलिसी किसकी है। "कवर करती है" का मतलब वह किसकी सुरक्षा करती है।', 'पॉलिसी क्रमांक कधीही शेअर होत नाहीत. "कोणाकडे" म्हणजे पॉलिसी कोणाची आहे. "कव्हर करते" म्हणजे ती कोणाचे संरक्षण करते.'),
    ('', 'Property and vehicles'): (None, 'संपत्ति और वाहन', 'मालमत्ता आणि वाहने'),
    ('', 'Fixed income'): (None, 'निश्चित आय', 'निश्चित उत्पन्न'),
    ('', 'Cash and bank'): (None, 'नकद और बैंक', 'रोख आणि बँक'),
    ('', 'Insurance savings plans'): (None, 'बीमा बचत योजनाएँ', 'विमा बचत योजना'),
    ('', 'Nothing is shared yet. Each member chooses what to share on the What I share page.'): (None, 'अभी कुछ साझा नहीं है। हर सदस्य "मैं क्या साझा करता हूँ" पन्ने पर चुनता है कि क्या साझा करना है।', 'अजून काही शेअर केलेले नाही. प्रत्येक सदस्य "मी काय शेअर करतो" पानावर काय शेअर करायचे ते निवडतो.'),

    # ---- Phase 4: gentle notes ----
    ('', 'Gentle notes'): (None, 'सौम्य सुझाव', 'सौम्य सूचना'),
    ('', 'Small, optional observations from what is shared. Nothing to fix, just good to know.'): (None, 'साझा की गई जानकारी से छोटे, वैकल्पिक सुझाव। कुछ ठीक करने को नहीं, बस जानना अच्छा है।', 'शेअर केलेल्या माहितीवरून लहान, ऐच्छिक सूचना. काही दुरुस्त करायचे नाही, फक्त माहीत असणे चांगले.'),
    ('', 'Turn off gentle notes'): (None, 'सौम्य सुझाव बंद करें', 'सौम्य सूचना बंद करा'),
    ('', 'Dismiss'): (None, 'हटाएँ', 'काढून टाका'),
    ('', 'Gentle notes are off for you.'): (None, 'आपके लिए सौम्य सुझाव बंद हैं।', 'तुमच्यासाठी सौम्य सूचना बंद आहेत.'),
    ('', 'Turn on'): (None, 'चालू करें', 'चालू करा'),
    ('', '%(head)s and %(last)s'): (None, '%(head)s और %(last)s', '%(head)s आणि %(last)s'),
    ('', '%(name)s on %(date)s'): (None, '%(date)s को %(name)s', '%(date)s रोजी %(name)s'),
    ('', 'Health cover is shared for %(covered)s. Nothing is shared yet for %(missing)s. If they have a policy, they can add it. If not, there is nothing to do.'): (None, '%(covered)s के लिए स्वास्थ्य कवर साझा है। %(missing)s के लिए अभी कुछ साझा नहीं है। अगर उनकी कोई पॉलिसी है तो वे जोड़ सकते हैं। नहीं है तो कुछ करने की ज़रूरत नहीं।', '%(covered)s यांच्यासाठी आरोग्य कव्हर शेअर केले आहे. %(missing)s यांच्यासाठी अजून काही शेअर केलेले नाही. त्यांची पॉलिसी असेल तर ते जोडू शकतात. नसेल तर काहीही करायचे नाही.'),
    ('', 'One card bill falls in the next 30 days: %(items)s. Nothing to do but keep it in mind.'): ('%(count)d card bills fall in the next 30 days: %(items)s. Nothing to do but keep them in mind.', ('अगले 30 दिनों में एक कार्ड बिल आएगा: %(items)s। बस ध्यान में रखें।', 'अगले 30 दिनों में %(count)d कार्ड बिल आएँगे: %(items)s। बस ध्यान में रखें।'), ('पुढील 30 दिवसांत एक कार्ड बिल येईल: %(items)s. फक्त लक्षात ठेवा.', 'पुढील 30 दिवसांत %(count)d कार्ड बिले येतील: %(items)s. फक्त लक्षात ठेवा.')),
    ('', 'One insurance premium falls due in the next 30 days: %(items)s.'): ('%(count)d insurance premiums fall due in the next 30 days: %(items)s.', ('अगले 30 दिनों में एक बीमा प्रीमियम देय है: %(items)s।', 'अगले 30 दिनों में %(count)d बीमा प्रीमियम देय हैं: %(items)s।'), ('पुढील 30 दिवसांत एक विमा प्रीमियम देय आहे: %(items)s.', 'पुढील 30 दिवसांत %(count)d विमा प्रीमियम देय आहेत: %(items)s.')),
    # ---- Phase 5: emergency access ----
    ('', '%(d)s days %(h)s hours'): (None, '%(d)s दिन %(h)s घंटे', '%(d)s दिवस %(h)s तास'),
    ('', '%(name)s approved your request'): (None, '%(name)s ने आपका अनुरोध मंज़ूर किया', '%(name)s यांनी तुमची विनंती मंजूर केली'),
    ('', '%(name)s asked for your emergency sheet'): (None, '%(name)s ने आपकी आपातकालीन शीट माँगी', '%(name)s यांनी तुमची आणीबाणी शीट मागितली'),
    ('', '%(name)s can answer'): (None, '%(name)s जवाब दे सकते हैं', '%(name)s उत्तर देऊ शकतात'),
    ('', '%(name)s can ask'): (None, '%(name)s माँग सकते हैं', '%(name)s मागू शकतात'),
    ('', '%(name)s can now open your emergency sheet'): (None, '%(name)s अब आपकी आपातकालीन शीट खोल सकते हैं', '%(name)s आता तुमची आणीबाणी शीट उघडू शकतात'),
    ('', '%(name)s has chosen to let you ask for their emergency sheet.'): (None, '%(name)s ने आपको अपनी आपातकालीन शीट माँगने की अनुमति दी है।', '%(name)s यांनी तुम्हाला त्यांची आणीबाणी शीट मागण्याची परवानगी दिली आहे.'),
    ('', '%(name)s has not shared their emergency sheet'): (None, '%(name)s ने अपनी आपातकालीन शीट साझा नहीं की', '%(name)s यांनी त्यांची आणीबाणी शीट शेअर केली नाही'),
    ('', '%(name)s has not shared their sheet'): (None, '%(name)s ने अपनी शीट साझा नहीं की', '%(name)s यांनी त्यांची शीट शेअर केली नाही'),
    ('', '%(name)s will be told. If they do not answer within %(days)s days you get a read-only copy.'): (None, '%(name)s को बताया जाएगा। अगर वे %(days)s दिनों में जवाब नहीं देते तो आपको केवल-पढ़ने वाली प्रति मिलेगी।', '%(name)s यांना कळवले जाईल. त्यांनी %(days)s दिवसांत उत्तर दिले नाही तर तुम्हाला फक्त-वाचनीय प्रत मिळेल.'),
    ('', '%(when)s. We have told them.'): (None, '%(when)s। हमने उन्हें बता दिया है।', '%(when)s. आम्ही त्यांना कळवले आहे.'),
    ('', 'Access withdrawn.'): (None, 'पहुँच वापस ली गई।', 'प्रवेश काढून घेतला.'),
    ('', 'Always visible to them'): (None, 'उन्हें हमेशा दिखे', 'त्यांना नेहमी दिसेल'),
    ('', 'Approve now'): (None, 'अभी मंज़ूर करें', 'आता मंजूर करा'),
    ('', 'Approved.'): (None, 'मंज़ूर किया गया।', 'मंजूर केले.'),
    ('', 'Ask for the emergency sheet'): (None, 'आपातकालीन शीट माँगें', 'आणीबाणी शीट मागा'),
    ('', 'Ask for the sheet'): (None, 'शीट माँगें', 'शीट मागा'),
    ('', 'Asked on %(when)s.'): (None, '%(when)s को माँगी गई।', '%(when)s रोजी मागितली.'),
    ('', 'Back to edit'): (None, 'संपादन पर वापस', 'संपादनाकडे परत'),
    ('', 'Built from what you already track. Switch off anything you want to keep off it.'): (None, 'आप जो पहले से ट्रैक करते हैं उसी से बनी है। जो इसमें नहीं रखना उसे बंद कर दें।', 'तुम्ही आधीच ट्रॅक करता त्यावरून बनलेली. ज्यात नको ते बंद करा.'),
    ('', 'Cancel request'): (None, 'अनुरोध रद्द करें', 'विनंती रद्द करा'),
    ('', 'Choose how they get access.'): (None, 'चुनें कि उन्हें पहुँच कैसे मिले।', 'त्यांना प्रवेश कसा मिळेल ते निवडा.'),
    ('', 'Deny'): (None, 'अस्वीकार करें', 'नाकारा'),
    ('', 'Emergency sheet'): (None, 'आपातकालीन शीट', 'आणीबाणी शीट'),
    ('', 'How they get access'): (None, 'उन्हें पहुँच कैसे मिलती है', 'त्यांना प्रवेश कसा मिळतो'),
    ('', 'If something happens to me'): (None, 'अगर मुझे कुछ हो जाए', 'मला काही झाले तर'),
    ('', 'If you do nothing, they get a read-only copy in'): (None, 'अगर आप कुछ नहीं करते, तो उन्हें केवल-पढ़ने वाली प्रति मिलेगी', 'तुम्ही काही केले नाही तर त्यांना फक्त-वाचनीय प्रत मिळेल'),
    ('', 'If you do nothing, they get a read-only copy in %(days)d days. You can deny or approve now.'): (None, 'अगर आप कुछ नहीं करते, तो उन्हें %(days)d दिनों में केवल-पढ़ने वाली प्रति मिलेगी। आप अभी अस्वीकार या मंज़ूर कर सकते हैं।', 'तुम्ही काही केले नाही तर त्यांना %(days)d दिवसांत फक्त-वाचनीय प्रत मिळेल. तुम्ही आता नाकारू किंवा मंजूर करू शकता.'),
    ('', 'Include %(name)s'): (None, '%(name)s शामिल करें', '%(name)s समाविष्ट करा'),
    ('', 'Keep each note under %(max)d characters.'): (None, 'हर नोट %(max)d अक्षरों से कम रखें।', 'प्रत्येक नोंद %(max)d अक्षरांपेक्षा कमी ठेवा.'),
    ('', 'Needs your answer'): (None, 'आपके जवाब की ज़रूरत है', 'तुमच्या उत्तराची गरज आहे'),
    ('', 'Never put account numbers, passwords or PINs here.'): (None, 'यहाँ खाता संख्या, पासवर्ड या पिन कभी न लिखें।', 'इथे खाते क्रमांक, पासवर्ड किंवा पिन कधीही लिहू नका.'),
    ('', 'No accounts are on the sheet.'): (None, 'शीट पर कोई खाता नहीं है।', 'शीटवर कोणतेही खाते नाही.'),
    ('', 'Nobody else is in your family yet. Invite someone on the Members page.'): (None, 'आपके परिवार में अभी कोई और नहीं है। सदस्य पन्ने पर किसी को आमंत्रित करें।', 'तुमच्या कुटुंबात अजून कोणी नाही. सदस्य पानावर कोणाला आमंत्रित करा.'),
    ('', 'Nobody has let you ask for their emergency sheet.'): (None, 'किसी ने आपको अपनी आपातकालीन शीट माँगने की अनुमति नहीं दी है।', 'कोणीही तुम्हाला त्यांची आणीबाणी शीट मागण्याची परवानगी दिलेली नाही.'),
    ('', 'Nominee'): (None, 'नामित', 'नामनिर्देशित'),
    ('', 'Not shared for now'): (None, 'फ़िलहाल साझा नहीं', 'सध्या शेअर केलेली नाही'),
    ('', 'Notes for the people you trust'): (None, 'आपके भरोसेमंद लोगों के लिए नोट', 'तुमच्या विश्वासू लोकांसाठी नोंदी'),
    ('', 'On request, with a delay'): (None, 'माँगने पर, देरी के साथ', 'विनंतीवर, विलंबासह'),
    ('', 'Only the owner of the sheet can answer.'): (None, 'केवल शीट का मालिक जवाब दे सकता है।', 'फक्त शीटचा मालक उत्तर देऊ शकतो.'),
    ('', 'Open'): (None, 'खोलें', 'उघडा'),
    ('', 'People to call'): (None, 'जिन्हें फ़ोन करना है', 'ज्यांना फोन करायचा'),
    ('', 'People who can open your sheet'): (None, 'जो आपकी शीट खोल सकते हैं', 'जे तुमची शीट उघडू शकतात'),
    ('', 'Pick what goes on your emergency sheet and who can ask for it. Nothing here is visible to anyone until you decide.'): (None, 'चुनें कि आपकी आपातकालीन शीट पर क्या हो और इसे कौन माँग सकता है। जब तक आप तय नहीं करते, यहाँ का कुछ भी किसी को नहीं दिखता।', 'तुमच्या आणीबाणी शीटवर काय असेल आणि ती कोण मागू शकते ते निवडा. तुम्ही ठरवेपर्यंत इथले काहीही कोणालाही दिसत नाही.'),
    ('', 'Print or save as PDF'): (None, 'प्रिंट करें या PDF में सहेजें', 'प्रिंट करा किंवा PDF म्हणून सेव्ह करा'),
    ('', 'Read-only copy on %(date)s'): (None, '%(date)s को केवल-पढ़ने वाली प्रति', '%(date)s रोजी फक्त-वाचनीय प्रत'),
    ('', 'Request cancelled.'): (None, 'अनुरोध रद्द किया गया।', 'विनंती रद्द केली.'),
    ('', 'Request sent'): (None, 'अनुरोध भेजा गया', 'विनंती पाठवली'),
    ('', 'Request sent. They have been told.'): (None, 'अनुरोध भेजा गया। उन्हें बता दिया गया है।', 'विनंती पाठवली. त्यांना कळवले आहे.'),
    ('', 'Save emergency sheet'): (None, 'आपातकालीन शीट सहेजें', 'आणीबाणी शीट सेव्ह करा'),
    ('', 'Saved on %(date)s. Read-only. It says what exists and where to look, never account numbers or passwords.'): (None, '%(date)s को सहेजी गई। केवल-पढ़ने के लिए। इसमें बताया गया है कि क्या है और कहाँ देखना है, खाता संख्या या पासवर्ड कभी नहीं।', '%(date)s रोजी सेव्ह केली. फक्त वाचनासाठी. यात काय आहे आणि कुठे पहायचे ते सांगितले आहे, खाते क्रमांक किंवा पासवर्ड कधीही नाही.'),
    ('', 'Sheets shared with you'): (None, 'आपके साथ साझा की गई शीट', 'तुमच्यासोबत शेअर केलेल्या शीट'),
    ('', 'Sum assured %(amount)s'): (None, 'बीमित राशि %(amount)s', 'विमा रक्कम %(amount)s'),
    ('', 'That looks like an account or card number. Please leave numbers out: say where to look, not what the number is.'): (None, 'यह खाता या कार्ड संख्या जैसा लगता है। कृपया संख्याएँ न लिखें: बताएँ कि कहाँ देखना है, संख्या क्या है यह नहीं।', 'हे खाते किंवा कार्ड क्रमांकासारखे दिसते. कृपया क्रमांक लिहू नका: कुठे पहायचे ते सांगा, क्रमांक काय आहे ते नाही.'),
    ('', 'That request cannot be cancelled.'): (None, 'वह अनुरोध रद्द नहीं किया जा सकता।', 'ती विनंती रद्द करता येत नाही.'),
    ('', 'That request is already closed.'): (None, 'वह अनुरोध पहले ही बंद हो चुका है।', 'ती विनंती आधीच बंद झाली आहे.'),
    ('', 'The sheet says what exists and where to look. It never contains account numbers or passwords.'): (None, 'शीट बताती है कि क्या है और कहाँ देखना है। इसमें खाता संख्या या पासवर्ड कभी नहीं होते।', 'शीट काय आहे आणि कुठे पहायचे ते सांगते. त्यात खाते क्रमांक किंवा पासवर्ड कधीही नसतात.'),
    ('', 'Their sheet stays closed for now.'): (None, 'उनकी शीट फ़िलहाल बंद रहती है।', 'त्यांची शीट सध्या बंद राहते.'),
    ('', 'Their sheet stays closed for now. You can ask again later.'): (None, 'उनकी शीट फ़िलहाल बंद रहती है। आप बाद में फिर माँग सकते हैं।', 'त्यांची शीट सध्या बंद राहते. तुम्ही नंतर पुन्हा मागू शकता.'),
    ('', 'They ask. You are told by email and a notification, and can deny. If you do not answer within %(days)s days they get a read-only copy.'): (None, 'वे माँगते हैं। आपको ईमेल और सूचना से बताया जाता है, और आप अस्वीकार कर सकते हैं। अगर आप %(days)s दिनों में जवाब नहीं देते तो उन्हें केवल-पढ़ने वाली प्रति मिलती है।', 'ते मागतात. तुम्हाला ईमेल आणि सूचनेने कळवले जाते, आणि तुम्ही नाकारू शकता. तुम्ही %(days)s दिवसांत उत्तर दिले नाही तर त्यांना फक्त-वाचनीय प्रत मिळते.'),
    ('', 'They can approve or deny at any time.'): (None, 'वे किसी भी समय मंज़ूर या अस्वीकार कर सकते हैं।', 'ते कधीही मंजूर किंवा नाकारू शकतात.'),
    ('', 'They can open the sheet at any time. Best for a spouse you trust fully.'): (None, 'वे शीट कभी भी खोल सकते हैं। ऐसे जीवनसाथी के लिए सबसे अच्छा जिन पर आप पूरा भरोसा करते हैं।', 'ते शीट कधीही उघडू शकतात. ज्यांच्यावर तुमचा पूर्ण विश्वास आहे अशा जोडीदारासाठी सर्वोत्तम.'),
    ('', 'They would see the sheet exactly as you saved it, read-only. Nothing else about your accounts.'): (None, 'उन्हें शीट ठीक वैसी ही दिखेगी जैसी आपने सहेजी है, केवल-पढ़ने के लिए। आपके खातों के बारे में और कुछ नहीं।', 'तुम्ही सेव्ह केली तशीच शीट त्यांना दिसेल, फक्त वाचनासाठी. तुमच्या खात्यांबद्दल दुसरे काहीही नाही.'),
    ('', 'This is exactly what the people you allow will see, read-only.'): (None, 'आप जिन्हें अनुमति देते हैं उन्हें ठीक यही दिखेगा, केवल-पढ़ने के लिए।', 'तुम्ही ज्यांना परवानगी देता त्यांना नेमके हेच दिसेल, फक्त वाचनासाठी.'),
    ('', 'This sheet says what exists and where to look. It is not a will or a legal nomination.'): (None, 'यह शीट बताती है कि क्या है और कहाँ देखना है। यह वसीयत या कानूनी नामांकन नहीं है।', 'ही शीट काय आहे आणि कुठे पहायचे ते सांगते. हे मृत्युपत्र किंवा कायदेशीर नामनिर्देशन नाही.'),
    ('', 'Waiting for %(name)s'): (None, '%(name)s का इंतज़ार', '%(name)s यांची प्रतीक्षा'),
    ('', 'What %(name)s wants you to know'): (None, '%(name)s आपको क्या बताना चाहते हैं', '%(name)s यांना तुम्हाला काय सांगायचे आहे'),
    ('', 'What is on the sheet'): (None, 'शीट पर क्या है', 'शीटवर काय आहे'),
    ('', 'Where my documents are'): (None, 'मेरे दस्तावेज़ कहाँ हैं', 'माझी कागदपत्रे कुठे आहेत'),
    ('', 'Who can ask for it'): (None, 'इसे कौन माँग सकता है', 'ती कोण मागू शकते'),
    ('', 'Withdraw access'): (None, 'पहुँच वापस लें', 'प्रवेश काढून घ्या'),
    ('', 'You asked'): (None, 'आपने माँगा', 'तुम्ही मागितले'),
    ('', 'You can ask for it'): (None, 'आप इसे माँग सकते हैं', 'तुम्ही ती मागू शकता'),
    ('', 'You can now open their emergency sheet. It is read-only.'): (None, 'अब आप उनकी आपातकालीन शीट खोल सकते हैं। यह केवल-पढ़ने के लिए है।', 'आता तुम्ही त्यांची आणीबाणी शीट उघडू शकता. ती फक्त वाचनासाठी आहे.'),
    ('', 'You can open it'): (None, 'आप इसे खोल सकते हैं', 'तुम्ही ती उघडू शकता'),
    ('', 'You cannot ask for this sheet right now.'): (None, 'आप अभी यह शीट नहीं माँग सकते।', 'तुम्ही आत्ता ही शीट मागू शकत नाही.'),
    ('', 'You did not answer within the delay, so they now have a read-only copy. You can withdraw access at any time.'): (None, 'आपने देरी की अवधि में जवाब नहीं दिया, इसलिए अब उनके पास केवल-पढ़ने वाली प्रति है। आप कभी भी पहुँच वापस ले सकते हैं।', 'तुम्ही विलंबाच्या कालावधीत उत्तर दिले नाही, म्हणून आता त्यांच्याकडे फक्त-वाचनीय प्रत आहे. तुम्ही कधीही प्रवेश काढून घेऊ शकता.'),
    ('', 'You get it automatically unless they deny first. %(d)s days %(h)s hours to go.'): (None, 'वे पहले अस्वीकार न करें तो यह आपको अपने आप मिल जाती है। %(d)s दिन %(h)s घंटे बाकी।', 'ते आधी नाकारले नाहीत तर ती तुम्हाला आपोआप मिळते. %(d)s दिवस %(h)s तास बाकी.'),
    ('', 'You have no accounts yet. Add one on the Accounts page, then come back.'): (None, 'आपका अभी कोई खाता नहीं है। खाते वाले पन्ने पर एक जोड़ें, फिर वापस आएँ।', 'तुमचे अजून कोणतेही खाते नाही. खाती पानावर एक जोडा, मग परत या.'),
    ('', 'Your emergency sheet is saved.'): (None, 'आपकी आपातकालीन शीट सहेज ली गई।', 'तुमची आणीबाणी शीट सेव्ह केली.'),
    ('', 'nominee %(name)s'): (None, 'नामित: %(name)s', 'नामनिर्देशित: %(name)s'),

    # ---- public pages: landing section and Features page ----
    ('', 'Family sharing'): (None, 'पारिवारिक साझाकरण', 'कौटुंबिक शेअरिंग'),
    ('', 'One household picture, with each person choosing what to share.'): (None, 'पूरे घर की एक तस्वीर, जिसमें हर व्यक्ति खुद चुनता है कि क्या साझा करना है।', 'संपूर्ण घराची एक तस्वीर, ज्यात प्रत्येक जण काय शेअर करायचे ते स्वतः ठरवतो.'),
    ('', 'One family net worth'): (None, 'परिवार की एक नेट वर्थ', 'कुटुंबाची एक नेट वर्थ'),
    ('', "The family sees one combined net worth: what you own, what you owe, and how it splits across cash, investments, property and more. The numbers come from the same valuation as each person's own net worth, so they never disagree."): (None, 'परिवार को एक संयुक्त नेट वर्थ दिखती है: आपके पास क्या है, आप पर क्या बकाया है, और यह नकद, निवेश, संपत्ति आदि में कैसे बँटा है। आँकड़े हर व्यक्ति की अपनी नेट वर्थ वाले ही मूल्यांकन से आते हैं, इसलिए वे कभी अलग नहीं होते।', 'कुटुंबाला एकत्रित नेट वर्थ दिसते: तुमच्याकडे काय आहे, काय देणे आहे, आणि ते रोख, गुंतवणूक, मालमत्ता वगैरेमध्ये कसे विभागले आहे. आकडे प्रत्येकाच्या स्वतःच्या नेट वर्थच्या मूल्यांकनातूनच येतात, त्यामुळे ते कधीच वेगळे दिसत नाहीत.'),
    ('', 'Who holds what'): (None, 'किसके पास क्या है', 'कोणाकडे काय आहे'),
    ('', 'See every credit card, insurance policy, loan and deposit the family has, and whose it is. A card shows its limit and next bill date, a policy its cover, premium and due date, a loan its EMI.'): (None, 'परिवार के हर क्रेडिट कार्ड, बीमा पॉलिसी, ऋण और जमा को देखें, और यह भी कि वह किसका है। कार्ड में उसकी सीमा और अगली बिल तारीख दिखती है, पॉलिसी में कवर, प्रीमियम और देय तारीख, और ऋण में उसकी EMI।', 'कुटुंबाचे प्रत्येक क्रेडिट कार्ड, विमा पॉलिसी, कर्ज आणि ठेव पहा, आणि ते कोणाचे आहे तेही. कार्डवर मर्यादा आणि पुढची बिल तारीख दिसते, पॉलिसीवर कव्हर, प्रीमियम आणि देय तारीख, आणि कर्जावर त्याचा EMI.'),
    ('', 'Nothing is shared until you share it'): (None, 'आप साझा करें, तभी कुछ साझा होता है', 'तुम्ही शेअर करेपर्यंत काहीही शेअर होत नाही'),
    ('', 'Joining a family shares nothing. You pick each account and choose Show amounts or List only. Transactions, categories, notes and the holdings inside an account are never shared.'): (None, 'परिवार से जुड़ने पर कुछ भी साझा नहीं होता। आप हर खाता चुनते हैं और राशि दिखाएँ या केवल सूची में रखें में से एक चुनते हैं। लेन-देन, श्रेणियाँ, नोट और खाते के भीतर की होल्डिंग्स कभी साझा नहीं होतीं।', 'कुटुंबात सामील झाल्याने काहीही शेअर होत नाही. तुम्ही प्रत्येक खाते निवडता आणि रक्कम दाखवा किंवा फक्त यादी यापैकी एक ठरवता. व्यवहार, श्रेणी, नोंदी आणि खात्यातील होल्डिंग्ज कधीही शेअर होत नाहीत.'),
    ('', 'See exactly what they see'): (None, 'ठीक वही देखें जो वे देखते हैं', 'त्यांना जे दिसते तेच नेमके पहा'),
    ('', "A preview shows your family's view of your shared accounts. Give an account a nickname if its real name is private. Stop sharing at any time and it disappears for them at once."): (None, 'पूर्वावलोकन में आपके साझा खातों का परिवार वाला दृश्य दिखता है। खाते का असली नाम निजी हो तो उसे उपनाम दें। कभी भी साझा करना बंद करें, और वह उनके लिए तुरंत गायब हो जाता है।', 'पूर्वावलोकनात तुमच्या शेअर केलेल्या खात्यांचे कुटुंबाला दिसणारे रूप दिसते. खात्याचे खरे नाव खाजगी असेल तर त्याला टोपणनाव द्या. केव्हाही शेअर करणे थांबवा, आणि ते त्यांच्यासाठी लगेच नाहीसे होते.'),
    ('', 'Emergency access'): (None, 'आपातकालीन पहुँच', 'आणीबाणीतील प्रवेश'),
    ('', 'Write a short sheet of what exists and where to look, never account numbers or passwords. Choose who may ask. They request it, you can approve or deny, and if you do not answer within 7 days they get a read-only copy.'): (None, 'एक छोटी शीट लिखें कि क्या-क्या है और कहाँ देखना है, खाता नंबर या पासवर्ड कभी नहीं। चुनें कि कौन माँग सकता है। वे अनुरोध करते हैं, आप स्वीकार या अस्वीकार कर सकते हैं, और 7 दिनों में जवाब न दें तो उन्हें केवल पढ़ने वाली प्रति मिल जाती है।', 'काय आहे आणि कुठे पाहायचे याची एक छोटी शीट लिहा, खाते क्रमांक किंवा पासवर्ड कधीही नाही. कोण मागू शकते ते ठरवा. ते विनंती करतात, तुम्ही मंजूर किंवा नाकारू शकता, आणि 7 दिवसांत उत्तर दिले नाही तर त्यांना फक्त वाचता येणारी प्रत मिळते.'),
    ('', 'Gentle notes, never comparisons'): (None, 'सौम्य नोट्स, तुलना कभी नहीं', 'सौम्य नोंदी, तुलना कधीही नाही'),
    ('', 'A few calm, optional observations, such as a premium falling due or a card bill coming up, built only from what is shared. They never compare people or judge an amount, and each person can switch them off.'): (None, 'कुछ शांत, वैकल्पिक टिप्पणियाँ, जैसे कोई प्रीमियम देय होना या कार्ड बिल आना, जो केवल साझा की गई जानकारी से बनती हैं। वे कभी लोगों की तुलना नहीं करतीं और किसी राशि पर राय नहीं देतीं, और हर व्यक्ति उन्हें बंद कर सकता है।', 'काही शांत, ऐच्छिक निरीक्षणे, जसे की प्रीमियम देय होणे किंवा कार्ड बिल येणे, जी फक्त शेअर केलेल्या माहितीवरून तयार होतात. ती कधीही माणसांची तुलना करत नाहीत किंवा रकमेवर मत देत नाहीत, आणि प्रत्येकजण ती बंद करू शकतो.'),
    ('', 'Your own account, your own data'): (None, 'आपका अपना खाता, आपका अपना डेटा', 'तुमचे स्वतःचे खाते, तुमचा स्वतःचा डेटा'),
    ('', 'Each person keeps their own TrackMyRupee account. Family view needs everyone on Pro, with up to 5 people. If a plan lapses, family view pauses and nothing is deleted. Leaving ends all sharing straight away.'): (None, 'हर व्यक्ति का अपना TrackMyRupee खाता रहता है। परिवार दृश्य के लिए सभी का Pro पर होना ज़रूरी है, अधिकतम 5 लोग। प्लान समाप्त हो तो परिवार दृश्य रुक जाता है और कुछ भी नहीं हटता। छोड़ने पर सारा साझाकरण तुरंत बंद हो जाता है।', 'प्रत्येकाचे स्वतःचे TrackMyRupee खाते राहते. कुटुंब दृश्यासाठी सर्वजण Pro वर असणे आवश्यक आहे, जास्तीत जास्त 5 जण. प्लॅन संपला तर कुटुंब दृश्य थांबते आणि काहीही हटवले जात नाही. सोडल्यावर सर्व शेअरिंग लगेच थांबते.'),
    ('', 'One family. One true net worth.'): (None, 'एक परिवार। एक सच्ची नेट वर्थ।', 'एक कुटुंब. एक खरी नेट वर्थ.'),
    ('', 'Each of you keeps your own account and chooses what to share. The family sees a combined, read-only picture of only what was shared: what you own, what you owe, and who holds which card, policy or loan.'): (None, 'आप सभी का अपना खाता रहता है और आप तय करते हैं कि क्या साझा करना है। परिवार को केवल साझा की गई चीज़ों की संयुक्त, केवल-पढ़ने वाली तस्वीर दिखती है: आपके पास क्या है, क्या बकाया है, और कौन-सा कार्ड, पॉलिसी या ऋण किसके पास है।', 'तुम्ही प्रत्येकजण स्वतःचे खाते ठेवता आणि काय शेअर करायचे ते ठरवता. कुटुंबाला फक्त शेअर केलेल्या गोष्टींचे एकत्रित, फक्त वाचता येणारे चित्र दिसते: तुमच्याकडे काय आहे, काय देणे आहे, आणि कोणते कार्ड, पॉलिसी किंवा कर्ज कोणाकडे आहे.'),
    ('', 'Nothing is shared by default'): (None, 'डिफ़ॉल्ट रूप से कुछ भी साझा नहीं होता', 'डिफॉल्टने काहीही शेअर होत नाही'),
    ('', 'Joining shares nothing. You pick each account, and whether the family sees the amount or only that it exists.'): (None, 'जुड़ने से कुछ साझा नहीं होता। आप हर खाता चुनते हैं, और यह भी कि परिवार राशि देखे या केवल यह कि वह मौजूद है।', 'सामील झाल्याने काहीही शेअर होत नाही. तुम्ही प्रत्येक खाते निवडता, आणि कुटुंबाने रक्कम पाहावी की फक्त ते अस्तित्वात आहे एवढेच, तेही ठरवता.'),
    ('', 'Accounts, never transactions'): (None, 'खाते, लेन-देन कभी नहीं', 'खाती, व्यवहार कधीही नाही'),
    ('', 'No one sees a transaction, category, note or account number. Only the account and the few fields you allow.'): (None, 'कोई भी लेन-देन, श्रेणी, नोट या खाता नंबर नहीं देखता। केवल खाता और वे कुछ फ़ील्ड जिनकी आप अनुमति देते हैं।', 'कोणालाही व्यवहार, श्रेणी, नोंद किंवा खाते क्रमांक दिसत नाही. फक्त खाते आणि तुम्ही परवानगी दिलेली काही फील्ड्स.'),
    ('', "Preview your family's view of your accounts before anyone else does."): (None, 'किसी और के देखने से पहले अपने खातों का परिवार वाला दृश्य खुद देखें।', 'इतर कोणी पाहण्याआधी तुमच्या खात्यांचे कुटुंबाला दिसणारे रूप तुम्ही स्वतः पहा.'),
    ('', 'If something happens to me'): (None, 'अगर मुझे कुछ हो जाए', 'मला काही झाले तर'),
    ('', 'An emergency sheet that says where to look. By default it opens on request, with a delay and a veto for you.'): (None, 'एक आपातकालीन शीट जो बताती है कि कहाँ देखना है। डिफ़ॉल्ट रूप से यह अनुरोध पर खुलती है, देरी के साथ और आपके लिए इनकार के अधिकार के साथ।', 'कुठे पाहायचे ते सांगणारी आणीबाणी शीट. डिफॉल्टनुसार ती विनंतीवर उघडते, विलंबासह आणि तुम्हाला नकाराधिकारासह.'),
    ('', 'Included with Pro'): (None, 'Pro में शामिल', 'Pro मध्ये समाविष्ट'),
    ('', 'Everyone in the family needs Pro. Up to five people.'): (None, 'परिवार में सभी को Pro चाहिए। अधिकतम पाँच लोग।', 'कुटुंबातील सर्वांना Pro लागते. जास्तीत जास्त पाच जण.'),
    ('', 'How family sharing works'): (None, 'पारिवारिक साझाकरण कैसे काम करता है', 'कौटुंबिक शेअरिंग कसे काम करते'),
    ('', 'Read the guide'): (None, 'गाइड पढ़ें', 'मार्गदर्शक वाचा'),
    ('', 'Examples of family sharing'): (None, 'पारिवारिक साझाकरण के उदाहरण', 'कौटुंबिक शेअरिंगची उदाहरणे'),
    ('', 'Your family net worth'): (None, 'आपके परिवार की नेट वर्थ', 'तुमच्या कुटुंबाची नेट वर्थ'),
    ('', 'Together you own ₹48.2L and owe ₹6.1L, a net worth of ₹42.1L. Three of you have shared amounts.'): (None, 'मिलाकर आपके पास ₹48.2L है और ₹6.1L बकाया है, यानी ₹42.1L की नेट वर्थ। आप में से तीन ने राशियाँ साझा की हैं।', 'एकत्रितपणे तुमच्याकडे ₹48.2L आहेत आणि ₹6.1L देणे आहे, म्हणजे ₹42.1L ची नेट वर्थ. तुमच्यापैकी तिघांनी रकमा शेअर केल्या आहेत.'),
    ('', 'Owns'): (None, 'स्वामित्व', 'मालकीचे'),
    ('', 'Owes'): (None, 'बकाया', 'देणे'),
    ('', 'A premium falls due'): (None, 'एक प्रीमियम देय होने वाला है', 'एक प्रीमियम देय होत आहे'),
    ('', "Meera's health policy premium of ₹24,000 falls due on 14 Dec. Nothing to do but keep it in mind."): (None, 'मीरा की स्वास्थ्य पॉलिसी का ₹24,000 का प्रीमियम 14 दिसंबर को देय है। बस ध्यान में रखें, और कुछ करने की ज़रूरत नहीं।', 'मीराच्या आरोग्य पॉलिसीचा ₹24,000 चा प्रीमियम 14 डिसेंबरला देय आहे. फक्त लक्षात ठेवा, काही करायचे नाही.'),
    ('', 'Two credit cards and four insurance policies across the family, held by Arjun, Priya and Meera.'): (None, 'परिवार में दो क्रेडिट कार्ड और चार बीमा पॉलिसियाँ हैं, जो अर्जुन, प्रिया और मीरा के पास हैं।', 'कुटुंबात दोन क्रेडिट कार्ड आणि चार विमा पॉलिसी आहेत, ज्या अर्जुन, प्रिया आणि मीरा यांच्याकडे आहेत.'),
    ('', 'Examples. Names and numbers are made up.'): (None, 'उदाहरण। नाम और आँकड़े काल्पनिक हैं।', 'उदाहरणे. नावे आणि आकडे काल्पनिक आहेत.'),
    # ---- sharing a whole group at once ----
    ('', '%(on)s of %(total)s shared'): (None, '%(total)s में से %(on)s साझा', '%(total)s पैकी %(on)s शेअर केलेली'),
    ('', 'Share all'): (None, 'सब साझा करें', 'सर्व शेअर करा'),
    ('', 'Stop sharing all'): (None, 'सब साझा करना बंद करें', 'सर्व शेअर करणे थांबवा'),
    ('', 'Stop sharing all in %(name)s'): (None, '%(name)s में सब साझा करना बंद करें', '%(name)s मधील सर्व शेअर करणे थांबवा'),
    ('', '%(on)s of %(total)s on the sheet'): (None, '%(total)s में से %(on)s शीट पर', '%(total)s पैकी %(on)s शीटवर'),
    ('', 'Add all'): (None, 'सब जोड़ें', 'सर्व जोडा'),
    ('', 'Remove all'): (None, 'सब हटाएँ', 'सर्व काढा'),
    ('', 'Add or remove all in %(name)s'): (None, '%(name)s में सब जोड़ें या हटाएँ', '%(name)s मधील सर्व जोडा किंवा काढा'),
    ('', 'Owed'): (None, 'बकाया', 'देणे'),
    ('', '%(counter)s private account is hidden.'): (
        '%(counter)s private accounts are hidden.',
        ('%(counter)s निजी खाता छिपा है।', '%(counter)s निजी खाते छिपे हैं।'),
        ('%(counter)s खाजगी खाते लपवलेले आहे.', '%(counter)s खाजगी खाती लपवलेली आहेत.')),
    ('', 'Amounts are hidden on %(counter)s listed item.'): (
        'Amounts are hidden on %(counter)s listed items.',
        ('%(counter)s सूचीबद्ध आइटम पर राशि छिपी है।', '%(counter)s सूचीबद्ध आइटम पर राशियाँ छिपी हैं।'),
        ('%(counter)s यादीतील आयटमवरील रक्कम लपवलेली आहे.', '%(counter)s यादीतील आयटमवरील रकमा लपवलेल्या आहेत.')),
    ('', 'Transactions, categories, notes and account numbers are never shown, and nothing hints that anything is hidden.'): (
        None, 'लेन-देन, श्रेणियाँ, नोट और खाता नंबर कभी नहीं दिखाए जाते, और कुछ भी संकेत नहीं देता कि कुछ छिपा है।',
        'व्यवहार, श्रेणी, नोंदी आणि खाते क्रमांक कधीही दाखवले जात नाहीत, आणि काहीही लपवले आहे याचा मागमूस लागत नाही.'),
    ('', 'Which accounts?'): (None, 'कौन-से खाते?', 'कोणती खाती?'),
    ('', 'Share all in %(name)s'): (None, '%(name)s में सब साझा करें', '%(name)s मधील सर्व शेअर करा'),
    ('', 'Shared %(count)d item in %(group)s with your family.'): (
        'Shared %(count)d items in %(group)s with your family.',
        ('%(group)s में %(count)d आइटम परिवार के साथ साझा किया गया।', '%(group)s में %(count)d आइटम परिवार के साथ साझा किए गए।'),
        ('%(group)s मधील %(count)d आयटम कुटुंबासोबत शेअर केली.', '%(group)s मधील %(count)d आयटम कुटुंबासोबत शेअर केल्या.')),
    ('', 'Stopped sharing %(count)d item in %(group)s.'): (
        'Stopped sharing %(count)d items in %(group)s.',
        ('%(group)s में %(count)d आइटम साझा करना बंद किया।', '%(group)s में %(count)d आइटम साझा करना बंद किया।'),
        ('%(group)s मधील %(count)d आयटम शेअर करणे थांबवले.', '%(group)s मधील %(count)d आयटम शेअर करणे थांबवले.')),
    # ---- privacy policy and security page ----
    ('', 'Family Sharing (optional)'): (None, 'पारिवारिक साझाकरण (वैकल्पिक)', 'कौटुंबिक शेअरिंग (ऐच्छिक)'),
    ('', 'Other Members of Your Family'): (None, 'आपके परिवार के अन्य सदस्य', 'तुमच्या कुटुंबातील इतर सदस्य'),
    ('', 'Family sharing'): (None, 'पारिवारिक साझाकरण', 'कौटुंबिक शेअरिंग'),
    ('', 'Family Sharing'): (None, 'पारिवारिक साझाकरण', 'कौटुंबिक शेअरिंग'),
    ('', 'Your family membership, the accounts you choose to share and at what level (amounts or list only), an optional nickname for each, and an emergency sheet if you write one (what exists and where to look, never account numbers or passwords) &rarr; Showing a combined family view to the other members of your family &rarr; Consent, given when you join &rarr; Until you stop sharing, leave the family or delete your account.'): (None, 'आपकी परिवार सदस्यता, वे खाते जिन्हें आप साझा करना चुनते हैं और किस स्तर पर (राशि के साथ या केवल सूची), हर खाते का वैकल्पिक उपनाम, और यदि आप लिखें तो आपातकालीन शीट (क्या है और कहाँ देखना है, खाता नंबर या पासवर्ड कभी नहीं) &rarr; आपके परिवार के अन्य सदस्यों को संयुक्त पारिवारिक दृश्य दिखाना &rarr; सहमति, जो आप जुड़ते समय देते हैं &rarr; जब तक आप साझा करना बंद न करें, परिवार छोड़ें या अपना खाता हटाएँ।', 'तुमचे कुटुंब सदस्यत्व, तुम्ही शेअर करण्यासाठी निवडलेली खाती आणि कोणत्या स्तरावर (रकमेसह किंवा फक्त यादी), प्रत्येकाचे ऐच्छिक टोपणनाव, आणि तुम्ही लिहिल्यास आणीबाणी शीट (काय आहे आणि कुठे पाहायचे, खाते क्रमांक किंवा पासवर्ड कधीही नाही) &rarr; तुमच्या कुटुंबातील इतर सदस्यांना एकत्रित कौटुंबिक दृश्य दाखवणे &rarr; संमती, जी तुम्ही सामील होताना देता &rarr; तुम्ही शेअरिंग थांबवेपर्यंत, कुटुंब सोडेपर्यंत किंवा खाते हटवेपर्यंत.'),
    ('', 'If you join a family, the other members can see the accounts you choose to share, at the level you choose, and your emergency sheet if you allow them to ask for it. They never see transactions, categories, notes, receipts, the holdings inside an account or account numbers, and they cannot tell that an account you did not share exists. Nothing is shared until you choose it, and you can stop at any time.'): (None, 'यदि आप किसी परिवार से जुड़ते हैं, तो अन्य सदस्य वे खाते देख सकते हैं जिन्हें आप साझा करना चुनते हैं, उसी स्तर पर जो आप चुनें, और आपकी आपातकालीन शीट यदि आप उन्हें उसे माँगने की अनुमति दें। वे कभी लेन-देन, श्रेणियाँ, नोट, रसीदें, खाते के भीतर की होल्डिंग्स या खाता नंबर नहीं देखते, और वे यह नहीं जान सकते कि आपका कोई असाझा खाता मौजूद है। जब तक आप न चुनें कुछ भी साझा नहीं होता, और आप कभी भी रुक सकते हैं।', 'तुम्ही एखाद्या कुटुंबात सामील झालात, तर इतर सदस्य तुम्ही शेअर करण्यासाठी निवडलेली खाती, तुम्ही ठरवलेल्या स्तरावर पाहू शकतात, आणि तुम्ही परवानगी दिल्यास तुमची आणीबाणी शीट मागू शकतात. ते कधीही व्यवहार, श्रेणी, नोंदी, पावत्या, खात्यातील होल्डिंग्ज किंवा खाते क्रमांक पाहत नाहीत, आणि तुम्ही शेअर न केलेले खाते अस्तित्वात आहे हे त्यांना कळू शकत नाही. तुम्ही निवडेपर्यंत काहीही शेअर होत नाही, आणि तुम्ही केव्हाही थांबवू शकता.'),
    ('', "Stopping sharing or leaving a family ends the other members' view straight away and removes your shares and emergency-sheet links. Deleting your account does the same. If you owned the family, ownership passes to the longest-standing member, and if you were the last member the family ends. An invitation link is single-use and expires after 7 days; we store only a hash of it."): (None, 'साझा करना बंद करने या परिवार छोड़ने पर अन्य सदस्यों का दृश्य तुरंत समाप्त हो जाता है और आपके साझाकरण व आपातकालीन-शीट लिंक हट जाते हैं। आपका खाता हटाने पर भी यही होता है। यदि आप परिवार के मालिक थे, तो स्वामित्व सबसे पुराने सदस्य को मिल जाता है, और यदि आप अंतिम सदस्य थे तो परिवार समाप्त हो जाता है। आमंत्रण लिंक एक बार ही काम करता है और 7 दिनों में समाप्त हो जाता है; हम उसका केवल हैश संग्रहीत करते हैं।', 'शेअरिंग थांबवल्यावर किंवा कुटुंब सोडल्यावर इतर सदस्यांचे दृश्य लगेच संपते आणि तुमचे शेअर्स व आणीबाणी-शीट लिंक काढले जातात. तुमचे खाते हटवल्यावरही हेच होते. तुम्ही कुटुंबाचे मालक असाल तर मालकी सर्वात जुन्या सदस्याकडे जाते, आणि तुम्ही शेवटचे सदस्य असाल तर कुटुंब संपते. आमंत्रण लिंक एकदाच चालते आणि 7 दिवसांत संपते; आम्ही फक्त तिचा हॅश साठवतो.'),
    ('', 'Sharing is off until you turn it on, account by account. Other family members receive only a fixed set of fields for the accounts you choose, never your transactions, notes, categories, receipts, the holdings inside an account or any account numbers. Invitation links work once, expire after 7 days and are stored only as a hash. An emergency sheet says what exists and where to look, never account numbers or passwords. By default it opens only on request, after a delay you can veto; if you choose, the people you name can open it at any time. Leaving the family ends all sharing straight away. We test this with automated checks that plant private data and confirm no family page, reply, email or notification shows it.'): (None, 'साझाकरण तब तक बंद रहता है जब तक आप उसे खाता-दर-खाता चालू न करें। अन्य परिवार सदस्यों को आपके चुने हुए खातों के केवल निश्चित फ़ील्ड मिलते हैं, आपके लेन-देन, नोट, श्रेणियाँ, रसीदें, खाते के भीतर की होल्डिंग्स या कोई खाता नंबर कभी नहीं। आमंत्रण लिंक एक बार काम करते हैं, 7 दिनों बाद समाप्त होते हैं और केवल हैश के रूप में संग्रहीत होते हैं। आपातकालीन शीट बताती है कि क्या है और कहाँ देखना है, खाता नंबर या पासवर्ड कभी नहीं। डिफ़ॉल्ट रूप से वह केवल अनुरोध पर खुलती है, एक देरी के बाद जिसे आप रोक सकते हैं; आप चाहें तो आपके बताए लोग उसे कभी भी खोल सकते हैं। परिवार छोड़ने पर सारा साझाकरण तुरंत बंद हो जाता है। हम इसे स्वचालित जाँचों से परखते हैं जो निजी डेटा रखकर देखती हैं कि कोई पारिवारिक पृष्ठ, उत्तर, ईमेल या सूचना उसे नहीं दिखाती।', 'शेअरिंग तुम्ही खाते-दर-खाते चालू करेपर्यंत बंद असते. इतर कुटुंब सदस्यांना तुम्ही निवडलेल्या खात्यांचे फक्त ठराविक फील्ड्स मिळतात, तुमचे व्यवहार, नोंदी, श्रेणी, पावत्या, खात्यातील होल्डिंग्ज किंवा कोणतेही खाते क्रमांक कधीही नाही. आमंत्रण लिंक एकदाच चालतात, 7 दिवसांनी संपतात आणि फक्त हॅश म्हणून साठवल्या जातात. आणीबाणी शीट काय आहे आणि कुठे पाहायचे ते सांगते, खाते क्रमांक किंवा पासवर्ड कधीही नाही. डिफॉल्टनुसार ती फक्त विनंतीवर उघडते, तुम्ही नाकारू शकता अशा विलंबानंतर; तुम्ही निवडले तर तुम्ही नावे दिलेले लोक ती कधीही उघडू शकतात. कुटुंब सोडल्यावर सर्व शेअरिंग लगेच थांबते. आम्ही हे स्वयंचलित तपासण्यांनी तपासतो, ज्या खाजगी डेटा ठेवून पाहतात की कोणतेही कौटुंबिक पान, उत्तर, ईमेल किंवा सूचना तो दाखवत नाही.'),
    ('', 'Start'): (None, 'शुरुआत', 'सुरुवात'),
}


def render(ctx, msgid, plural, values) -> str:
    lines = ['#, python-format'] if '%(' in msgid or '%(' in (plural or '') else []
    if ctx:
        lines.append(f'msgctxt {quote(ctx)}')
    lines.append(f'msgid {quote(msgid)}')
    if plural is None:
        lines.append(f'msgstr {quote(values)}')
    else:
        lines.append(f'msgid_plural {quote(plural)}')
        lines += [f'msgstr[{i}] {quote(v)}' for i, v in enumerate(values)]
    return '\n'.join(lines)


# ---- finding the strings the family code owns -------------------------------------------------------------

_TRANS = re.compile(r"""\{%\s*trans\s+(?:"([^"]*)"|'([^']*)')[^%]*%\}""")
_BLOCK = re.compile(r'\{%\s*blocktrans(?P<args>[^%]*)%\}(?P<body>.*?)\{%\s*endblocktrans\s*%\}', re.DOTALL)
_VAR = re.compile(r'\{\{\s*(\w+)\s*\}\}')


def _block_text(body: str) -> str:
    lines = [line.strip() for line in body.strip().splitlines() if line.strip()]
    return _VAR.sub(lambda m: f'%({m.group(1)})s', ' '.join(lines))


def template_strings(text: str):
    found = set()
    for m in _TRANS.finditer(text):
        found.add(('', m.group(1) if m.group(1) is not None else m.group(2), None))
    for m in _BLOCK.finditer(text):
        body = m.group('body')
        if re.search(r'\bcount\b', m.group('args')) and re.search(r'\{%\s*plural\s*%\}', body):
            singular, plural = re.split(r'\{%\s*plural\s*%\}', body, maxsplit=1)
            counter = re.search(r'count\s+(\w+)\s*=', m.group('args'))
            counter_name = counter.group(1) if counter else 'counter'

            def fix(s):
                return _block_text(s).replace(f'%({counter_name})s', '%(counter)s')
            found.add(('', fix(singular), fix(plural)))
        else:
            found.add(('', _block_text(body), None))
    return found


def python_strings(source: str):
    found = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ''
        consts = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if name in ('_', 'gettext', 'gettext_lazy') and len(node.args) == 1 and consts:
            found.add(('', consts[0], None))
        elif name in ('ngettext', 'ngettext_lazy') and len(consts) == 2:
            found.add(('', consts[0], consts[1]))
    return found


def find_strings():
    found = {('', msgid, None) for ctx, msgid in SHARED_STRINGS}
    for pattern in TEMPLATE_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            found |= template_strings(path.read_text(encoding='utf-8'))
    for pattern in PYTHON_FILES:
        for path in sorted(ROOT.glob(pattern)):
            found |= python_strings(path.read_text(encoding='utf-8'))
    return found


# ---- merging into the catalogues ---------------------------------------------------------------------------

def _catalogue(lang: str):
    path = ROOT / 'locale' / lang / 'LC_MESSAGES' / 'django.po'
    blocks = parse_blocks(path.read_text(encoding='utf-8'))
    index = {(unquote(b['key'][0]), unquote(b['key'][1])): b for b in blocks}
    return path, blocks, index


def merge(lang: str) -> tuple[int, int]:
    path, blocks, index = _catalogue(lang)
    added = filled = 0
    out = [b['text'] for b in blocks]
    for (ctx, msgid), value in ENTRIES.items():
        plural, values = value[0], value[LOCALES[lang]]
        existing = index.get((ctx, msgid))
        entry = render(ctx, msgid, plural, values)
        if existing is None:
            out.append('#: expenses/family\n' + entry)
            added += 1
        elif not existing['translated']:
            at = out.index(existing['text'])
            comments = [ln for ln in existing['text'].splitlines() if ln.startswith('#') and 'fuzzy' not in ln and not ln.startswith('#|')]
            out[at] = '\n'.join(comments + [entry])
            filled += 1
    path.write_text('\n\n'.join(out) + '\n', encoding='utf-8')
    return added, filled


def missing() -> list[str]:
    """Family strings with no Hindi or Marathi text, neither here nor already in the catalogue."""
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
        print('\n'.join(left) if left else 'every family string has an entry')
        sys.exit(1 if left else 0)
    for code in LOCALES:
        added, filled = merge(code)
        print(f'{code}: {added} added, {filled} filled')
