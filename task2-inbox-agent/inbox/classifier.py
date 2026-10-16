"""Email classification at $0: keyword rules + multinomial Naive Bayes over spaCy tokens.

- Rules are high-precision signals ("refund", "how much would it cost"). Only the highest-priority
  rule that fires counts, and it adds a fixed bonus to that category's score.
- Naive Bayes is trained on the DEV emails only (the test emails stay unseen).
- Final category = argmax(NB log-probability + rule bonus); confidence = its softmax probability.
Urgency and risk come from separate keyword lists plus the category.
"""
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache

from .models import Category

# Order = priority when several fire: money first, then commitments, then tone, then type of help.
RULES: list[tuple[Category, re.Pattern]] = [(c, re.compile(p, re.I)) for c, p in (
    (Category.REFUND_CANCEL, r"\brefund|money back|\bcancel(l?ing|l?ation)?\b.{0,40}\b(plan|subscription|account|membership|renewal)"
                             r"|chargeback|stop (billing|charging)|close my account|\bdowngrade my\b"),
    (Category.QUOTE, r"how much (would|will|does|is)|\bquote\b|\bestimate\b|pricing for|price for|\bbudget\b|\bcost to\b"),
    (Category.FOLLOW_UP, r"did you (get|receive|see)|any update|following up|follow(ing)?[- ]up on|haven'?t heard|still waiting"
                         r"|checking in|my (last|previous|earlier) (email|message|ticket)|\bticket #?\d+"),
    (Category.COMPLAINT, r"unacceptable|disappointed|frustrat|terrible|\bworst\b|angry|furious|ridiculous|not happy|unhappy"
                         r"|waste of|lost (customers|money|sales|clients)|fed up|appalled"),
    (Category.PROJECT_REQUEST, r"(can|could) you (build|add|create|develop|custom|integrat|design)|new feature|custom "
                               r"(feature|integration|development|module)|we('d| would) like (you|to have|a new)|redesign"),
    (Category.TROUBLESHOOTING, r"not working|stopped working|broken|\berror\b|doesn'?t (work|show|load|send)|can'?t|cannot"
                               r"|won'?t|isn'?t|failing|\bfailed\b|\bbug\b|issue with|problem with|\bblank\b|not (showing|loading|receiving|sending)"
                               r"|doesn'?t seem|is there a fix|running out of|almost full|\bbroke\b"),
    (Category.HOW_TO, r"\bhow (do|can|to|would)\b|\bwhere (do|can|is)\b|is (it|there) (possible|a way)|\bcan i\b|steps to"
                      r"|\bis it possible\b|\bwhich\b.{0,60}\b(support|use|can|do)\b|\bwhat (size|settings)\b"),
)]
# Chosen by leave-one-out on the DEV emails only (test emails never used): rule weight e^2 -> 10/36,
# e^1000 -> 30/36, rules-decide -> 31/36. With 36 training emails Naive Bayes is only a fallback.
RULE_BONUS = math.log(1e4)

URGENT = re.compile(r"urgent|asap|immediately|right away|\btoday\b|\bdown\b|outage|losing (money|customers|sales|clients)"
                    r"|critical|emergency|as soon as possible", re.I)
NOT_URGENT = re.compile(r"no rush|whenever|when you (get|have) a (chance|moment)|not urgent", re.I)
HIGH_RISK = re.compile(r"lawyer|legal action|\bsue\b|gdpr|data breach|hacked|chargeback|fraud|stolen|privacy complaint", re.I)


@lru_cache(maxsize=1)
def _nlp():
    import spacy
    return spacy.blank("en")


@lru_cache(maxsize=1)
def _stop():
    from spacy.lang.en.stop_words import STOP_WORDS
    return STOP_WORDS - {"not", "no", "cannot", "how", "where", "why"}


def tokens(text: str) -> list[str]:
    stop = _stop()
    return [t.lower_ for t in _nlp().make_doc(text) if re.match(r"\w", t.text) and t.lower_ not in stop]


def rule_category(text: str) -> Category | None:
    for cat, pat in RULES:
        if pat.search(text):
            return cat
    return None


class NaiveBayes:
    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha
        self.counts: dict[str, Counter] = defaultdict(Counter)
        self.totals: Counter = Counter()
        self.docs: Counter = Counter()
        self.vocab: set[str] = set()

    def fit(self, texts: list[str], labels: list[str]) -> "NaiveBayes":
        for text, label in zip(texts, labels):
            toks = tokens(text)
            self.counts[label].update(toks)
            self.totals[label] += len(toks)
            self.docs[label] += 1
            self.vocab.update(toks)
        return self

    def log_probs(self, text: str) -> dict[str, float]:
        n_docs = sum(self.docs.values())
        v = len(self.vocab) or 1
        toks = [t for t in tokens(text) if t in self.vocab]
        out = {}
        for label in self.docs:
            lp = math.log(self.docs[label] / n_docs)
            denom = self.totals[label] + self.alpha * v
            for t in toks:
                lp += math.log((self.counts[label][t] + self.alpha) / denom)
            out[label] = lp
        return out


class Classifier:
    def __init__(self, nb: NaiveBayes | None = None):
        self.nb = nb

    @classmethod
    def train(cls, emails: list[dict]) -> "Classifier":
        return cls(NaiveBayes().fit([f"{e['subject']} {e['body']}" for e in emails], [e["label"]["category"] for e in emails]))

    def classify(self, subject: str, body: str) -> tuple[Category, float]:
        text = f"{subject}\n{body}"
        scores = {c.value: 0.0 for c in Category if c != Category.OTHER}
        if self.nb and self.nb.docs:
            nb = self.nb.log_probs(text)
            top = max(nb.values())
            scores = {c: nb.get(c, top - 50) - top for c in scores}  # shift for numeric stability
        rule = rule_category(text)
        if rule:
            scores[rule.value] += RULE_BONUS
        elif not (self.nb and self.nb.docs):
            return Category.OTHER, 0.0
        best = max(scores, key=scores.get)
        z = sum(math.exp(s - scores[best]) for s in scores.values())
        return Category(best), round(1 / z, 3)


def urgency(subject: str, body: str) -> str:
    text = f"{subject}\n{body}"
    if URGENT.search(text):
        return "high"
    return "low" if NOT_URGENT.search(text) else "normal"


def risk(category: Category, subject: str, body: str, urgency_level: str) -> str:
    if HIGH_RISK.search(f"{subject}\n{body}") or category in (Category.REFUND_CANCEL, Category.COMPLAINT):
        return "high"
    if category in (Category.QUOTE, Category.PROJECT_REQUEST, Category.OTHER) or urgency_level == "high":
        return "medium"
    return "low"
