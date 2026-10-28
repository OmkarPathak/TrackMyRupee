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
