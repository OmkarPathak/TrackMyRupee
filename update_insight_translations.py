"""Hindi and Marathi for the Insights Engine, merged into locale/{hi,mr}/LC_MESSAGES/django.po.

    python update_insight_translations.py          # add or fill every entry below, then run compilemessages
    python update_insight_translations.py --check  # list engine strings that still have no entry here

Entries that already have a translation in the catalogue are left alone (the module names, "Balance", "Dismiss"
and so on were translated long ago). Plural entries give (singular, plural) for each language. The strings the
engine owns are found by expenses/insights/msgids.py; a test fails if one of them has no Hindi or Marathi text.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# (context, msgid): (msgid_plural or None, hindi, marathi)
ENTRIES = {
    # ---- amount suffixes (the k, L, Cr words in "about ₹9.2k") ----
    ('amount suffix: thousand', 'k'): (None, 'हज़ार', 'हजार'),
    ('amount suffix: lakh', 'L'): (None, 'लाख', 'लाख'),
    ('amount suffix: crore', 'Cr'): (None, 'करोड़', 'कोटी'),
    ('amount suffix: million', 'M'): (None, 'मिलियन', 'दशलक्ष'),
    ('amount suffix: billion', 'B'): (None, 'अरब', 'अब्ज'),

    # ---- module names (Settings) ----
    ('', 'Expenses'): (None, 'खर्च', 'खर्च'),
    ('', 'Income'): (None, 'आय', 'उत्पन्न'),
    ('', 'Budgets'): (None, 'बजट', 'बजेट'),
    ('', 'Accounts'): (None, 'खाते', 'खाती'),
    ('', 'Loans'): (None, 'ऋण', 'कर्जे'),
    ('', 'Goals'): (None, 'लक्ष्य', 'उद्दिष्टे'),
    ('', 'Recurring payments'): (None, 'आवर्ती भुगतान', 'आवर्ती देयके'),
    ('', 'Transfers and investing'): (None, 'ट्रांसफ़र और निवेश', 'हस्तांतरण आणि गुंतवणूक'),
    ('', 'Capital events'): (None, 'पूँजीगत घटनाएँ', 'भांडवली घटना'),
    ('', 'Holdings'): (None, 'होल्डिंग्स', 'होल्डिंग्ज'),
    ('', 'Calendar'): (None, 'कैलेंडर', 'कॅलेंडर'),
    ('', 'Analytics'): (None, 'विश्लेषण', 'विश्लेषण'),
    ('', 'Import'): (None, 'आयात', 'आयात'),
    ('', 'Cash flow'): (None, 'नकदी प्रवाह', 'रोख प्रवाह'),

    # ---- cash flow rules ----
    ('', 'Spending is running a little higher'): (None, 'खर्च थोड़ा ज़्यादा चल रहा है', 'खर्च थोडा जास्त चालला आहे'),
    ('', 'At this pace you could end up about %(pct)s above your usual %(usual)s. Money moves in waves, so this may simply be a busy stretch.'): (
        None,
        'इसी रफ़्तार से आप अपने सामान्य %(usual)s से लगभग %(pct)s ऊपर पहुँच सकते हैं। पैसे की चाल लहरों जैसी होती है, इसलिए यह बस एक व्यस्त दौर हो सकता है।',
        'याच गतीने तुम्ही तुमच्या नेहमीच्या खर्चाच्या (%(usual)s) तुलनेत सुमारे %(pct)s वर पोहोचू शकता. पैशांची चाल लाटांसारखी असते, त्यामुळे हा फक्त एक व्यस्त टप्पा असू शकतो.'),
    ('', 'If it is on purpose, you can dismiss this.'): (None, 'अगर यह जानबूझकर है, तो आप इसे हटा सकते हैं।', 'हे मुद्दाम असेल तर तुम्ही हे काढून टाकू शकता.'),
    ('', 'Spent so far'): (None, 'अब तक खर्च', 'आतापर्यंतचा खर्च'),
    ('', 'Your usual'): (None, 'आपका सामान्य', 'तुमचा नेहमीचा'),
    ('', 'At this pace'): (None, 'इसी रफ़्तार से', 'याच गतीने'),
    ('', 'See where it went'): (None, 'देखें कहाँ गया', 'कुठे गेले ते पहा'),
    ('', 'A steady run of saving'): (None, 'लगातार बचत का सिलसिला', 'सातत्याने बचतीचा क्रम'),
    ('', 'You have kept more than you spent for %(n)s month in a row. Small habits like this add up quietly.'): (
        'You have kept more than you spent for %(n)s months in a row. Small habits like this add up quietly.',
        ('आपने लगातार %(n)s महीने खर्च से ज़्यादा बचाया है। ऐसी छोटी आदतें चुपचाप जुड़ती जाती हैं।',
         'आपने लगातार %(n)s महीने खर्च से ज़्यादा बचाया है। ऐसी छोटी आदतें चुपचाप जुड़ती जाती हैं।'),
        ('%(n)s महिना सलग तुम्ही खर्चापेक्षा जास्त बचत केली आहे. अशा छोट्या सवयी शांतपणे जमा होत जातात.',
         '%(n)s महिने सलग तुम्ही खर्चापेक्षा जास्त बचत केली आहे. अशा छोट्या सवयी शांतपणे जमा होत जातात.')),
    ('', 'Kept so far'): (None, 'अब तक बचा', 'आतापर्यंत वाचवलेले'),
    ('', 'Your savings are growing'): (None, 'आपकी बचत बढ़ रही है', 'तुमची बचत वाढत आहे'),
    ('', 'You have kept about %(now)s so far, up from %(before)s in the previous cycle. That is a good sign.'): (
        None,
        'अब तक आपने लगभग %(now)s बचाया है, जो पिछले चक्र के %(before)s से ज़्यादा है। यह अच्छा संकेत है।',
        'आतापर्यंत तुम्ही सुमारे %(now)s वाचवले आहेत; मागील चक्रात ते %(before)s होते. हे चांगले लक्षण आहे.'),
    ('', 'You have kept about %(now)s so far, up from %(before)s last month. That is a good sign.'): (
        None,
        'अब तक आपने लगभग %(now)s बचाया है, जो पिछले महीने के %(before)s से ज़्यादा है। यह अच्छा संकेत है।',
        'आतापर्यंत तुम्ही सुमारे %(now)s वाचवले आहेत; मागील महिन्यात ते %(before)s होते. हे चांगले लक्षण आहे.'),
    ('', 'Kept before'): (None, 'पहले बचा', 'आधी वाचवलेले'),
    ('', 'Change'): (None, 'बदलाव', 'बदल'),
    ('', 'Your year is shaping up well'): (None, 'आपका साल अच्छा बन रहा है', 'तुमचे वर्ष चांगले आकार घेत आहे'),
    ('', "If this year's average continues, you could keep around %(amount)s by the end of the year. It is only an estimate and will move as your months do."): (
        None,
        'अगर इस साल का औसत ऐसा ही रहा, तो आप साल के अंत तक लगभग %(amount)s बचा सकते हैं। यह सिर्फ़ एक अनुमान है और आपके महीनों के साथ बदलता रहेगा।',
        'या वर्षीची सरासरी अशीच राहिली, तर तुम्ही वर्षाअखेरीस सुमारे %(amount)s वाचवू शकता. हा फक्त अंदाज आहे आणि तुमच्या महिन्यांनुसार बदलत राहील.'),
    ('', 'Kept so far this year'): (None, 'इस साल अब तक बचा', 'या वर्षी आतापर्यंत वाचवलेले'),

    # ---- expenses ----
    ('', '%(category)s is a little above usual'): (None, '%(category)s सामान्य से थोड़ा ऊपर है', '%(category)s नेहमीपेक्षा थोडे वर आहे'),
    ('', '%(category)s is about %(pct)s above your recent average so far, %(now)s against %(usual)s. One higher stretch is common, especially around festivals and travel.'): (
        None,
        '%(category)s अब तक आपके हाल के औसत से लगभग %(pct)s ऊपर है, %(usual)s के मुकाबले %(now)s। कभी-कभार ज़्यादा खर्च आम बात है, खासकर त्योहारों और यात्राओं के दौरान।',
        '%(category)s आतापर्यंत तुमच्या अलीकडील सरासरीपेक्षा सुमारे %(pct)s वर आहे: सरासरी %(usual)s, आता %(now)s. अधूनमधून जास्त खर्च होणे सामान्य आहे, विशेषतः सणवार आणि प्रवासाच्या काळात.'),
    ('', 'See the entries'): (None, 'प्रविष्टियाँ देखें', 'नोंदी पहा'),
    ('', 'This period'): (None, 'इस अवधि में', 'या कालावधीत'),
    ('', 'Recent average'): (None, 'हाल का औसत', 'अलीकडील सरासरी'),

    # ---- budgets ----
    ('', '%(category)s went past its limit'): (
        '%(n)s categories went past their limits',
        ('%(category)s की सीमा पार हो गई', '%(n)s श्रेणियों की सीमाएँ पार हो गईं'),
        ('%(category)s ची मर्यादा ओलांडली गेली', '%(n)s श्रेणींच्या मर्यादा ओलांडल्या गेल्या')),
    ('', '%(category)s is about %(over)s over its %(limit)s limit. It happens in many months, and you could raise the limit or leave it as it is.'): (
        '%(n)s categories are over their limits so far, the largest being %(category)s by about %(over)s. It happens in many months, and a limit can be adjusted if it no longer fits.',
        ('%(category)s अपनी %(limit)s की सीमा से लगभग %(over)s ऊपर है। ऐसा कई महीनों में होता है; आप चाहें तो सीमा बढ़ा सकते हैं या इसे वैसे ही रहने दें।',
         '%(n)s श्रेणियाँ अब तक अपनी सीमा से ऊपर हैं, सबसे ज़्यादा %(category)s लगभग %(over)s से। ऐसा कई महीनों में होता है, और अगर कोई सीमा अब ठीक न बैठे तो उसे बदला जा सकता है।'),
        ('%(category)s त्याच्या मर्यादेपेक्षा (%(limit)s) सुमारे %(over)s जास्त आहे. असे अनेक महिन्यांत होते; तुम्ही हवे तर मर्यादा वाढवू शकता किंवा तशीच राहू देऊ शकता.',
         '%(n)s श्रेणी आतापर्यंत त्यांच्या मर्यादेच्या वर आहेत, सर्वाधिक %(category)s सुमारे %(over)s ने. असे अनेक महिन्यांत होते, आणि एखादी मर्यादा आता योग्य वाटत नसेल तर ती बदलता येते.')),
    ('', '%(n)s other category with a limit is within it.'): (
        '%(n)s other categories with limits are within them.',
        ('%(n)s और श्रेणी अपनी सीमा के भीतर है।', '%(n)s और श्रेणियाँ अपनी सीमा के भीतर हैं।'),
        ('आणखी %(n)s श्रेणी मर्यादेत आहे.', 'आणखी %(n)s श्रेणी मर्यादेत आहेत.')),
    ('', '%(spent)s of %(limit)s'): (None, '%(limit)s में से %(spent)s', '%(limit)s पैकी %(spent)s'),
    ('', 'Review the limit'): (None, 'सीमा देखें', 'मर्यादा पहा'),
    ('', '%(category)s is close to its limit'): (
        '%(n)s categories are close to their limits',
        ('%(category)s अपनी सीमा के करीब है', '%(n)s श्रेणियाँ अपनी सीमा के करीब हैं'),
        ('%(category)s मर्यादेच्या जवळ आहे', '%(n)s श्रेणी त्यांच्या मर्यादेच्या जवळ आहेत')),
    ('', '%(category)s has used about %(percent)s of its %(limit)s limit so far. If that feels right, there is nothing to do.'): (
        '%(n)s categories have used most of their limits so far, %(category)s the most at about %(percent)s. If that feels right, there is nothing to do.',
        ('%(category)s में अब तक %(limit)s की सीमा का लगभग %(percent)s इस्तेमाल हो चुका है। अगर यह ठीक लगता है, तो कुछ करने की ज़रूरत नहीं।',
         '%(n)s श्रेणियों में अब तक सीमा का ज़्यादातर हिस्सा इस्तेमाल हो चुका है, सबसे ज़्यादा %(category)s में लगभग %(percent)s। अगर यह ठीक लगता है, तो कुछ करने की ज़रूरत नहीं।'),
        ('%(category)s मध्ये आतापर्यंत मर्यादेपैकी (%(limit)s) सुमारे %(percent)s वापरले गेले आहे. हे योग्य वाटत असेल तर काहीही करण्याची गरज नाही.',
         '%(n)s श्रेणींमध्ये आतापर्यंत बहुतेक मर्यादा वापरल्या गेल्या आहेत, सर्वाधिक %(category)s मध्ये सुमारे %(percent)s. हे योग्य वाटत असेल तर काहीही करण्याची गरज नाही.')),
    ('', '%(category)s may reach its limit early'): (
        '%(n)s categories may reach their limits early',
        ('%(category)s की सीमा जल्दी पूरी हो सकती है', '%(n)s श्रेणियों की सीमाएँ जल्दी पूरी हो सकती हैं'),
        ('%(category)s ची मर्यादा लवकर गाठली जाऊ शकते', '%(n)s श्रेणींच्या मर्यादा लवकर गाठल्या जाऊ शकतात')),
    ('', 'At this pace, %(category)s could reach its %(limit)s limit around %(date)s; %(spent)s is used so far. If that sounds right, you could ease the pace or leave it.'): (
        'At this pace, %(n)s categories could reach their limits before the period ends, the first being %(category)s around %(date)s. If that sounds right, there is nothing to do.',
        ('इसी रफ़्तार से %(category)s की %(limit)s की सीमा %(date)s के आसपास पूरी हो सकती है; अब तक %(spent)s इस्तेमाल हुआ है। अगर यह ठीक लगता है, तो आप रफ़्तार धीमी कर सकते हैं या इसे ऐसे ही रहने दें।',
         'इसी रफ़्तार से %(n)s श्रेणियों की सीमाएँ अवधि खत्म होने से पहले पूरी हो सकती हैं, सबसे पहले %(category)s की %(date)s के आसपास। अगर यह ठीक लगता है, तो कुछ करने की ज़रूरत नहीं।'),
        ('याच गतीने %(category)s ची मर्यादा (%(limit)s) सुमारे %(date)s रोजी गाठली जाऊ शकते; आतापर्यंत %(spent)s वापरले आहे. हे योग्य वाटत असेल तर तुम्ही गती थोडी कमी करू शकता किंवा तशीच राहू देऊ शकता.',
         'याच गतीने %(n)s श्रेणींच्या मर्यादा कालावधी संपण्यापूर्वी गाठल्या जाऊ शकतात, सर्वात आधी %(category)s ची सुमारे %(date)s रोजी. हे योग्य वाटत असेल तर काहीही करण्याची गरज नाही.')),

    # ---- accounts ----
    ('', 'A deposit matures soon'): (None, 'एक जमा जल्द परिपक्व होगी', 'एक ठेव लवकरच परिपक्व होणार आहे'),
    ('', '%(name)s matures on %(date)s, with about %(amount)s coming back to you. You might decide where it goes next, or leave it for now.'): (
        None,
        '%(name)s %(date)s को परिपक्व होगी, और लगभग %(amount)s आपके पास लौटेंगे। आप तय कर सकते हैं कि आगे यह कहाँ जाए, या अभी इसे वैसे ही रहने दें।',
        '%(name)s %(date)s रोजी परिपक्व होईल, आणि सुमारे %(amount)s तुमच्याकडे परत येतील. ते पुढे कुठे जावे हे तुम्ही ठरवू शकता, किंवा सध्या तसेच राहू देऊ शकता.'),
    ('', 'Matures on'): (None, 'परिपक्वता तिथि', 'परिपक्वता तारीख'),
    ('', 'View account'): (None, 'खाता देखें', 'खाते पहा'),
    ('', 'Balance'): (None, 'शेष राशि', 'शिल्लक'),

    # ---- recurring ----
    ('', 'Bills coming up this week'): (None, 'इस हफ़्ते आने वाले बिल', 'या आठवड्यातील येणारी बिले'),
    ('', '%(description)s, about %(total)s, is due on %(date)s. It is scheduled, so there is nothing to do unless plans have changed.'): (
        '%(n)s payments totalling about %(total)s are due in the next 7 days, the first on %(date)s. They are scheduled, so there is nothing to do unless plans have changed.',
        ('%(description)s, लगभग %(total)s, %(date)s को देय है। यह निर्धारित है, इसलिए योजना न बदली हो तो कुछ करने की ज़रूरत नहीं।',
         'अगले ७ दिनों में कुल लगभग %(total)s के %(n)s भुगतान देय हैं, पहला %(date)s को। ये निर्धारित हैं, इसलिए योजना न बदली हो तो कुछ करने की ज़रूरत नहीं।'),
        ('%(description)s, सुमारे %(total)s, %(date)s रोजी देय आहे. हे ठरलेले आहे, त्यामुळे योजना बदलली नसेल तर काहीही करण्याची गरज नाही.',
         'पुढील ७ दिवसांत एकूण सुमारे %(total)s असलेली %(n)s देयके येणार आहेत, पहिले %(date)s रोजी. ती ठरलेली आहेत, त्यामुळे योजना बदलली नसेल तर काहीही करण्याची गरज नाही.')),
    ('', 'View recurring payments'): (None, 'आवर्ती भुगतान देखें', 'आवर्ती देयके पहा'),

    # ---- engine, views and the panel ----
    ('', 'There is a little more to see with %(plan)s.'): (None, '%(plan)s के साथ यहाँ थोड़ा और देखने को मिलेगा।', '%(plan)s सह येथे थोडे अधिक पाहायला मिळेल.'),
    ('', 'See plans'): (None, 'प्लान देखें', 'प्लॅन पहा'),
    ('', 'Your Insights settings are saved.'): (None, 'आपकी अंतर्दृष्टि सेटिंग सहेज ली गई हैं।', 'तुमच्या अंतर्दृष्टी सेटिंग्ज जतन केल्या आहेत.'),
    ('', 'Insights'): (None, 'अंतर्दृष्टि', 'अंतर्दृष्टी'),
    ('', 'Calm observations about your own money. Descriptive only, never financial advice.'): (
        None, 'आपके अपने पैसे के बारे में शांत अवलोकन। केवल वर्णन, कभी वित्तीय सलाह नहीं।',
        'तुमच्या स्वतःच्या पैशांबद्दल शांत निरीक्षणे. फक्त वर्णन, कधीही आर्थिक सल्ला नाही.'),
    ('', 'Loading insights'): (None, 'अंतर्दृष्टि लोड हो रही हैं', 'अंतर्दृष्टी लोड होत आहेत'),
    ('', 'Good news:'): (None, 'अच्छी खबर:', 'चांगली बातमी:'),
    ('', 'Worth a look:'): (None, 'देखने लायक:', 'पाहण्यासारखे:'),
    ('', 'For your information:'): (None, 'आपकी जानकारी के लिए:', 'तुमच्या माहितीसाठी:'),
    ('', 'More options for this insight'): (None, 'इस अंतर्दृष्टि के लिए और विकल्प', 'या अंतर्दृष्टीसाठी आणखी पर्याय'),
    ('', 'More options'): (None, 'और विकल्प', 'आणखी पर्याय'),
    ('', 'Not now'): (None, 'अभी नहीं', 'आता नाही'),
    ('', 'Dismiss'): (None, 'हटाएँ', 'काढून टाका'),
    ('', 'This was helpful'): (None, 'यह उपयोगी था', 'हे उपयुक्त होते'),
    ('', 'This was not helpful'): (None, 'यह उपयोगी नहीं था', 'हे उपयुक्त नव्हते'),
    ('', 'Insights are switched off.'): (None, 'अंतर्दृष्टि बंद हैं।', 'अंतर्दृष्टी बंद आहेत.'),
    ('', 'Turn them back on'): (None, 'उन्हें फिर चालू करें', 'त्या पुन्हा सुरू करा'),
    ('', 'We are still learning your patterns. A few more weeks of entries and we will have something useful to say.'): (
        None, 'हम अभी आपके पैटर्न सीख रहे हैं। कुछ और हफ़्तों की प्रविष्टियों के बाद हमारे पास कुछ उपयोगी कहने को होगा।',
        'आम्ही अजून तुमचे नमुने शिकत आहोत. आणखी काही आठवड्यांच्या नोंदींनंतर आमच्याकडे सांगण्यासारखे काहीतरी उपयुक्त असेल.'),
    ('', 'Nothing needs a look right now. We will mention it here when something is worth your time.'): (
        None, 'अभी कुछ भी देखने की ज़रूरत नहीं है। जब कुछ आपके समय के लायक होगा, हम यहाँ बता देंगे।',
        'सध्या काहीही पाहण्याची गरज नाही. काही तुमच्या वेळेसाठी योग्य असेल तेव्हा आम्ही ते येथे सांगू.'),
    ('', 'Thanks, glad it was useful.'): (None, 'धन्यवाद, अच्छा लगा कि यह उपयोगी रहा।', 'धन्यवाद, हे उपयुक्त ठरले याचा आनंद आहे.'),
    ('', 'Thanks, noted. We will use it to say less of what is not useful.'): (
        None, 'धन्यवाद, नोट कर लिया। हम इसका उपयोग उन बातों को कम बताने में करेंगे जो उपयोगी नहीं हैं।',
        'धन्यवाद, नोंद घेतली. जे उपयुक्त नाही ते कमी सांगण्यासाठी आम्ही याचा वापर करू.'),

    # ---- Settings ----
    ('', 'Calm observations about your own money, drawn only from your own entries. They describe what we notice and never advise.'): (
        None, 'आपके अपने पैसे के बारे में शांत अवलोकन, केवल आपकी अपनी प्रविष्टियों से। वे बताते हैं कि हम क्या देखते हैं, सलाह कभी नहीं देते।',
        'तुमच्या स्वतःच्या पैशांबद्दल शांत निरीक्षणे, फक्त तुमच्या स्वतःच्या नोंदींवरून. आम्हाला काय दिसते ते ती सांगतात, सल्ला कधीही देत नाहीत.'),
    ('', 'Show insights'): (None, 'अंतर्दृष्टि दिखाएँ', 'अंतर्दृष्टी दाखवा'),
    ('', 'Turn this off to hide insights everywhere in the app.'): (
        None, 'ऐप में हर जगह अंतर्दृष्टि छिपाने के लिए इसे बंद करें।', 'अ‍ॅपमध्ये सर्वत्र अंतर्दृष्टी लपवण्यासाठी हे बंद करा.'),
    ('', 'Topics'): (None, 'विषय', 'विषय'),
    ('', 'How often should we mention things?'): (None, 'हम कितनी बार बातें बताएँ?', 'आम्ही किती वेळा गोष्टी सांगाव्यात?'),
    ('', 'Normally: at most one new insight a day in the bell'): (
        None, 'सामान्य: घंटी में दिन में अधिकतम एक नई अंतर्दृष्टि', 'सामान्य: घंटेत दिवसाला जास्तीत जास्त एक नवीन अंतर्दृष्टी'),
    ('', 'Quietly: at most one a week, and no push'): (
        None, 'शांत: हफ़्ते में अधिकतम एक, और कोई पुश नहीं', 'शांत: आठवड्यातून जास्तीत जास्त एक, आणि पुश नाही'),
    ('', 'Only on the pages, never in the bell'): (None, 'केवल पृष्ठों पर, घंटी में कभी नहीं', 'फक्त पानांवर, घंटेत कधीच नाही'),
    ('', 'Push for time-sensitive insights'): (None, 'समय-संवेदी अंतर्दृष्टि के लिए पुश', 'वेळ-संवेदनशील अंतर्दृष्टीसाठी पुश'),
    ('', 'At most two a week, and only for things like a deposit maturing or a limit being reached. Never late at night.'): (
        None, 'हफ़्ते में अधिकतम दो, और केवल जमा के परिपक्व होने या सीमा तक पहुँचने जैसी बातों के लिए। रात देर से कभी नहीं।',
        'आठवड्यातून जास्तीत जास्त दोन, आणि फक्त ठेव परिपक्व होणे किंवा मर्यादा गाठली जाणे अशा गोष्टींसाठी. रात्री उशिरा कधीच नाही.'),
    ('', 'Save insight settings'): (None, 'अंतर्दृष्टि सेटिंग सहेजें', 'अंतर्दृष्टी सेटिंग्ज जतन करा'),

    # ---- Phase B: budgets and expenses ----
    ('', '%(category)s ended above its limit in each of the last 3 budget months, by about %(over)s on average. You could raise the limit to match how you live, or keep it as it is.'): (
        None,
        'पिछले ३ बजट महीनों में हर बार %(category)s में खर्च सीमा से ऊपर रहा, औसतन लगभग %(over)s। आप अपनी जीवनशैली के हिसाब से सीमा बढ़ा सकते हैं, या इसे वैसे ही रहने दें।',
        'गेल्या ३ बजेट महिन्यांत प्रत्येक वेळी %(category)s चा खर्च मर्यादेपेक्षा जास्त राहिला, सरासरी सुमारे %(over)s ने. तुम्ही तुमच्या राहणीमानानुसार मर्यादा वाढवू शकता, किंवा तशीच राहू देऊ शकता.'),
    ('', '%(category)s finished well under its limit in each of the last 3 budget months, leaving about %(unused)s unused each time. You might lower the limit, or share the room with another category.'): (
        None,
        'पिछले ३ बजट महीनों में हर बार %(category)s में खर्च सीमा से काफ़ी नीचे रहा, हर बार लगभग %(unused)s बचा। आप सीमा घटा सकते हैं, या बची जगह किसी और श्रेणी के साथ बाँट सकते हैं।',
        'गेल्या ३ बजेट महिन्यांत प्रत्येक वेळी %(category)s चा खर्च मर्यादेपेक्षा बराच कमी राहिला, प्रत्येक वेळी सुमारे %(unused)s शिल्लक राहिले. तुम्ही मर्यादा कमी करू शकता, किंवा उरलेली जागा दुसऱ्या श्रेणीसोबत वाटून घेऊ शकता.'),
    ('', '%(category)s has room to spare'): (None, '%(category)s में गुंजाइश है', '%(category)s मध्ये मोकळीक आहे'),
    ('', '%(category)s is a bit higher'): (None, '%(category)s थोड़ा ज़्यादा है', '%(category)s थोडे जास्त आहे'),
    ('', '%(category)s is a bit lower'): (None, '%(category)s थोड़ा कम है', '%(category)s थोडे कमी आहे'),
    ('', '%(category)s is about %(pct)s higher than at this point last time, %(now)s so far against %(before)s. Spending moves in waves, so this may be nothing to act on.'): (
        None,
        '%(category)s पिछली बार इसी बिंदु की तुलना में लगभग %(pct)s ज़्यादा है: अब तक %(now)s, तब %(before)s। खर्च लहरों में चलता है, इसलिए हो सकता है इस पर कुछ करने की ज़रूरत न हो।',
        'मागच्या वेळी याच टप्प्यावर होता त्यापेक्षा %(category)s सुमारे %(pct)s जास्त आहे: आतापर्यंत %(now)s, तेव्हा %(before)s. खर्च लाटांसारखा चालतो, त्यामुळे यावर काही करण्याची गरज नसेल.'),
    ('', '%(category)s is about %(pct)s lower than at this point last time, %(now)s so far against %(before)s. Quieter stretches like this can leave room for other things.'): (
        None,
        '%(category)s पिछली बार इसी बिंदु की तुलना में लगभग %(pct)s कम है: अब तक %(now)s, तब %(before)s। ऐसे शांत दौर से दूसरी चीज़ों के लिए जगह बन सकती है।',
        'मागच्या वेळी याच टप्प्यावर होता त्यापेक्षा %(category)s सुमारे %(pct)s कमी आहे: आतापर्यंत %(now)s, तेव्हा %(before)s. असे शांत टप्पे इतर गोष्टींसाठी जागा करून देऊ शकतात.'),
    ('', '%(category)s keeps passing its limit'): (None, '%(category)s की सीमा बार-बार पार हो रही है', '%(category)s ची मर्यादा वारंवार ओलांडली जाते'),
    ('', '%(category)s stays under its limit'): (None, '%(category)s में खर्च सीमा से नीचे रहता है', '%(category)s चा खर्च मर्यादेखाली राहतो'),
    ('', '%(day)s tends to be your busiest day'): (None, '%(day)s अक्सर आपका सबसे व्यस्त दिन होता है', '%(day)s हा अनेकदा तुमचा सर्वात व्यस्त दिवस असतो'),
    ('', '%(donor)s has barely used its limit for 3 budget months, while %(needy)s has reached its own limit. You could move about %(amount)s across for now, or leave things as they are.'): (
        None,
        '%(donor)s की सीमा पिछले ३ बजट महीनों से लगभग अनछुई है, जबकि %(needy)s अपनी सीमा तक पहुँच चुका है। आप अभी के लिए लगभग %(amount)s इधर ला सकते हैं, या चीज़ें जैसी हैं वैसी रहने दें।',
        '%(donor)s ची मर्यादा गेल्या ३ बजेट महिन्यांपासून जवळजवळ वापरलेली नाही, तर %(needy)s ने स्वतःची मर्यादा गाठली आहे. तुम्ही सध्या सुमारे %(amount)s इकडे हलवू शकता, किंवा गोष्टी आहेत तशाच ठेवू शकता.'),
    ('', '%(n)s entries of %(amount)s for %(what)s were added close together on %(date)s. If both are right, you can dismiss this; otherwise you could remove one.'): (
        None,
        '%(date)s को %(what)s के लिए %(amount)s की %(n)s प्रविष्टियाँ पास-पास जोड़ी गईं। अगर सभी सही हैं, तो इसे हटा सकते हैं; नहीं तो आप एक को हटा सकते हैं।',
        '%(date)s रोजी %(what)s साठी %(amount)s च्या %(n)s नोंदी एकमेकांच्या जवळ जोडल्या गेल्या. सर्व बरोबर असतील तर हे काढून टाका; नाहीतर तुम्ही एक काढू शकता.'),
    ('', 'A larger %(category)s entry'): (None, '%(category)s की एक बड़ी प्रविष्टि', '%(category)s ची एक मोठी नोंद'),
    ('', 'A limit with room to spare'): (None, 'गुंजाइश वाली एक सीमा', 'मोकळीक असलेली एक मर्यादा'),
    ('', 'A pattern in one of your limits'): (None, 'आपकी एक सीमा में एक पैटर्न', 'तुमच्या एका मर्यादेतील एक नमुना'),
    ('', 'Amount'): (None, 'राशि', 'रक्कम'),
    ('', 'An entry larger than your usual'): (None, 'आपके सामान्य से बड़ी एक प्रविष्टि', 'तुमच्या नेहमीपेक्षा मोठी एक नोंद'),
    ('', 'An entry of %(amount)s on %(date)s is about %(times)s times your usual %(category)s amount of %(usual)s. If it was planned, you can dismiss this.'): (
        None,
        '%(date)s की %(amount)s की प्रविष्टि आपकी सामान्य %(category)s राशि %(usual)s से लगभग %(times)s गुना है। अगर यह योजना के अनुसार थी, तो आप इसे हटा सकते हैं।',
        '%(date)s रोजीची %(amount)s ची नोंद तुमच्या नेहमीच्या %(category)s रकमेच्या (%(usual)s) सुमारे %(times)s पट आहे. हे ठरल्याप्रमाणे असेल तर तुम्ही हे काढून टाकू शकता.'),
    ('', 'In each of your last 3 cycles, about %(pct)s of spending landed in the first %(days)s days. That is common when rent and bills fall early.'): (
        None,
        'आपके पिछले ३ चक्रों में से हर एक में लगभग %(pct)s खर्च पहले %(days)s दिनों में हुआ। जब किराया और बिल जल्दी आते हैं तो यह आम बात है।',
        'तुमच्या गेल्या ३ चक्रांपैकी प्रत्येकात सुमारे %(pct)s खर्च पहिल्या %(days)s दिवसांत झाला. भाडे आणि बिले लवकर येतात तेव्हा हे सामान्य आहे.'),
    ('', 'In each of your last 3 months, about %(pct)s of spending landed in the first %(days)s days. That is common when rent and bills fall early.'): (
        None,
        'आपके पिछले ३ महीनों में से हर एक में लगभग %(pct)s खर्च पहले %(days)s दिनों में हुआ। जब किराया और बिल जल्दी आते हैं तो यह आम बात है।',
        'तुमच्या गेल्या ३ महिन्यांपैकी प्रत्येकात सुमारे %(pct)s खर्च पहिल्या %(days)s दिवसांत झाला. भाडे आणि बिले लवकर येतात तेव्हा हे सामान्य आहे.'),
    ('', 'In earlier cycles when it stayed within the limit, it averaged about %(weekly)s a week.'): (
        None,
        'पिछले जिन चक्रों में यह सीमा के भीतर रहा, उनमें औसतन लगभग %(weekly)s प्रति सप्ताह खर्च हुआ।',
        'मर्यादेत राहिलेल्या आधीच्या चक्रांमध्ये आठवड्याला सरासरी सुमारे %(weekly)s खर्च झाला.'),
    ('', 'Limit of %(category)s'): (None, '%(category)s की सीमा', '%(category)s ची मर्यादा'),
    ('', 'Most spending comes early'): (None, 'ज़्यादातर खर्च शुरुआत में होता है', 'बहुतेक खर्च सुरुवातीला होतो'),
    ('', 'Other days'): (None, 'बाकी दिन', 'इतर दिवस'),
    ('', 'Over the last 8 weeks, %(day)s averaged about %(top)s, against about %(other)s on other days. Plans and outings often gather around the same day, so this is usually just your routine.'): (
        None,
        'पिछले ८ हफ़्तों में %(day)s का औसत लगभग %(top)s रहा, जबकि बाकी दिनों में लगभग %(other)s। योजनाएँ और घूमना अक्सर एक ही दिन के आसपास होते हैं, इसलिए यह आमतौर पर बस आपकी दिनचर्या है।',
        'गेल्या ८ आठवड्यांत %(day)s चे सरासरी सुमारे %(top)s होते, तर इतर दिवशी सुमारे %(other)s. योजना आणि फिरणे अनेकदा एकाच दिवसाच्या आसपास होतात, त्यामुळे हा साधारणपणे तुमचा नित्यक्रम असतो.'),
    ('', 'Review the budget'): (None, 'बजट देखें', 'बजेट पहा'),
    ('', 'Room in %(category)s'): (None, '%(category)s में गुंजाइश', '%(category)s मधील मोकळीक'),
    ('', 'Same point last time'): (None, 'पिछली बार इसी बिंदु पर', 'मागच्या वेळी याच टप्प्यावर'),
    ('', 'See the entry'): (None, 'प्रविष्टि देखें', 'नोंद पहा'),
    ('', 'Share in the first days'): (None, 'शुरुआती दिनों का हिस्सा', 'सुरुवातीच्या दिवसांचा वाटा'),
    ('', 'So far'): (None, 'अब तक', 'आतापर्यंत'),
    ('', 'This entry'): (None, 'यह प्रविष्टि', 'ही नोंद'),
    ('', 'Two entries look alike'): (None, 'दो प्रविष्टियाँ एक जैसी दिखती हैं', 'दोन नोंदी सारख्या दिसतात'),
    ('', 'Usual amount'): (None, 'सामान्य राशि', 'नेहमीची रक्कम'),
}

# Pairs whose singular and plural use different placeholders cannot be gettext plurals (msgfmt rejects them), and the
# engine only has two cases (one, or several) in any case, so each side is its own plain string.
for _key in [k for k, v in ENTRIES.items() if v[0] and k[1] in (
        '%(category)s went past its limit',
        '%(category)s is about %(over)s over its %(limit)s limit. It happens in many months, and you could raise the limit or leave it as it is.',
        '%(category)s is close to its limit',
        '%(category)s has used about %(percent)s of its %(limit)s limit so far. If that feels right, there is nothing to do.',
        '%(category)s may reach its limit early',
        'At this pace, %(category)s could reach its %(limit)s limit around %(date)s; %(spent)s is used so far. If that sounds right, you could ease the pace or leave it.',
        '%(description)s, about %(total)s, is due on %(date)s. It is scheduled, so there is nothing to do unless plans have changed.')]:
    _plural, _hi, _mr = ENTRIES.pop(_key)
    ENTRIES[_key] = (None, _hi[0], _mr[0])
    ENTRIES[(_key[0], _plural)] = (None, _hi[1], _mr[1])

LOCALES = {'hi': 1, 'mr': 2}      # index into each ENTRIES value


def quote(text: str) -> str:
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


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


def parse_blocks(text: str):
    """Blocks separated by blank lines; each with its (context, msgid) and whether it has any translation."""
    blocks = []
    for chunk in re.split(r'\n{2,}', text.strip('\n')):
        fields, current = {}, None
        for line in chunk.splitlines():
            m = re.match(r'(msgctxt|msgid_plural|msgid|msgstr(?:\[\d\])?)\s+"(.*)"$', line)
            if m:
                current = m.group(1)
                fields[current] = fields.get(current, '') + m.group(2)
            elif line.startswith('"') and current:
                fields[current] += line[1:-1]
        fuzzy = '#, fuzzy' in chunk or any(line.startswith('#, ') and 'fuzzy' in line for line in chunk.splitlines())
        translated = any(v for k, v in fields.items() if k.startswith('msgstr'))
        blocks.append({'text': chunk, 'key': (fields.get('msgctxt', ''), fields.get('msgid', '')),
                       'translated': translated and not fuzzy})
    return blocks


def unquote(raw: str) -> str:
    return raw.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')


def merge(lang: str) -> tuple[int, int]:
    path = ROOT / 'locale' / lang / 'LC_MESSAGES' / 'django.po'
    blocks = parse_blocks(path.read_text(encoding='utf-8'))
    index = {unquote_key: b for b in blocks for unquote_key in [(unquote(b['key'][0]), unquote(b['key'][1]))]}
    added = filled = 0
    out = [b['text'] for b in blocks]
    for (ctx, msgid), value in ENTRIES.items():
        plural, values = value[0], value[LOCALES[lang]]
        existing = index.get((ctx, msgid))
        entry = render(ctx, msgid, plural, values)
        if existing is None:
            out.append('#: expenses/insights\n' + entry)
            added += 1
        elif not existing['translated']:
            at = out.index(existing['text'])
            comments = [line for line in existing['text'].splitlines() if line.startswith('#') and 'fuzzy' not in line and not line.startswith('#|')]
            out[at] = '\n'.join(comments + [entry])
            filled += 1
    path.write_text('\n\n'.join(out) + '\n', encoding='utf-8')
    return added, filled


def missing() -> list[str]:
    import os

    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'finance_tracker.settings')
    django.setup()
    from expenses.insights import msgids
    return sorted({m for _p, c, m, _pl in msgids.all_strings() if (c, m) not in ENTRIES})


if __name__ == '__main__':
    if '--check' in sys.argv:
        left = missing()
        print('\n'.join(left) if left else 'every insight string has an entry')
        sys.exit(1 if left else 0)
    for code in LOCALES:
        added, filled = merge(code)
        print(f'{code}: {added} added, {filled} filled')
