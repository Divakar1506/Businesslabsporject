"""Catalogue of common directory-website features ("topics").

A topic gives a requirement a stable ID (REQ-<KEY>) so the same need can be matched across meetings,
a Knowledge Brain question for the feasibility check, a testable acceptance criterion and an
estimate profile. Sentences that match no topic still become requirements (topic "other"),
matched across meetings by word overlap instead.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    pattern: re.Pattern
    kb_question: str
    acceptance: str
    profile: str = "feature"          # feature | integration | content | design


def _t(key, title, pattern, kb, acc, profile="feature"):
    return Topic(key, title, re.compile(pattern, re.I), kb, acc, profile)


# Order matters: more specific topics first (e.g. "price range" before generic "filter").
TOPICS: list[Topic] = [
    _t("PRICE_FILTER", "Price range filter", r"price range", "How do I edit the min and max value of price sliders in search?",
       "Customers can filter search results by a minimum and maximum price."),
    _t("VERIFIED_FILTER", "Verified listings filter", r"\bverified\b", "How do I verify a listing?",
       "Customers can choose to see only verified listings."),
    _t("MAP_VIEW", "Map view of search results", r"\bmap\b", "How do I show search results on a Google map?",
       "Search results can be shown on a map, with each pin opening the listing."),
    _t("AUTOSUGGEST", "Search suggestions while typing", r"suggestions?\b|autosuggest|as (they|you) type|autocomplete",
       "How do I edit keyword search autosuggest settings for member searches?",
       "After typing 2 letters, matching categories are suggested within 1 second."),
    _t("AI_SEARCH", "AI-assisted search", r"\bAI\b|chatgpt|artificial intelligence",
       "Does the platform have AI search or a chatbot that recommends members?",
       "Defined after the client confirms what the AI must do (open question)."),
    _t("SORTING", "Featured members first in results", r"\bsort|order of results|show first|featured",
       "How do I change search results sorting options?",
       "Featured members appear above non-featured members in every search result list."),
    _t("FILTERS", "Search filters", r"\bfilter", "How do I add search filters by category, rating and distance?",
       "Customers can filter search results by each agreed attribute, and the filters can be combined."),
    _t("MOBILE_APP", "Mobile app", r"mobile app|app store", "How do I turn the directory into a mobile app?",
       "An installable app is published (if confirmed)."),
    _t("MOBILE_SEARCH", "Mobile search experience", r"mobile|phones?\b|one thumb",
       "How do I make the search page work well on mobile?",
       "On a 375px-wide phone, search and filters are usable without zooming and results load in under 3 seconds."),
    _t("SEARCH_RELEVANCE", "Keyword search relevance", r"\bsearch\b.*(right|relevan|great|wrong|find)|types? \"",
       "How does keyword search work and how can I improve search results relevance?",
       "For the 20 test searches supplied by the client, the expected trade appears in the top 3 results."),
    _t("GUEST_CHECKOUT", "One-page and guest checkout", r"checkout|check out",
       "Can customers check out as a guest on a one-page checkout?",
       "A buyer can complete a purchase on one page without creating an account."),
    _t("SMS", "SMS notifications", r"\bSMS\b|text message", "Can the site send SMS notifications?",
       "Makers receive an SMS within 1 minute of a new order (if confirmed).", "integration"),
    _t("ORDER_EMAILS", "Order notification emails", r"notification|confirmation email|an email|order emails?", "How do I manage and edit email templates for orders?",
       "Seller and buyer each receive an order email within 1 minute of purchase."),
    _t("STRIPE", "Stripe card payments", r"stripe", "Which payment gateways are supported? Is Stripe supported?",
       "Card payments through Stripe succeed in test and live mode.", "integration"),
    _t("PAYPAL", "PayPal payments", r"paypal", "How do I set up PayPal as the payment gateway?",
       "Buyers can pay with PayPal and the order is marked paid.", "integration"),
    _t("COUPONS", "Coupon codes at checkout", r"coupon|discount code", "How do I create easy coupon codes?",
       "A valid coupon code reduces the order total; an expired code is rejected."),
    _t("INVOICES", "Invoices for orders", r"invoice", "How do I create an invoice for a member?",
       "Every completed order produces a downloadable invoice."),
    _t("SHIPPING", "Live shipping rates (ShipStation)", r"shipstation|shipping", "Does the platform integrate with ShipStation for shipping rates?",
       "Live ShipStation rates show at checkout for a test address.", "integration"),
    _t("CALENDAR_SYNC", "Calendar sync for bookings", r"calendar", "Can bookings sync with Google Calendar?",
       "Bookings appear in the provider's Google Calendar within 5 minutes.", "integration"),
    _t("BOOKING", "Online booking", r"\bbook(ing)?\b|time slot|appointment", "Can customers book appointments online with a member?",
       "A customer can pick a free time slot on a profile and both sides get a confirmation."),
    _t("REVIEW_PHOTOS", "Photos in reviews", r"review", "Can reviews include photos?",
       "A customer can attach up to 5 photos to a review and they show on the profile."),
    _t("SOCIAL_LOGIN", "Social login", r"facebook|google login|sign in with|login", "How do I enable the Facebook Login add-on?",
       "Users can sign up and sign in with Facebook."),
    _t("NEWSLETTER", "Email newsletter", r"newsletter", "How do I create and send an email newsletter?",
       "The admin can create and send a monthly newsletter to opted-in customers."),
    _t("HOMEPAGE_REDESIGN", "Homepage redesign", r"homepage", "How do I edit the homepage design?",
       "New homepage layout approved by the client.", "design"),
    _t("BRANDING", "Logo and branding", r"\blogo\b|branding", "How do I upload a logo image?",
       "New brand assets applied site-wide.", "design"),
    _t("CONTENT_WRITING", "SEO content writing", r"blog posts?|content writing|write .* (posts|articles)",
       "How do I add a website blog article?", "Agreed number of articles published per month.", "content"),
]

BY_KEY = {t.key: t for t in TOPICS}


def match_topic(text: str) -> Topic | None:
    for t in TOPICS:
        if t.pattern.search(text):
            return t
    return None
