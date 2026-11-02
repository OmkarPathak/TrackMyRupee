# Family sharing announcement (draft)

Paste into Django admin > Announcements. Body is Markdown.

| Field | Value |
|---|---|
| Title | Family sharing is here: see your household's money together |
| Audience | All users (it is a Pro feature, so the post also nudges Free users to upgrade) |
| CTA text | Set up your family |
| CTA link | /expenses/family/  (confirm the real path with `reverse('family-home')`) |
| Show modal | Yes |
| Send email / push | Your call. Email is the better fit for a feature this size. |
| Status | Draft until FAMILY_ENABLED is on in production |

## Body

Most households don't have one person who holds all the money. There's a salary account here, a credit card there,
a parent's insurance, a home loan in two names. **Family sharing** lets you see all of it together, without handing
over your whole account.

### How it works

- **Each person keeps their own TrackMyRupee account.** Nothing is merged.
- **Invite up to 5 people** by email, or send yourself a link to share.
- **You choose what to share, one account at a time.** Joining a family shares nothing until you say so.
- **Show amounts, or list only.** Pick whether the family sees a balance or just that the account exists.
- **See the combined picture:** your family's net worth, and which cards, policies, loans and accounts the family holds and who holds them.

### What your family never sees

Transactions, categories, notes, receipts, holdings inside an account, account or policy numbers, and anything you
haven't shared. They won't even know it exists.

### Also in this release

- **Emergency sheet:** note which accounts and policies exist and where to look, so your family can find them if
  something happens to you. Never account numbers or passwords.
- **You stay in control.** Stop sharing an account, or leave the family, any time. The person who started the family
  can also remove a member or delete the family entirely.

Family sharing needs a **Pro plan** for everyone in the family.
