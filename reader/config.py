"""Settings from config.json in the project folder (see config.example.json); command-line options win."""

import json
import os

from . import PROJECT_DIR

PATH = os.path.join(PROJECT_DIR, "config.json")
DEFAULTS = {
    "manga": "~/Manga",          # folders: "~" is your home folder; relative paths start at the config file
    "books": "graded-readers",   # the sample library in this project
    "port": 8421,
    "lan": False,                # let other devices connect (there is no login)
    "allowHosts": [],            # extra host names to answer with lan, such as a Tailscale name
    "skipMangaFolders": [],      # manga folder names to leave out of the library and OCR
    "jiten": True,               # look up difficulty and summaries on jiten.moe
    "anki": {
        "url": "http://127.0.0.1:8765",  # AnkiConnect
        "search": "",            # extra Anki search for the cards that count, such as "deck:Japanese"
        "knownInterval": 21,     # days at which a card's word counts as known
        # Note type -> the field holding its word.
        "wordFields": {"Kaishi 1.5k": "Word", "Japanese sentences": "VocabKanji", "Lapis Simplified": "Expression",
                       "JLPT Tango N5 Japanese": "Expression",
                       "JlabNote-JlabConverted-1": "Jlab-Lemma", "JlabNote-JlabConverted-1+": "Jlab-Lemma"},
        "wordListFields": ["Jlab-Lemma"],             # fields listing every word of a sentence, space-separated
        "wordCardsOnly": ["JLPT Tango N5 Japanese"],  # note types mixing word and sentence cards: sentences skipped
    },
}


class ConfigError(Exception):
    pass


def load(path=None):
    """The settings, with defaults for anything config.json leaves out and folders made absolute."""
    path = path or PATH
    try:
        with open(path, encoding="utf-8-sig") as file:  # Notepad may add a byte-order mark
            user = json.load(file)
    except FileNotFoundError:
        user = {}
    except (OSError, ValueError) as error:
        raise ConfigError(f"Could not read {path}: {error}") from error
    settings = merge(DEFAULTS, user, os.path.basename(path))
    base = os.path.dirname(os.path.abspath(path))
    for key in ("manga", "books"):
        settings[key] = os.path.abspath(os.path.join(base, os.path.expanduser(settings[key])))
    return settings


def save(changes, path=None):
    """Write changes (settings, with "anki" as a partial dict) into the config file; return load()'s result.

    Settings not in changes, and the way the file writes them ("~/Manga"), are kept as they are.
    """
    path = path or PATH
    try:
        with open(path, encoding="utf-8-sig") as file:
            current = json.load(file)
    except FileNotFoundError:
        current = {}
    except (OSError, ValueError) as error:
        raise ConfigError(f"Could not read {path}: {error}") from error
    if not isinstance(current, dict):
        raise ConfigError(f"{os.path.basename(path)} must be a JSON object")
    updated = {**current, **changes}
    if isinstance(changes.get("anki"), dict) and isinstance(current.get("anki"), dict):
        updated["anki"] = {**current["anki"], **changes["anki"]}
    merge(DEFAULTS, updated, os.path.basename(path))  # nothing is written unless all of it is valid
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(updated, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(tmp_path, path)
    except OSError as error:
        raise ConfigError(f"Could not save {path}: {error}") from error
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return load(path)


def merge(defaults, user, where):
    if not isinstance(user, dict):
        raise ConfigError(f"{where} must be a JSON object")
    unknown = sorted(set(user) - set(defaults))
    if unknown:
        raise ConfigError(f"{where}: unknown setting {', '.join(map(repr, unknown))}; see config.example.json")
    result = {}
    for key, default in defaults.items():
        value = user.get(key, default)
        name = f"{where}: {key!r}"
        if key == "wordFields":
            if not (isinstance(value, dict) and value and
                    all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in value.items())):
                raise ConfigError(f"{name} must map at least one note type to a field name")
        elif isinstance(default, dict):
            value = merge(default, value, name)
        elif isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"{name} must be true or false")
        elif isinstance(default, int):
            limits = (0, 65535) if key == "port" else (1, 36500)
            if not (isinstance(value, int) and not isinstance(value, bool) and limits[0] <= value <= limits[1]):
                raise ConfigError(f"{name} must be a whole number from {limits[0]} to {limits[1]}")
        elif isinstance(default, list):
            if not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
                raise ConfigError(f"{name} must be a list of text")
        elif not isinstance(value, str) or (key in ("manga", "books") and not value):
            raise ConfigError(f"{name} must be text")
        elif key == "url" and not value.startswith(("http://", "https://")):
            raise ConfigError(f"{name} must start with http:// or https://")
        result[key] = value
    return result
