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

    # ---- Phase C: recurring payments, loans, cards and accounts ----
    ('', '%(card)s bills on %(date)s, with about %(amount)s outstanding, around %(pct)s of its limit. A payment before then may lower what shows on the statement.'): (
        None,
        '%(card)s का बिल %(date)s को बनेगा, और लगभग %(amount)s बकाया है, जो इसकी सीमा का लगभग %(pct)s है। उससे पहले भुगतान करने पर स्टेटमेंट में दिखने वाली रकम कम हो सकती है।',
        '%(card)s चे बिल %(date)s रोजी तयार होईल, आणि सुमारे %(amount)s थकबाकी आहे, जी मर्यादेच्या सुमारे %(pct)s आहे. त्यापूर्वी भरणा केल्यास स्टेटमेंटमध्ये दिसणारी रक्कम कमी होऊ शकते.'),
    ('', '%(card)s bills on %(date)s, with about %(amount)s outstanding. A payment before then may lower what shows on the statement.'): (
        None,
        '%(card)s का बिल %(date)s को बनेगा, और लगभग %(amount)s बकाया है। उससे पहले भुगतान करने पर स्टेटमेंट में दिखने वाली रकम कम हो सकती है।',
        '%(card)s चे बिल %(date)s रोजी तयार होईल, आणि सुमारे %(amount)s थकबाकी आहे. त्यापूर्वी भरणा केल्यास स्टेटमेंटमध्ये दिसणारी रक्कम कमी होऊ शकते.'),
    ('', '%(card)s is using about %(pct)s of its %(limit)s limit. That is common after a big purchase, and it eases as the card is paid down.'): (
        None,
        '%(card)s में सीमा (%(limit)s) का लगभग %(pct)s इस्तेमाल हो रहा है। बड़ी खरीदारी के बाद ऐसा आम है, और कार्ड चुकाने के साथ यह घटता जाता है।',
        '%(card)s मध्ये मर्यादेच्या (%(limit)s) सुमारे %(pct)s वापर होत आहे. मोठ्या खरेदीनंतर हे सामान्य असते, आणि कार्डची परतफेड होत जाते तसे ते कमी होते.'),
    ('', '%(high)s is at %(high_rate)s, about %(gap)s points above %(low)s at %(low_rate)s. This is a comparison only, for whenever you next look at your loans.'): (
        None,
        '%(high)s की दर %(high_rate)s है, जबकि %(low)s की %(low_rate)s, यानी लगभग %(gap)s अंक का फ़र्क़। यह सिर्फ़ एक तुलना है, जब आप अगली बार अपने ऋण देखें तब के लिए।',
        '%(high)s चा दर %(high_rate)s आहे, तर %(low)s चा %(low_rate)s आहे, म्हणजे सुमारे %(gap)s अंकांचा फरक. ही फक्त तुलना आहे, तुम्ही पुढच्या वेळी तुमची कर्जे पाहाल तेव्हासाठी.'),
    ('', '%(loan)s has about %(n)s month left on its schedule, with %(amount)s still to repay. A steady finish is in sight.'): (
        '%(loan)s has about %(n)s months left on its schedule, with %(amount)s still to repay. A steady finish is in sight.',
        ('%(loan)s की अनुसूची में लगभग %(n)s महीना बचा है, और %(amount)s चुकाना बाकी है। स्थिर समापन नज़र के सामने है।',
         '%(loan)s की अनुसूची में लगभग %(n)s महीने बचे हैं, और %(amount)s चुकाना बाकी है। स्थिर समापन नज़र के सामने है।'),
        ('%(loan)s च्या वेळापत्रकात सुमारे %(n)s महिना उरला आहे, आणि %(amount)s अजून फेडायचे आहेत. स्थिर समाप्ती दृष्टिपथात आहे.',
         '%(loan)s च्या वेळापत्रकात सुमारे %(n)s महिने उरले आहेत, आणि %(amount)s अजून फेडायचे आहेत. स्थिर समाप्ती दृष्टिपथात आहे.')),
    ('', '%(loan)s is %(pct)s repaid'): (None, '%(loan)s का %(pct)s चुकता हो चुका है', '%(loan)s चे %(pct)s फेडले आहे'),
    ('', '%(loan)s is in its final year'): (None, '%(loan)s अपने अंतिम वर्ष में है', '%(loan)s शेवटच्या वर्षात आहे'),
    ('', '%(loan)s rate changes soon'): (None, '%(loan)s की दर जल्द बदलेगी', '%(loan)s चा दर लवकरच बदलणार आहे'),
    ('', '%(n)s months of spending'): (None, '%(n)s महीनों का खर्च', '%(n)s महिन्यांचा खर्च'),
    ('', '%(name)s came down from %(before)s to %(after)s on the latest entry. If you expected this, you can dismiss it.'): (
        None,
        'ताज़ा प्रविष्टि में %(name)s की रकम %(before)s से घटकर %(after)s हो गई। अगर आपको इसकी उम्मीद थी, तो इसे हटा सकते हैं।',
        'ताज्या नोंदीत %(name)s ची रक्कम %(before)s वरून %(after)s झाली. हे अपेक्षित असेल तर तुम्ही हे काढून टाकू शकता.'),
    ('', '%(name)s went up from %(before)s to %(after)s on the latest entry. If you expected this, you can dismiss it.'): (
        None,
        'ताज़ा प्रविष्टि में %(name)s की रकम %(before)s से बढ़कर %(after)s हो गई। अगर आपको इसकी उम्मीद थी, तो इसे हटा सकते हैं।',
        'ताज्या नोंदीत %(name)s ची रक्कम %(before)s वरून %(after)s झाली. हे अपेक्षित असेल तर तुम्ही हे काढून टाकू शकता.'),
    ('', '%(name)s was last matched to a statement %(days)s days ago. A quick check when you have a moment keeps the balance trustworthy.'): (
        None,
        '%(name)s का मिलान आख़िरी बार %(days)s दिन पहले स्टेटमेंट से हुआ था। जब समय हो, एक छोटी जाँच से शेष राशि भरोसेमंद बनी रहती है।',
        '%(name)s चा स्टेटमेंटशी ताळमेळ शेवटी %(days)s दिवसांपूर्वी झाला होता. वेळ मिळेल तेव्हा एक छोटी तपासणी शिल्लक विश्वासार्ह ठेवते.'),
    ('', '%(what)s looks like it repeats'): (None, '%(what)s बार-बार आता दिखता है', '%(what)s वारंवार येताना दिसते'),
    ('', 'A balance could use a check'): (None, 'एक शेष राशि की जाँच हो सकती है', 'एका शिल्लकेची तपासणी उपयोगी ठरेल'),
    ('', 'A card billing date is near'): (None, 'कार्ड की बिलिंग तिथि पास है', 'कार्डची बिलिंग तारीख जवळ आली आहे'),
    ('', 'A closer look at the days before payday'): (None, 'वेतन से पहले के दिनों पर एक नज़दीकी नज़र', 'पगारापूर्वीच्या दिवसांवर एक जवळून नजर'),
    ('', 'A look at the cash you hold'): (None, 'आपके पास मौजूद नकदी पर एक नज़र', 'तुमच्याकडील रोखीवर एक नजर'),
    ('', 'A look at your fixed costs'): (None, 'आपके तय खर्चों पर एक नज़र', 'तुमच्या ठरलेल्या खर्चांवर एक नजर'),
    ('', 'A rate change you recorded for %(loan)s takes effect on %(date)s, from %(old)s to %(new)s. Your payments will follow the new rate.'): (
        None,
        'आपने %(loan)s के लिए जो दर बदलाव दर्ज किया है, वह %(date)s से लागू होगा, %(old)s से %(new)s। आपके भुगतान नई दर के अनुसार होंगे।',
        'तुम्ही %(loan)s साठी नोंदवलेला दर बदल %(date)s रोजी लागू होईल, %(old)s वरून %(new)s. तुमची देयके नवीन दराप्रमाणे असतील.'),
    ('', '%(name)s now costs a bit more'): (None, '%(name)s अब थोड़ा महंगा है', '%(name)s आता थोडे महाग आहे'),
    ('', '%(name)s now costs a bit less'): (None, '%(name)s अब थोड़ा सस्ता है', '%(name)s आता थोडे स्वस्त आहे'),
    ('', 'A recurring amount changed'): (None, 'एक आवर्ती राशि बदली', 'एक आवर्ती रक्कम बदलली'),
    ('', 'A tight stretch before payday'): (None, 'वेतन से पहले थोड़ी तंगी का दौर', 'पगारापूर्वी थोडी तंगीचा टप्पा'),
    ('', 'About %(idle)s sits in your bank accounts, roughly %(months)s months of your spending, against your %(target)s-month target. If that feels right, there is nothing to do.'): (
        None,
        'आपके बैंक खातों में लगभग %(idle)s रखे हैं, जो आपके खर्च के लगभग %(months)s महीनों के बराबर है, जबकि आपका लक्ष्य %(target)s महीने का है। अगर यह ठीक लगता है, तो कुछ करने की ज़रूरत नहीं।',
        'तुमच्या बँक खात्यांत सुमारे %(idle)s आहेत, जे तुमच्या खर्चाच्या सुमारे %(months)s महिन्यांइतके आहेत, तर तुमचे लक्ष्य %(target)s महिन्यांचे आहे. हे योग्य वाटत असेल तर काहीही करण्याची गरज नाही.'),
    ('', 'At this pace, your bank balance could dip to about %(amount)s around %(day)s, before your salary on %(payday)s. If it helps, you could move a bill or keep a small buffer.'): (
        None,
        'इसी रफ़्तार से आपके बैंक का शेष %(day)s के आसपास घटकर लगभग %(amount)s रह सकता है, आपके वेतन (%(payday)s) से पहले। मदद मिले तो आप कोई बिल आगे-पीछे कर सकते हैं या थोड़ा बफ़र रख सकते हैं।',
        'याच गतीने तुमची बँक शिल्लक सुमारे %(day)s रोजी घटून सुमारे %(amount)s राहू शकते, तुमच्या पगारापूर्वी (%(payday)s). मदत होत असेल तर तुम्ही एखादे बिल पुढे-मागे करू शकता किंवा थोडा बफर ठेवू शकता.'),
    ('', 'At this pace, your bank balance could run a little short around %(day)s, before your salary on %(payday)s. If it helps, you could move a bill or keep a small buffer.'): (
        None,
        'इसी रफ़्तार से %(day)s के आसपास आपके बैंक में थोड़ी कमी पड़ सकती है, आपके वेतन (%(payday)s) से पहले। मदद मिले तो आप कोई बिल आगे-पीछे कर सकते हैं या थोड़ा बफ़र रख सकते हैं।',
        'याच गतीने सुमारे %(day)s रोजी तुमच्या बँकेत थोडी कमतरता येऊ शकते, तुमच्या पगारापूर्वी (%(payday)s). मदत होत असेल तर तुम्ही एखादे बिल पुढे-मागे करू शकता किंवा थोडा बफर ठेवू शकता.'),
    ('', 'Before'): (None, 'पहले', 'आधी'),
    ('', 'Billing date'): (None, 'बिलिंग तिथि', 'बिलिंग तारीख'),
    ('', 'Card use is a little higher'): (None, 'कार्ड का उपयोग थोड़ा ज़्यादा है', 'कार्डचा वापर थोडा जास्त आहे'),
    ('', 'Cash and bank'): (None, 'नकद और बैंक', 'रोख आणि बँक'),
    ('', 'Cash and bank today'): (None, 'आज नकद और बैंक', 'आज रोख आणि बँक'),
    ('', 'Cash set aside for emergencies'): (None, 'आपात स्थिति के लिए रखी नकदी', 'आणीबाणीसाठी ठेवलेली रोख'),
    ('', 'Check the balance'): (None, 'शेष राशि जाँचें', 'शिल्लक तपासा'),
    ('', 'Each month'): (None, 'हर महीने', 'दर महिन्याला'),
    ('', 'Everyday spending a day'): (None, 'रोज़ का सामान्य खर्च', 'रोजचा सामान्य खर्च'),
    ('', 'Takes effect on'): (None, 'लागू होने की तिथि', 'लागू होण्याची तारीख'),
    ('', "How much cash in the bank feels comfortable to you. Insights compare your cash with this, never with anyone else's."): (
        None,
        'बैंक में कितनी नकदी आपको आरामदायक लगती है। अंतर्दृष्टि आपकी नकदी की तुलना इसी से करती हैं, किसी और से कभी नहीं।',
        'बँकेत किती रोख तुम्हाला सोयीची वाटते. अंतर्दृष्टी तुमच्या रोखीची तुलना याच्याशी करतात, इतर कोणाशीही कधीच नाही.'),
    ('', 'Interest'): (None, 'ब्याज', 'व्याज'),
    ('', 'Interest on your loans this year'): (None, 'इस साल आपके ऋणों पर ब्याज', 'या वर्षी तुमच्या कर्जांवरील व्याज'),
    ('', 'Interest saved'): (None, 'बचा ब्याज', 'वाचलेले व्याज'),
    ('', 'Last matched'): (None, 'आख़िरी मिलान', 'शेवटचा ताळमेळ'),
    ('', 'Limit'): (None, 'सीमा', 'मर्यादा'),
    ('', 'Make it recurring'): (None, 'इसे आवर्ती बनाएँ', 'हे आवर्ती करा'),
    ('', 'Now'): (None, 'अब', 'आता'),
    ('', 'One extra EMI a year on %(loan)s could close it about %(n)s months earlier and save around %(amount)s in interest. It is only an estimate, and entirely optional.'): (
        None,
        'हर साल एक अतिरिक्त EMI देने से %(loan)s लगभग %(n)s महीने पहले बंद हो सकता है और लगभग %(amount)s का ब्याज बच सकता है। यह सिर्फ़ एक अनुमान है, और पूरी तरह वैकल्पिक।',
        'दरवर्षी एक जादा EMI भरल्यास %(loan)s सुमारे %(n)s महिने आधी बंद होऊ शकते आणि सुमारे %(amount)s व्याज वाचू शकते. हा फक्त अंदाज आहे, आणि पूर्णपणे ऐच्छिक.'),
    ('', 'One extra EMI a year on %(loan)s could close it about %(years)s years earlier and save around %(amount)s in interest. It is only an estimate, and entirely optional.'): (
        None,
        'हर साल एक अतिरिक्त EMI देने से %(loan)s लगभग %(years)s साल पहले बंद हो सकता है और लगभग %(amount)s का ब्याज बच सकता है। यह सिर्फ़ एक अनुमान है, और पूरी तरह वैकल्पिक।',
        'दरवर्षी एक जादा EMI भरल्यास %(loan)s सुमारे %(years)s वर्षे आधी बंद होऊ शकते आणि सुमारे %(amount)s व्याज वाचू शकते. हा फक्त अंदाज आहे, आणि पूर्णपणे ऐच्छिक.'),
    ('', 'Outstanding'): (None, 'बकाया', 'थकबाकी'),
    ('', 'Payments'): (None, 'भुगतान', 'देयके'),
    ('', 'Principal'): (None, 'मूलधन', 'मुद्दल'),
    ('', 'Recurring payments come to about %(monthly)s a month, around %(share)s of your income. Knowing this number can make the rest of the month easier to plan.'): (
        None,
        'आवर्ती भुगतान महीने में लगभग %(monthly)s बैठते हैं, जो आपकी आय का लगभग %(share)s है। यह संख्या जानने से बाकी महीने की योजना बनाना आसान हो सकता है।',
        'आवर्ती देयके महिन्याला सुमारे %(monthly)s होतात, जे तुमच्या उत्पन्नाच्या सुमारे %(share)s आहे. हा आकडा माहीत असल्याने उर्वरित महिन्याचे नियोजन सोपे होऊ शकते.'),
    ('', 'Scheduled until payday'): (None, 'वेतन तक निर्धारित', 'पगारापर्यंत ठरलेले'),
    ('', 'See the full picture'): (None, 'पूरी तस्वीर देखें', 'संपूर्ण चित्र पहा'),
    ('', 'Share of income'): (None, 'आय का हिस्सा', 'उत्पन्नाचा वाटा'),
    ('', 'So far this financial year, about %(interest)s of your loan payments went to interest and %(principal)s to principal.'): (
        None,
        'इस वित्त वर्ष में अब तक आपके ऋण भुगतानों में से लगभग %(interest)s ब्याज में और %(principal)s मूलधन में गए।',
        'या आर्थिक वर्षात आतापर्यंत तुमच्या कर्ज देयकांपैकी सुमारे %(interest)s व्याजात आणि %(principal)s मुद्दलात गेले.'),
    ('', 'So far this year, about %(interest)s of your loan payments went to interest and %(principal)s to principal.'): (
        None,
        'इस साल अब तक आपके ऋण भुगतानों में से लगभग %(interest)s ब्याज में और %(principal)s मूलधन में गए।',
        'या वर्षी आतापर्यंत तुमच्या कर्ज देयकांपैकी सुमारे %(interest)s व्याजात आणि %(principal)s मुद्दलात गेले.'),
    ('', 'Still to repay'): (None, 'चुकाना बाकी', 'फेडायचे बाकी'),
    ('', 'The same %(amount)s entry for %(what)s appeared %(n)s times in the last 3 months. You could make it recurring, or leave it as it is.'): (
        None,
        'पिछले ३ महीनों में %(what)s के लिए %(amount)s की वही प्रविष्टि %(n)s बार आई। आप इसे आवर्ती बना सकते हैं, या वैसे ही रहने दें।',
        'गेल्या ३ महिन्यांत %(what)s साठी %(amount)s ची तीच नोंद %(n)s वेळा आली. तुम्ही ती आवर्ती करू शकता, किंवा तशीच राहू देऊ शकता.'),
    ('', 'Times'): (None, 'बार', 'वेळा'),
    ('', 'Used'): (None, 'इस्तेमाल', 'वापरलेले'),
    ('', 'Usual monthly spending'): (None, 'सामान्य मासिक खर्च', 'नेहमीचा मासिक खर्च'),
    ('', 'View card'): (None, 'कार्ड देखें', 'कार्ड पहा'),
    ('', 'View loan'): (None, 'ऋण देखें', 'कर्ज पहा'),
    ('', 'View the calendar'): (None, 'कैलेंडर देखें', 'कॅलेंडर पहा'),
    ('', 'View your loans'): (None, 'अपने ऋण देखें', 'तुमची कर्जे पहा'),
    ('', 'What one extra EMI could do'): (None, 'एक अतिरिक्त EMI से क्या हो सकता है', 'एक जादा EMI मुळे काय होऊ शकते'),
    ('', 'You have repaid about %(pct)s of %(loan)s, with about %(amount)s still to go. Steady payments got you here.'): (
        None,
        'आप %(loan)s का लगभग %(pct)s चुका चुके हैं, और लगभग %(amount)s बाकी है। नियमित भुगतान ने आपको यहाँ तक पहुँचाया।',
        'तुम्ही %(loan)s चे सुमारे %(pct)s फेडले आहे, आणि सुमारे %(amount)s अजून बाकी आहे. नियमित देयकांनी तुम्हाला येथवर आणले.'),
    ('', 'Your EMI'): (None, 'आपकी EMI', 'तुमचा EMI'),
    ('', 'Your cushion target'): (None, 'आपका सुरक्षा-निधि लक्ष्य', 'तुमचे सुरक्षितता-निधी लक्ष्य'),
    ('', 'Your fixed costs each month'): (None, 'आपके हर महीने के तय खर्च', 'तुमचे दर महिन्याचे ठरलेले खर्च'),
    ('', 'Your loan rates differ'): (None, 'आपके ऋणों की दरें अलग हैं', 'तुमच्या कर्जांचे दर वेगळे आहेत'),
    # ---- Phase D: income ----
    ('', 'How steady your income is'): (None, 'आपकी आय कितनी स्थिर है', 'तुमचे उत्पन्न किती स्थिर आहे'),
    ('', 'About %(share)s of your income came from salary; the rest varied between %(low)s and %(high)s a cycle.'): (
        None,
        'आपकी आय का लगभग %(share)s वेतन से आया; बाकी हर चक्र में %(low)s से %(high)s के बीच रहा।',
        'तुमच्या उत्पन्नाचा सुमारे %(share)s भाग पगारातून आला; उर्वरित प्रत्येक चक्रात %(low)s ते %(high)s दरम्यान राहिला.'),
    ('', 'About %(share)s of your income came from salary; the rest came to around %(avg)s a cycle.'): (
        None,
        'आपकी आय का लगभग %(share)s वेतन से आया; बाकी हर चक्र में करीब %(avg)s रहा।',
        'तुमच्या उत्पन्नाचा सुमारे %(share)s भाग पगारातून आला; उर्वरित प्रत्येक चक्रात सुमारे %(avg)s होता.'),
    ('', 'Salary'): (None, 'वेतन', 'पगार'),
    ('', 'Other income'): (None, 'अन्य आय', 'इतर उत्पन्न'),
    ('', 'Salary has not arrived yet'): (None, 'वेतन अभी नहीं आया', 'पगार अजून आलेला नाही'),
    ('', 'It usually lands around day %(day)s of the month and none is recorded this cycle. If it is on its way, there is nothing to do.'): (
        None,
        'यह आम तौर पर महीने की %(day)s तारीख के आसपास आता है और इस चक्र में कोई दर्ज नहीं है। अगर वह रास्ते में है, तो कुछ करने की ज़रूरत नहीं।',
        'तो साधारणपणे महिन्याच्या %(day)s तारखेच्या आसपास येतो आणि या चक्रात कोणताही नोंदवलेला नाही. तो वाटेत असेल तर काही करण्याची गरज नाही.'),
    ('', 'Add income'): (None, 'आय जोड़ें', 'उत्पन्न जोडा'),
    ('', 'No income recorded for a while'): (None, 'कुछ समय से कोई आय दर्ज नहीं', 'काही काळापासून कोणतेही उत्पन्न नोंदवलेले नाही'),
    ('', 'There has been no income entry for %(days)s days. You can add income whenever you are ready.'): (
        None,
        '%(days)s दिनों से कोई आय प्रविष्टि नहीं है। जब आप तैयार हों, आय जोड़ सकते हैं।',
        '%(days)s दिवसांपासून कोणतीही उत्पन्न नोंद नाही. तुम्ही तयार असाल तेव्हा उत्पन्न जोडू शकता.'),
    ('', 'Last income'): (None, 'पिछली आय', 'मागील उत्पन्न'),

    # ---- Phase D: goals ----
    ('', '%(name)s is on track for %(month)s'): (None, '%(name)s %(month)s तक सही राह पर', '%(name)s %(month)s पर्यंत पूर्ण होण्याच्या मार्गावर आहे'),
    ('', '%(name)s may finish a little later'): (None, '%(name)s शायद कुछ देर से पूरा होगा', '%(name)s थोड्या उशिरा पूर्ण होऊ शकते'),
    ('', 'At the pace of the last 3 months, it reaches %(target)s around %(when)s.'): (
        None,
        'पिछले 3 महीनों की रफ़्तार से यह %(when)s के आसपास %(target)s तक पहुँचेगा।',
        'गेल्या 3 महिन्यांच्या गतीने ते %(when)s च्या आसपास %(target)s पर्यंत पोहोचेल.'),
    ('', 'At the pace of the last 3 months, it reaches %(target)s around %(when)s, a little after your %(date)s date. A small top-up could close the gap.'): (
        None,
        'पिछले 3 महीनों की रफ़्तार से यह %(when)s के आसपास %(target)s तक पहुँचेगा, यानी आपकी %(date)s की तारीख़ से थोड़ा बाद। थोड़ी सी अतिरिक्त राशि इस अंतर को भर सकती है।',
        'गेल्या 3 महिन्यांच्या गतीने ते %(when)s च्या आसपास %(target)s पर्यंत पोहोचेल, म्हणजे तुमच्या %(date)s च्या तारखेनंतर थोडे. थोडी जादा रक्कम हा फरक भरून काढू शकते.'),
    ('', 'Saved so far'): (None, 'अब तक बचाया', 'आतापर्यंत वाचवलेले'),
    ('', 'Each month'): (None, 'हर महीने', 'दर महिन्याला'),
    ('', 'Open the goal'): (None, 'लक्ष्य खोलें', 'उद्दिष्ट उघडा'),
    ('', 'A small change for a goal'): (None, 'एक लक्ष्य के लिए एक छोटा बदलाव', 'एका उद्दिष्टासाठी एक छोटासा बदल'),
    ('', 'A small change for %(name)s'): (None, '%(name)s के लिए एक छोटा बदलाव', '%(name)s साठी एक छोटासा बदल'),
    ('', 'Adding %(amount)s a month would bring the finish forward by about %(n)s months. It is one option; the current pace is fine too.'): (
        None,
        'हर महीने %(amount)s जोड़ने से समाप्ति लगभग %(n)s महीने पहले हो सकती है। यह बस एक विकल्प है; मौजूदा रफ़्तार भी ठीक है।',
        'दर महिन्याला %(amount)s जोडल्यास पूर्णता सुमारे %(n)s महिने आधी होऊ शकते. हा फक्त एक पर्याय आहे; सध्याची गतीही चांगली आहे.'),
    ('', 'Extra each month'): (None, 'हर महीने अतिरिक्त', 'दर महिन्याला जादा'),
    ('', 'A goal has been quiet lately'): (None, 'एक लक्ष्य में हाल में कोई हलचल नहीं', 'एका उद्दिष्टात अलीकडे हालचाल नाही'),
    ('', '%(name)s has been quiet lately'): (None, '%(name)s में हाल में कोई हलचल नहीं', '%(name)s मध्ये अलीकडे हालचाल नाही'),
    ('', 'There has been no contribution for %(days)s days; it stands at %(pct)s of %(target)s. A small top-up or a pause are both fine.'): (
        None,
        '%(days)s दिनों से कोई योगदान नहीं हुआ; यह %(target)s का %(pct)s है। थोड़ी राशि जोड़ना या रुकना, दोनों ठीक हैं।',
        '%(days)s दिवसांपासून कोणतेही योगदान झालेले नाही; ते %(target)s च्या %(pct)s वर आहे. थोडी रक्कम जोडणे किंवा थांबणे, दोन्ही चालेल.'),
    ('', '%(name)s is complete'): (None, '%(name)s पूरा हो गया', '%(name)s पूर्ण झाले'),
    ('', '%(name)s is nearly there'): (None, '%(name)s लगभग पूरा होने को है', '%(name)s जवळजवळ पूर्ण होत आले'),
    ('', '%(name)s is halfway'): (None, '%(name)s आधा पूरा हुआ', '%(name)s अर्धे पूर्ण झाले'),
    ('', 'You have reached all of %(target)s. Steady contributions got you here.'): (
        None, 'आप पूरे %(target)s तक पहुँच गए हैं। नियमित योगदान ने आपको यहाँ तक पहुँचाया।',
        'तुम्ही संपूर्ण %(target)s पर्यंत पोहोचला आहात. नियमित योगदानाने तुम्हाला येथवर आणले.'),
    ('', 'You have reached %(pct)s of %(target)s. Steady contributions got you here.'): (
        None, 'आप %(target)s का %(pct)s तक पहुँच गए हैं। नियमित योगदान ने आपको यहाँ तक पहुँचाया।',
        'तुम्ही %(target)s च्या %(pct)s पर्यंत पोहोचला आहात. नियमित योगदानाने तुम्हाला येथवर आणले.'),

    # ---- Phase D: transfers and investing, holdings ----
    ('', 'Share of income you invest'): (None, 'निवेश में गया आय का हिस्सा', 'उत्पन्नाचा तुम्ही गुंतवत असलेला हिस्सा'),
    ('', 'About %(share)s of income went into investments this cycle, against your usual %(usual)s.'): (
        None,
        'इस चक्र में आय का लगभग %(share)s निवेश में गया, जबकि आपका सामान्य %(usual)s रहता है।',
        'या चक्रात उत्पन्नाचा सुमारे %(share)s गुंतवणुकीत गेला, तुमचा नेहमीचा %(usual)s असतो.'),
    ('', 'Invested'): (None, 'निवेश किया', 'गुंतवले'),
    ('', 'A SIP has not been recorded'): (None, 'एक SIP दर्ज नहीं हुई', 'एक SIP नोंदवली गेलेली नाही'),
    ('', 'Your %(amount)s SIP (%(name)s) was due on %(day)s and is not recorded yet. If it went through, you can add it.'): (
        None,
        'आपकी %(amount)s की SIP (%(name)s) %(day)s को देय थी और अभी दर्ज नहीं है। अगर वह हो गई है, तो आप उसे जोड़ सकते हैं।',
        'तुमची %(amount)s ची SIP (%(name)s) %(day)s रोजी देय होती आणि अजून नोंदवलेली नाही. ती झाली असल्यास तुम्ही ती जोडू शकता.'),
    ('', 'Due on'): (None, 'देय तिथि', 'देय तारीख'),
    ('', 'Add the transfer'): (None, 'ट्रांसफ़र जोड़ें', 'हस्तांतरण जोडा'),
    ('', 'Where your invested money sits'): (None, 'आपका निवेशित पैसा कहाँ है', 'तुमचे गुंतवलेले पैसे कुठे आहेत'),
    ('', '%(kind)s are about %(kind_share)s of your invested money and %(name)s about %(share)s. This is just a picture of your mix.'): (
        None,
        '%(kind)s आपके निवेशित पैसे का लगभग %(kind_share)s हैं और %(name)s लगभग %(share)s। यह बस आपके मिश्रण की एक तस्वीर है।',
        '%(kind)s तुमच्या गुंतवलेल्या पैशांच्या सुमारे %(kind_share)s आहेत आणि %(name)s सुमारे %(share)s. हे फक्त तुमच्या मिश्रणाचे चित्र आहे.'),
    ('', '%(kind)s are about %(kind_share)s of your invested money. This is just a picture of your mix.'): (
        None,
        '%(kind)s आपके निवेशित पैसे का लगभग %(kind_share)s हैं। यह बस आपके मिश्रण की एक तस्वीर है।',
        '%(kind)s तुमच्या गुंतवलेल्या पैशांच्या सुमारे %(kind_share)s आहेत. हे फक्त तुमच्या मिश्रणाचे चित्र आहे.'),
    ('', '%(name)s is about %(share)s of your invested money. This is just a picture of your mix.'): (
        None,
        '%(name)s आपके निवेशित पैसे का लगभग %(share)s है। यह बस आपके मिश्रण की एक तस्वीर है।',
        '%(name)s तुमच्या गुंतवलेल्या पैशांच्या सुमारे %(share)s आहे. हे फक्त तुमच्या मिश्रणाचे चित्र आहे.'),
    ('', 'Invested value'): (None, 'निवेशित मूल्य', 'गुंतवणूक मूल्य'),
    ('', 'Mutual funds'): (None, 'म्यूचुअल फ़ंड', 'म्युच्युअल फंड'),
    ('', 'Stocks'): (None, 'शेयर', 'शेअर्स'),
    ('', 'Recurring deposits'): (None, 'आवर्ती जमा', 'आवर्ती ठेवी'),
    ('', 'Other holdings'): (None, 'अन्य होल्डिंग्स', 'इतर होल्डिंग्ज'),

    # ---- Phase D: capital events, calendar, net worth ----
    ('', 'A one-off cost shaped this cycle'): (None, 'एकमुश्त खर्च का इस चक्र पर असर', 'एका एकवेळच्या खर्चाने हे चक्र घडवले'),
    ('', 'Your savings rate is %(rate)s, or %(with)s with %(name)s counted. One-off costs are normal and are kept out of averages by default.'): (
        None,
        'आपकी बचत दर %(rate)s है, या %(name)s को गिनने पर %(with)s। एकमुश्त खर्च सामान्य हैं और डिफ़ॉल्ट रूप से औसत से बाहर रखे जाते हैं।',
        'तुमचा बचत दर %(rate)s आहे, किंवा %(name)s धरल्यास %(with)s. एकवेळचे खर्च सामान्य असतात आणि डीफॉल्टनुसार सरासरीतून वगळले जातात.'),
    ('', 'One-off costs'): (None, 'एकमुश्त खर्च', 'एकवेळचे खर्च'),
    ('', 'A busy week of payments ahead'): (None, 'आगे भुगतानों से भरा एक व्यस्त सप्ताह', 'पुढे देयकांचा एक व्यस्त आठवडा'),
    ('', 'About %(amount)s is scheduled over the next 7 days, around %(times)s times a typical week for you.'): (
        None,
        'अगले 7 दिनों में लगभग %(amount)s निर्धारित है, जो आपके सामान्य सप्ताह का करीब %(times)s गुना है।',
        'पुढील 7 दिवसांत सुमारे %(amount)s ठरलेले आहे, जे तुमच्या सामान्य आठवड्याच्या सुमारे %(times)s पट आहे.'),
    ('', 'Scheduled'): (None, 'निर्धारित', 'ठरलेले'),
    ('', 'A typical week'): (None, 'एक सामान्य सप्ताह', 'एक सामान्य आठवडा'),
    ('', 'Open the calendar'): (None, 'कैलेंडर खोलें', 'कॅलेंडर उघडा'),
    ('', 'What moved your net worth'): (None, 'आपकी कुल संपत्ति में क्या बदला', 'तुमच्या निव्वळ संपत्तीत काय बदलले'),
    ('', 'It is up about %(change)s this cycle: roughly %(kept)s from what you kept and %(rest)s from changes in value.'): (
        None,
        'इस चक्र में यह लगभग %(change)s बढ़ी है: करीब %(kept)s आपकी बचत से और %(rest)s मूल्य में बदलाव से।',
        'या चक्रात ती सुमारे %(change)s वाढली आहे: सुमारे %(kept)s तुमच्या बचतीतून आणि %(rest)s मूल्यातील बदलांमुळे.'),
    ('', 'It is up about %(change)s this cycle: you kept %(kept)s, and changes in value took back %(rest)s.'): (
        None,
        'इस चक्र में यह लगभग %(change)s बढ़ी है: आपने %(kept)s बचाए, और मूल्य में बदलाव ने %(rest)s वापस ले लिए।',
        'या चक्रात ती सुमारे %(change)s वाढली आहे: तुम्ही %(kept)s वाचवले, आणि मूल्यातील बदलांनी %(rest)s परत घेतले.'),
    ('', 'It is down about %(change)s this cycle: you kept %(kept)s, and changes in value took away %(rest)s.'): (
        None,
        'इस चक्र में यह लगभग %(change)s घटी है: आपने %(kept)s बचाए, और मूल्य में बदलाव ने %(rest)s घटा दिए।',
        'या चक्रात ती सुमारे %(change)s कमी झाली आहे: तुम्ही %(kept)s वाचवले, आणि मूल्यातील बदलांनी %(rest)s कमी केले.'),
    ('', 'Net worth'): (None, 'कुल संपत्ति', 'निव्वळ संपत्ती'),
    ('', 'Kept this cycle'): (None, 'इस चक्र में बचाया', 'या चक्रात वाचवलेले'),
    # ---- Phase E: the Analytics sentences (several already had these words in the catalogue; those are left alone) ----
    ('', 'What you kept this period'): (None, 'इस अवधि में आपने क्या बचाया', 'या कालावधीत तुम्ही काय वाचवले'),
    ('', 'No income in this period'): (None, 'इस अवधि में कोई आय नहीं', 'या कालावधीत उत्पन्न नाही'),
    ('', 'No income was recorded in this period.'): (None, 'इस अवधि में कोई आय दर्ज नहीं हुई।', 'या कालावधीत कोणतेही उत्पन्न नोंदवले गेले नाही.'),
    ('', 'You kept %(saved)s of %(income)s (%(rate)s).'): (None, 'आपने %(income)s में से %(saved)s बचाए (%(rate)s)।', 'तुम्ही %(income)s पैकी %(saved)s वाचवले (%(rate)s).'),
    ('', 'That is ahead of the comparison period (%(rate)s).'): (None, 'यह तुलना वाली अवधि (%(rate)s) से बेहतर है।', 'हे तुलनेच्या कालावधीपेक्षा (%(rate)s) चांगले आहे.'),
    ('', 'That is behind the comparison period (%(rate)s).'): (None, 'यह तुलना वाली अवधि (%(rate)s) से पीछे है।', 'हे तुलनेच्या कालावधीपेक्षा (%(rate)s) मागे आहे.'),
    ('', 'Your biggest cost'): (None, 'आपका सबसे बड़ा खर्च', 'तुमचा सर्वात मोठा खर्च'),
    ('', '%(name)s is your biggest cost: %(amount)s, %(pct)s of your income.'): (
        None, '%(name)s आपका सबसे बड़ा खर्च है: %(amount)s, यानी आपकी आय का %(pct)s।',
        '%(name)s हा तुमचा सर्वात मोठा खर्च आहे: %(amount)s, म्हणजे तुमच्या उत्पन्नाच्या %(pct)s.'),
    ('', 'Spending beyond income'): (None, 'आय से ज़्यादा खर्च', 'उत्पन्नापेक्षा जास्त खर्च'),
    ('', 'You spent %(amount)s more than you earned in this period.'): (
        None, 'इस अवधि में आपने कमाई से %(amount)s ज़्यादा खर्च किया।', 'या कालावधीत तुम्ही कमाईपेक्षा %(amount)s जास्त खर्च केला.'),
    ('', 'Your costs and investing came to %(amount)s more than your income, paid from savings you already had.'): (
        None, 'आपके खर्च और निवेश आपकी आय से %(amount)s ज़्यादा रहे, जो पहले से जमा बचत से चुकाए गए।',
        'तुमचा खर्च आणि गुंतवणूक तुमच्या उत्पन्नापेक्षा %(amount)s जास्त झाली, जी आधीच्या बचतीतून भरली गेली.'),
    ('', 'Where your income went'): (None, 'आपकी आय कहाँ गई', 'तुमचे उत्पन्न कुठे गेले'),
    ('', 'You invested %(invested)s and kept %(kept)s of your income as cash.'): (
        None, 'आपने आय का %(invested)s निवेश किया और %(kept)s नकद में बचाया।',
        'तुम्ही उत्पन्नाच्या %(invested)s गुंतवणूक केली आणि %(kept)s रोख म्हणून वाचवले.'),
    ('', 'The biggest mover'): (None, 'सबसे ज़्यादा बदलाव', 'सर्वात जास्त बदल'),
    ('', '%(name)s went up by %(amount)s compared with %(then)s.'): (
        None, '%(then)s की तुलना में %(name)s पर %(amount)s ज़्यादा खर्च हुआ।', '%(then)s च्या तुलनेत %(name)s वर %(amount)s जास्त खर्च झाला.'),
    ('', '%(name)s went down by %(amount)s compared with %(then)s.'): (
        None, '%(then)s की तुलना में %(name)s पर %(amount)s कम खर्च हुआ।', '%(then)s च्या तुलनेत %(name)s वर %(amount)s कमी खर्च झाला.'),
    ('', '%(name)s keeps rising'): (None, '%(name)s बढ़ता जा रहा है', '%(name)s वाढतच आहे'),
    ('', '%(name)s has risen three months in a row, by about %(pct)s a month.'): (
        None, '%(name)s लगातार तीन महीने बढ़ा है, औसतन लगभग %(pct)s प्रति माह।',
        '%(name)s सलग तीन महिने वाढले आहे, सरासरी सुमारे %(pct)s दरमहा.'),
    ('', 'Over this period it is up about %(change)s: roughly %(kept)s from what you kept and %(rest)s from changes in value.'): (
        None,
        'इस अवधि में यह लगभग %(change)s बढ़ी है: करीब %(kept)s आपकी बचत से और %(rest)s मूल्य में बदलाव से।',
        'या कालावधीत ती सुमारे %(change)s वाढली आहे: सुमारे %(kept)s तुमच्या बचतीतून आणि %(rest)s मूल्यातील बदलांमुळे.'),
    ('', 'Over this period it is up about %(change)s: you kept %(kept)s, and changes in value took back %(rest)s.'): (
        None,
        'इस अवधि में यह लगभग %(change)s बढ़ी है: आपने %(kept)s बचाए, और मूल्य में बदलाव ने %(rest)s वापस ले लिए।',
        'या कालावधीत ती सुमारे %(change)s वाढली आहे: तुम्ही %(kept)s वाचवले, आणि मूल्यातील बदलांनी %(rest)s परत घेतले.'),
    ('', 'Over this period it is down about %(change)s: you kept %(kept)s, and changes in value took away %(rest)s.'): (
        None,
        'इस अवधि में यह लगभग %(change)s घटी है: आपने %(kept)s बचाए, और मूल्य में बदलाव ने %(rest)s घटा दिए।',
        'या कालावधीत ती सुमारे %(change)s कमी झाली आहे: तुम्ही %(kept)s वाचवले, आणि मूल्यातील बदलांनी %(rest)s कमी केले.'),
    # ---- Phase E: the email digest, its Settings control, the unsubscribe page, the Year in Review card ----
    ('', 'What we noticed'): (None, 'हमने क्या देखा', 'आम्ही काय पाहिले'),
    ('', 'Your insights'): (None, 'आपकी अंतर्दृष्टि', 'तुमच्या अंतर्दृष्टी'),
    ('', 'A few things we noticed'): (None, 'कुछ बातें जो हमने देखीं', 'काही गोष्टी ज्या आम्ही पाहिल्या'),
    ('', 'Calm observations about your own money. Nothing here is advice.'): (
        None, 'आपके अपने पैसे के बारे में शांत अवलोकन। यहाँ कुछ भी सलाह नहीं है।', 'तुमच्या स्वतःच्या पैशांबद्दल शांत निरीक्षणे. येथे काहीही सल्ला नाही.'),
    ('', 'Your TrackMyRupee insights, %(date)s'): (None, 'आपकी TrackMyRupee अंतर्दृष्टि, %(date)s', 'तुमच्या TrackMyRupee अंतर्दृष्टी, %(date)s'),
    ('', 'Open TrackMyRupee'): (None, 'TrackMyRupee खोलें', 'TrackMyRupee उघडा'),
    ('', 'Unsubscribe'): (None, 'सदस्यता रद्द करें', 'सदस्यता रद्द करा'),
    ('', 'Change how often'): (None, 'कितनी बार भेजें, बदलें', 'किती वेळा पाठवायचे ते बदला'),
    ('', 'You get this email once a week, because you chose that in Settings.'): (
        None, 'आपको यह ईमेल हफ़्ते में एक बार मिलता है, क्योंकि आपने सेटिंग्स में यही चुना है।',
        'तुम्हाला हा ईमेल आठवड्यातून एकदा मिळतो, कारण तुम्ही सेटिंग्जमध्ये तेच निवडले आहे.'),
    ('', 'You get this email every day when there is something new, because you chose that in Settings.'): (
        None, 'जब भी कुछ नया हो, आपको यह ईमेल हर दिन मिलता है, क्योंकि आपने सेटिंग्स में यही चुना है।',
        'काही नवीन असेल तेव्हा तुम्हाला हा ईमेल दररोज मिळतो, कारण तुम्ही सेटिंग्जमध्ये तेच निवडले आहे.'),
    ('', 'Email digest'): (None, 'ईमेल डाइजेस्ट', 'ईमेल डायजेस्ट'),
    ('', 'Weekly: one email a week with the most useful insights'): (
        None, 'साप्ताहिक: हफ़्ते में एक ईमेल, सबसे काम की अंतर्दृष्टि के साथ', 'साप्ताहिक: आठवड्यातून एक ईमेल, सर्वात उपयुक्त अंतर्दृष्टींसह'),
    ('', 'Daily: one email a day, when there is something new'): (
        None, 'दैनिक: हर दिन एक ईमेल, जब कुछ नया हो', 'दैनिक: दररोज एक ईमेल, काही नवीन असेल तेव्हा'),
    ('', 'Never'): (None, 'कभी नहीं', 'कधीही नाही'),
    ('', 'Only sent when there is something worth saying, and every email has an unsubscribe link.'): (
        None, 'केवल तब भेजा जाता है जब कुछ कहने लायक हो, और हर ईमेल में सदस्यता रद्द करने का लिंक होता है।',
        'फक्त काही सांगण्यासारखे असेल तेव्हाच पाठवला जातो, आणि प्रत्येक ईमेलमध्ये सदस्यता रद्द करण्याचा दुवा असतो.'),
    ('', 'The email digest is part of the Pro plan.'): (None, 'ईमेल डाइजेस्ट प्रो प्लान का हिस्सा है।', 'ईमेल डायजेस्ट प्रो प्लॅनचा भाग आहे.'),
    ('', 'Insight emails'): (None, 'अंतर्दृष्टि ईमेल', 'अंतर्दृष्टी ईमेल'),
    ('', 'Stop the weekly insight digest?'): (None, 'साप्ताहिक अंतर्दृष्टि डाइजेस्ट बंद करें?', 'साप्ताहिक अंतर्दृष्टी डायजेस्ट बंद करायचा?'),
    ('', 'You will not get the insight digest any more. You can switch it back on in Settings whenever you like.'): (
        None, 'अब आपको अंतर्दृष्टि डाइजेस्ट नहीं मिलेगा। आप जब चाहें सेटिंग्स में इसे फिर चालू कर सकते हैं।',
        'आता तुम्हाला अंतर्दृष्टी डायजेस्ट मिळणार नाही. तुम्हाला हवे तेव्हा तुम्ही सेटिंग्जमध्ये तो पुन्हा सुरू करू शकता.'),
    ('', 'This unsubscribe link is not valid.'): (None, 'यह सदस्यता रद्द करने का लिंक मान्य नहीं है।', 'हा सदस्यता रद्द करण्याचा दुवा वैध नाही.'),
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
