---
title: Quick Add from Your Phone
description: Open the new-expense form already filled in from a link, paste a copied bank message, share text to TrackMyRupee on Android, or build a Shortcut on iPhone or an automation on Android.
keywords: TrackMyRupee quick add, iPhone shortcut, Siri expense, Android share, Tasker, MacroDroid, log expense faster, paste UPI message
---

# Quick Add from Your Phone

Get to a filled-in expense form in a tap or two: from a link, from a message you copied, from your phone's Share menu, or from a Shortcut or automation you build yourself.

!!! info "You still tap Add"
    Every route on this page opens the **Add a new expense** form with the line already typed and read. You check the details and tap **Add**, exactly as when you type it yourself. Nothing is saved on its own.

---

## 1. What works where

| | Android | iPhone |
|---|---|---|
| A link that opens the form filled in ([section 2](#2-a-link-that-fills-in-the-form)) | Yes | Yes |
| Paste button ([section 3](#3-paste-from-your-clipboard)) | Yes, in Chrome | Yes, in Safari (iOS asks you to allow Paste) |
| Share menu: **Share → TrackMyRupee** ([section 4](#4-android)) | Yes, once installed | No: iPhone does not offer this, use a Shortcut instead |
| Press-and-hold the app icon: **Add expense**, **Add income** | Yes, once installed | No |
| Your own shortcut or automation ([sections 4 and 5](#5-iphone-shortcuts)) | Yes, with an automation app | Yes, with the Shortcuts app |

You need to be **signed in** on the phone's browser for any of this.

---

## 2. A link that fills in the form

Add `?text=` and what you want typed to the address of the new-expense page:

```
https://trackmyrupee.com/expenses/add/?text=swiggy%20320
```

The form opens with *swiggy 320* in the box, already read: amount 320, description Swiggy, and the category and account it would normally pick.

* **Spaces are written `%20`**, and a few characters (`&`, `#`, `+`) must be encoded too. Shortcut and automation apps can do this for you, as shown below.
* The usual words work: `uber 150 upi yesterday`, `rent 1.5k cash 3 oct`. See [Type It Like You Would Text a Friend](index.md#2-type-it-like-you-would-text-a-friend).
* Only the first **300 characters** are used, and line breaks become spaces.
* Once the form has opened, the text is **removed from the address bar**, so refreshing or going back does not bring it back.
* If you are signed out, you are asked to sign in and then land on the same form.

Make a link for something you log every day, such as `?text=chai%2020`, and keep it as a bookmark or a Home Screen icon for one-tap logging.

---

## 3. Paste from your clipboard

Next to the microphone there is a **clipboard button**. Copy a bank or UPI message, open **Add a new expense**, tap the button, and the message is pasted and read for you. For example, `Rs.150 paid to UBER via UPI` becomes amount 150, description Uber, payment method UPI.

* The clipboard is read **only when you tap** the button. On iPhone, iOS asks you to allow the paste.
* If the clipboard is empty, or your browser refuses, a short message says so and you can press and hold in the box and choose **Paste** instead.
* The button is hidden in browsers that cannot read the clipboard (Firefox, for example).

---

## 4. Android

### Install the app

Open `trackmyrupee.com` in **Chrome**, then choose the menu (⋮) → **Install app** (or **Add to Home screen**). It then opens like an app, without the browser bars.

### The Share menu

In any app, **Share** a piece of text and choose **TrackMyRupee**. The form opens with that text read. Try it with a message from your bank app, a note, or a chat. If TrackMyRupee does not appear straight away, give Chrome a day to refresh the installed app, or install it again.

### Press and hold the icon

Press and hold the TrackMyRupee icon to see **Add expense** and **Add income**.

### Your own automation

Any app that can open a link will do. Three examples:

* **A button on your Home Screen:** in Chrome, open the link from [section 2](#2-a-link-that-fills-in-the-form), then **⋮ → Add to Home screen**. One tap opens the form filled in.
* **Tasker:** make a task with a **Browse URL** action. Set the URL to `https://trackmyrupee.com/expenses/add/?text=%text`, where `%text` holds your text. Run **Variable Convert → URL Encode** on it first so spaces and symbols are safe.
* **MacroDroid and similar apps:** use the **Open Website** (or **Launch URL**) action with the same link, and URL-encode the text if the app offers it.

Links to `trackmyrupee.com` usually open in the installed app. If yours opens in Chrome instead, that works the same.

---

## 5. iPhone Shortcuts

iPhone does not list TrackMyRupee in the Share menu, but the **Shortcuts** app can do the same job, and more.

### A shortcut you can speak or type into

1. Open **Shortcuts** and tap **+** to make a new shortcut.
2. Add **Ask for Input**. Choose **Text** and set the question to *What did you spend on?* To speak instead, add **Dictate Text**.
3. Add an action that **URL-encodes** the text (search the actions for *encode*), and give it the input from step 2.
4. Add **Open URLs** and set it to `https://trackmyrupee.com/expenses/add/?text=` followed by the encoded text.
5. Name it, for example **Add expense**.

### A shortcut that appears in the Share menu

This is handy for a bank or UPI message you want to log.

1. Make a new shortcut, open its settings (ⓘ), and turn on **Show in Share Sheet**. Allow it to receive **Text**.
2. Add the URL-encode action on **Shortcut Input**, then **Open URLs** with the same link as above.
3. In Messages, press and hold a bank message, tap **Share**, and choose your shortcut.

### Ways to run a shortcut

* **Home Screen icon:** in the shortcut's settings, **Add to Home Screen**.
* **Siri:** say "Hey Siri" followed by the shortcut's name.
* **Action Button** (iPhone 15 Pro and later): Settings → Action Button → **Shortcut**.
* **Back Tap:** Settings → Accessibility → Touch → Back Tap → choose your shortcut.
* **Lock Screen or widget:** add the **Shortcuts** widget and pick your shortcut.

On iPhone the link opens in **Safari**, so be signed in there.

---

## 6. Good to know

* **Nothing is saved until you tap Add.** For fully hands-free logging we would need a different feature, which is not part of TrackMyRupee today.
* **Privacy.** The text you send rides in the link, so it can appear in the server's access logs. Keep it to what you would type into the box anyway, and leave out account numbers.
* **Analytics.** We count that someone arrived by link, share or paste. We never record the text or the amount.
* **Language.** The form reads English, Hindi and Marathi the way it does when you type.

---

## FAQ

**Can I make it save automatically?** Not today. Every route opens the form so you can check the details first.

**The Share menu does not list TrackMyRupee on Android.** It must be installed from Chrome (**Install app**), not just bookmarked. After installing, wait a day, or install it again.

**The link opens a sign-in page.** You are signed out in that browser. Sign in once and the link works afterwards.

**The text I shared is cut off.** Only the first 300 characters are used. Share just the part with the amount and the merchant.

---

## Related Links
- [Adding Expenses](index.md)
- [Importing a Statement](importing.md)
- [Mobile App (iOS)](../20-mobile-app/index.md)
