import os
import types

TOKEN = os.environ.get("SHARED_SECRET")


def go():
    return types.SimpleNamespace()
