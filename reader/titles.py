"""Fuzzy title matching for looking up local manga and books on Jiten, Natively and jpdb."""

import re
import unicodedata

MATCH_THRESHOLD = 0.72
EXACT = 0.99  # a match this good ends the search; a weaker one tries the other variants of the name
JAPANESE = re.compile(r"[぀-ヿ㐀-鿿豈-﫿々〆]")


def normalize_title(value):
    value = unicodedata.normalize("NFKC", value or "").lower()
    value = re.sub(r"[‘’`´]", "'", value)
    value = re.sub(r"[“”]", '"', value)
    value = re.sub(r"[「」『』【】〈〉《》]", " ", value)
    value = re.sub(r"[()\[\]{}:;,.!?/\\|~、。・]", " ", value)
    value = re.sub(r"[\-‐‑‒–—―]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def title_tokens(value):
    return [t for t in normalize_title(value).split(" ") if len(t) > 1]


def collapse_long_vowels(value):
    # "ou"->"o", "uu"->"u" — the two unambiguous long-vowel romanizations
    # (お段+う, う段+う). Deliberately not touching "ei"->"e": that pattern
    # is far more often a real distinct sequence (e.g. "keitai") than a
    # long-vowel marker, so collapsing it risks false positive matches.
    return re.sub(r"uu", "u", re.sub(r"ou", "o", value))


def title_similarity(a, b):
    aa, bb = normalize_title(a), normalize_title(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    if len(aa) >= 5 and (bb in aa or aa in bb):
        return 0.92
    # Catches cases like folder name "Yotsubato" vs Jiten's "Yotsuba to!" —
    # same title, just missing/extra whitespace between words.
    sa, sb = aa.replace(" ", ""), bb.replace(" ", "")
    if len(sa) >= 5 and sa == sb:
        return 0.9
    # Catches romanization variance like "Kyoko" vs the official "Kyouko".
    ca, cb = collapse_long_vowels(sa), collapse_long_vowels(sb)
    if len(ca) >= 5 and ca == cb:
        return 0.85
    at, bt = set(title_tokens(aa)), set(title_tokens(bb))
    common = len(at & bt)
    union = len(at | bt)
    return common / union if union else 0.0


def best_similarity(queries, candidates):
    return max((title_similarity(query, candidate) for query in queries for candidate in candidates if candidate),
               default=0.0)


# Common scanlation/reprint edition markers that get appended to a title
# in local file naming (e.g. "ドメスティックな彼女　よりぬきカラー版"). Jiten's
# search does near-exact prefix matching, so a query carrying one of these
# returns nothing even though the base title is indexed — strip them
# before searching. Longest-first so "よりぬきカラー版" doesn't get partially
# matched by "カラー版" and leave a dangling "よりぬき".
EDITION_SUFFIXES = sorted(
    ["よりぬきカラー版", "カラー版", "完全版", "新装版", "愛蔵版", "総集編", "決定版"],
    key=len, reverse=True
)


def strip_edition_suffix(name):
    for suffix in EDITION_SUFFIXES:
        new = re.sub(r"[\s　]+" + re.escape(suffix) + r"\s*$", "", name)
        if new != name:
            return new.strip()
    return name


def clean_title_variants(name):
    """
    Local library folder names are often decorated with release-group tags,
    volume-range suffixes, and edition markers (e.g. "Group.Name_Saike
    Mata Shitemo vol 01-15") that hurt fuzzy matching against Jiten's clean
    titles. Best effort only — try the raw name plus a few stripped
    variants, in order from least to most aggressively cleaned.
    """
    variants = [name]

    no_vol = re.sub(r"\s*vol\.?\s*\d+\s*-\s*\d+\s*$", "", name, flags=re.IGNORECASE).strip()
    if no_vol and no_vol not in variants:
        variants.append(no_vol)

    # EPUB titles often end in a publisher label or bonus note, e.g.
    # "三日間の幸福 (メディアワークス文庫)" or "五人一役でも君が好き【電子特典付き】";
    # downloaded files may end in a hash such as "[d5af847c]".
    no_label = re.sub(r"(?:[\s　]*(?:[(（【<＜［\[][^)）】>＞］\]]*[)）】>＞］\]]|電子特典付き))+[\s　]*$", "",
                      no_vol or name).strip()
    if no_label and no_label not in variants:
        variants.append(no_label)

    no_edition = strip_edition_suffix(no_label or no_vol or name)
    if no_edition and no_edition not in variants:
        variants.append(no_edition)

    m = re.match(r"^[A-Za-z0-9.]+[_.]([A-Za-z].*)$", no_vol or name)
    if m:
        candidate = m.group(1).strip()
        if candidate and candidate not in variants:
            variants.append(candidate)

    # "キノの旅 the Beautiful World": Japanese sites often index the Japanese part alone.
    m = re.match(r"^(.*[぀-ヿ㐀-鿿々〆][^A-Za-z]*?)[\s　]+[A-Za-z][A-Za-z\s'!?.,-]*$", variants[-1])
    if m and len(m.group(1).strip()) >= 2 and m.group(1).strip() not in variants:
        variants.append(m.group(1).strip())

    return variants
