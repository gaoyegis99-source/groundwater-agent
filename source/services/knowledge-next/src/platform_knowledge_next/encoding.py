import hashlib
import json
import re
import unicodedata
from uuid import uuid4

from .config import MODEL_PROFILE


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def uid(prefix):
    return prefix + "_" + uuid4().hex


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold()


def tokens(text):
    result = []
    for word in re.findall(r"[^\W_]+", normalize(text)):
        if len(word) < 3:
            result.append(word)
        else:
            result.extend(word[i : i + 3] for i in range(len(word) - 2))
    # Only alphanumeric lexemes survive; never pass user tsquery syntax through.
    return result


def input_text(document, unit):
    return f"{document.title}\n{document.summary}\n{unit.content}"


def input_hash(document, unit):
    return digest([MODEL_PROFILE, input_text(document, unit)])


def vector_text(vector):
    return "[" + ",".join(str(v) for v in vector) + "]"
