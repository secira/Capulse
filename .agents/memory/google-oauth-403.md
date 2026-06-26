---
name: Google OAuth generic 403
description: Diagnosing Google sign-in failures — distinguishing a Google Console config problem from an app code bug.
---

# Google OAuth "403. That's an error. You do not have access to this page."

This generic Google 403 page (broken-robot illustration) is **not** `redirect_uri_mismatch`
(which instead says "Access blocked: this app's request is invalid"). The generic
"you do not have access" 403 almost always means the **OAuth consent screen User Type
is set to "Internal"** — only users inside the same Google Workspace org can sign in,
and any personal/external Gmail gets this page.

**Fix (user-side, in Google Cloud Console — not code):**
- Consent screen → set **User Type = External**, then **Publish App** (status → In production).
- Only `email`/`profile`/`openid` scopes are used, so no Google verification review is needed; publishing is instant.
- Alternatively keep Testing mode and add the tester's Gmail under **Test users**.

**How to tell where the failure is:** the app logs `Redirecting to Google — redirect_uri=...`
and the matching `/google_login` 302. If `/google_login/callback` is **never** hit afterward,
Google rejected the request on its side → it's a Console config issue, not app code.

**Why this matters:** we burned several iterations assuming a redirect_uri mismatch /
code bug. The redirect URI was correct the whole time; the blocker was the consent
screen audience setting.
