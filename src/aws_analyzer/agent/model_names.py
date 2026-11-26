"""
Names for saved models: a label the user picks ('work gpt', 'Hazel'), or one suggested from the model's own words, so
'claude-haiku-4-5' is offered 'Haiku Hummingbird', 'Happy Haiku' and 'Little Petal', and 'gpt-4o' 'GPT Gosling'.

model_keywords() takes the words that say which model it is out of an ID or profile (no versions, regions, vendors
or dates), and suggest_names() builds names from the first one: the word with an animal of the same letter, the word
with a friendly word of the same letter, and a name from the word's theme (_THEMES: a haiku is a petal, a sonnet a
quill). The choice depends only on the model's ID, so the same model is always offered the same names, and a name
already taken is never offered. Everything here is pure.
"""

from __future__ import annotations

import re

SUGGESTED = 3
_NUMBERED_NAMES = 40
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_VERSION = re.compile(r"^(v?\d+[a-z]?|\d+[kmb]|\d{6,8}|r\d+|o\d+|[a-z]\d+)$")
_NOISE = frozenset({
    "us", "eu", "apac", "global", "anthropic", "amazon", "meta", "mistral_ai", "cohere", "ai21", "openai", "azure",
    "accounts", "fireworks", "models", "latest", "preview", "instruct", "chat", "it", "hf", "turbo", "versatile",
    "arn", "aws", "bedrock", "inference", "profile", "foundation", "model", "the", "default",
})
_SHORT_NAMES = {"o1": "Owl", "o3": "Orbit", "o4": "Otter", "gpt": "GPT"}
_TRAILING_NUMBER = re.compile(r"(?<=[a-z])\d+$")
_ANIMALS = {
    "a": ("Alpaca", "Axolotl"), "b": ("Badger", "Bunny"), "c": ("Capybara", "Cuttlefish"), "d": ("Dormouse", "Dolphin"),
    "e": ("Echidna", "Egret"), "f": ("Fennec", "Finch"), "g": ("Gecko", "Gosling"), "h": ("Hedgehog", "Hummingbird"),
    "i": ("Ibis", "Impala"), "j": ("Jackalope", "Jay"), "k": ("Koala", "Kitten"), "l": ("Lemur", "Lynx"),
    "m": ("Marmot", "Meerkat"), "n": ("Narwhal", "Newt"), "o": ("Otter", "Ocelot"), "p": ("Penguin", "Puffin"),
    "q": ("Quokka", "Quail"), "r": ("Red Panda", "Robin"), "s": ("Sparrow", "Sea Otter"), "t": ("Tapir", "Turtle"),
    "u": ("Urchin", "Uakari"), "v": ("Vole", "Vervet"), "w": ("Wombat", "Wren"), "x": ("Xerus", "Xenops"),
    "y": ("Yak", "Yellowhammer"), "z": ("Zebra", "Zorilla"),
}
_FRIENDLY = {
    "a": "Amber", "b": "Bubbly", "c": "Cozy", "d": "Dreamy", "e": "Eager", "f": "Fluffy", "g": "Gentle", "h": "Happy",
    "i": "Itty", "j": "Jolly", "k": "Kind", "l": "Lucky", "m": "Merry", "n": "Nifty", "o": "Optimistic", "p": "Plucky",
    "q": "Quiet", "r": "Rosy", "s": "Sunny", "t": "Tiny", "u": "Upbeat", "v": "Velvet", "w": "Witty", "x": "Xtra",
    "y": "Yummy", "z": "Zippy",
}
_THEMES = {
    "haiku": ("Little Petal", "Tiny Verse", "Cherry Blossom"),
    "sonnet": ("Quill", "Rhyme Time", "Little Bard"),
    "opus": ("Maestro", "Grand Finale", "Big Symphony"),
    "claude": ("Clawdette", "Claudie", "Captain Claude"),
    "nova": ("Stardust", "Little Comet", "Twinkle"),
    "titan": ("Gentle Giant", "Big Buddy", "Atlas"),
    "llama": ("Lil Llama", "Llama Latte", "Fuzzy Drama"),
    "gpt": ("Pixel", "Byte Buddy", "Chatterbox"),
    "gemini": ("Twinkle Twins", "Star Pair", "Double Trouble"),
    "mistral": ("Breezy", "Sea Breeze", "Little Gust"),
    "mixtral": ("Whirlwind", "Breezy Bunch", "Mix Tape"),
    "deepseek": ("Deep Diver", "Pearl Hunter", "Snorkel"),
    "grok": ("Groovy", "Cosmo", "Rocket"),
    "command": ("Captain", "Commander Cuddles", "Skipper"),
    "qwen": ("Queenie", "Q-Tip", "Little Qi"),
    "phi": ("Fifi", "Pocket Phi", "Golden Ratio"),
    "jamba": ("Jamboree", "Jam Session", "Mango Jam"),
    "ollama": ("Ollie", "Home Llama", "Cozy Llama"),
}
_ANY_THEME = ("Sidekick", "Buddy", "Sunbeam", "Pebble", "Biscuit", "Noodle")


def model_keywords(model: str) -> list[str]:
    """
    The words of a model's ID that say which model it is, in order: 'us.anthropic.claude-haiku-4-5-20251001-v1:0' ->
    ['claude', 'haiku']; 'gpt-4o' -> ['gpt']; 'llama3.1' -> ['llama']; 'o3' -> ['o3'].
    """
    words = [word for word in _WORD_SPLIT.split(model.lower()) if word]
    keywords = []
    for word in words:
        is_named_short = word in _SHORT_NAMES
        is_noise = word in _NOISE or (_VERSION.match(word) is not None and not is_named_short)
        if is_noise:
            continue
        keyword = word if is_named_short else _TRAILING_NUMBER.sub("", word)
        if keyword not in keywords:
            keywords.append(keyword)
    return keywords


def _main_keyword(keywords: list[str]) -> str:
    """
    The keyword names are built from: the first one that has a theme of its own after the vendor's family name
    ('haiku' over 'claude'), else the first.
    """
    themed = [word for word in keywords if word in _THEMES]
    specific = [word for word in themed if word != "claude"]
    return (specific or themed or keywords)[0]


def _display_word(word: str) -> str:
    return _SHORT_NAMES.get(word, word.title())


def _seed(text: str) -> int:
    """
    A number that depends only on the text (Python's hash() changes between runs), to pick among names.
    """
    return sum((index + 1) * ord(char) for index, char in enumerate(text))


def _candidates(keyword: str, seed: int) -> list[str]:
    """
    Every name the keyword could have, the three kinds taking turns: animal, friendly word, theme.
    """
    letter = keyword[0] if keyword[0] in _ANIMALS else "b"
    animals = _ANIMALS[letter][seed % 2:] + _ANIMALS[letter][:seed % 2]
    themes = list(_THEMES.get(keyword, _ANY_THEME))
    themes = themes[seed % len(themes):] + themes[:seed % len(themes)]
    word = _display_word(keyword)
    kinds = [[f"{word} {animal}" for animal in animals], [f"{_FRIENDLY[letter]} {word}"], themes]
    names: list[str] = []
    for turn in range(max(len(kind) for kind in kinds)):
        names += [kind[turn] for kind in kinds if turn < len(kind)]
    return names


def suggest_names(provider: str, model: str, *, taken: tuple[str, ...] | list[str] = (), most: int = SUGGESTED
                  ) -> list[str]:
    """
    Up to `most` cute names for the model, none of them `taken` (any case): 'claude-haiku-4-5' -> ['Haiku Hummingbird',
    'Happy Haiku', 'Little Petal']. A model with no word of its own ('' or only numbers) is named after its provider.
    """
    keywords = model_keywords(model) or model_keywords(provider) or ["buddy"]
    keyword = _main_keyword(keywords)
    seed = _seed(f"{provider}:{model}")
    taken_names = {name.casefold() for name in taken}
    names: list[str] = []
    for name in _candidates(keyword, seed) + [f"{_display_word(keyword)} {number}" for number in range(2, _NUMBERED_NAMES)]:
        if name.casefold() not in taken_names and name not in names:
            names.append(name)
        if len(names) == most:
            break
    return names


def clean_model_name(name: str) -> str:
    """
    A name as it's kept: spaces squeezed, at most 40 characters. ValueError for an empty one.
    """
    cleaned = " ".join(str(name or "").split())[:40].strip()
    if not cleaned:
        raise ValueError("A saved model's name can't be empty: give it a word or two, like 'work gpt'.")
    return cleaned
