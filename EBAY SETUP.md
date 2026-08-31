# eBay publishing — setup checklist

This is prep work for a future **"Publish to eBay"** feature in Card Desk. It is not built yet — this file is just the account/credential setup so it's ready to wire up whenever you want to continue. Everything below has to be done by you directly on eBay's site; it involves your own eBay account and agreeing to eBay's developer terms, which isn't something that can be done on your behalf.

eBay is the realistic target platform: TCGPlayer's seller API is invite-only for approved sellers, and Mercari / Facebook Marketplace / Whatnot don't offer a public listing-creation API for individual sellers.

## 1. Join the eBay Developers Program

Go to [developer.ebay.com](https://developer.ebay.com) and sign in with your regular eBay account (create one first if you don't already sell on eBay). Joining the Developers Program is free. Exact menu wording drifts over time — look for the general shape of these steps rather than an exact match.

## 2. Create a Sandbox keyset first

In the developer portal, create an application **keyset**. You get two separate environments:

- **Sandbox** — a fake eBay with test data. No real money, no real listings. **Start here.**
- **Production** — real listings, real money. Only move to this after Sandbox testing works end to end.

Each keyset gives you three values:

- **App ID** (Client ID)
- **Dev ID**
- **Cert ID** (Client Secret)

Note the Sandbox versions of all three somewhere safe.

## 3. Turn on Business Policies on your actual eBay seller account

This is separate from the developer portal — it's on your normal eBay seller account. Under **Seller Hub → Account**, turn on **Business Policies** if it isn't already, and make sure you have at least one of each:

- **Payment policy**
- **Return policy**
- **Shipping policy**

The listing API requires referencing these by ID — it cannot create a listing without them already existing.

## 4. Hand off to Card Desk

Once you have Sandbox App ID / Dev ID / Cert ID:

- Paste them in chat and ask to continue the eBay integration, **or**
- Set them as environment variables yourself and just say they're set — same pattern as the existing `OPENAI_API_KEY` handling (kept in memory / read from the environment, never written to the database).

One more step happens at that point, not before: eBay's listing flow requires a one-time login consent (you log into eBay in a browser, approve the app, it returns a token). Card Desk will handle catching that automatically when this gets built — you won't need to manage raw tokens by hand.

## What happens after that

- The integration gets built and tested against **Sandbox** first — fake listings, safe to experiment with freely.
- Only after that's confirmed working does it make sense to switch to **Production** keys.
- Even in Production, publishing stays a **"Publish to eBay" button you click per card or batch, with a confirmation step** — never a silent background process. That matches how every other selling action in Card Desk already works (you approve every listing), and real-money marketplace actions should always have a human confirming them.
