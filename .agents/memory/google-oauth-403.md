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

**CRITICAL — redirect_uri must be dynamic, not static:**
Always derive `redirect_uri` in `login()` and `redirect_url` in `callback()` from
`request.base_url` at runtime:

```python
# login()
dynamic_redirect = request.base_url.replace("http://", "https://") + "/callback"

# callback()
dynamic_redirect = request.base_url.replace("http://", "https://")
```

Never use a module-level constant (e.g. built from `REPLIT_DEV_DOMAIN`) for the actual
OAuth calls. A static constant always sends one fixed domain (e.g. `.worf.replit.dev`)
even when the user is on `www.capulse.tech` — Google sees a redirect_uri_mismatch
and returns its generic 403 page. The dynamic approach auto-matches whichever registered
domain the user is actually using.

**How to tell where the failure is:** the app logs `Redirecting to Google — redirect_uri=...`
and the matching `/google_login` 302. If `/google_login/callback` is **never** hit afterward,
Google rejected the request on its side → check that the redirect_uri in the log matches
one of the URIs registered in Google Console exactly.

**Why this matters:** the generic Google 403 "you do not have access to this page" is shown
for both consent-screen audience issues AND redirect_uri_mismatch. Don't assume it's only
a Console config problem — check the actual redirect_uri being sent first.
