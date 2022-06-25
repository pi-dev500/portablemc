"""
Development tool script for managing all modules at once using poetry.
"""

import subprocess
import sys
import os

def for_each_module(args):

    os.chdir(os.path.dirname(__file__))

    def inner(name: str, path: str):
        print(f"{name} > {' '.join(args)}")
        subprocess.call(args, cwd=path)

    inner("core", "core")

    with os.scandir() as dirs:
        for entry in dirs:
            if entry.is_dir() and entry.name != "core":
                inner(entry.name, entry.path)

if __name__ == '__main__':
    for_each_module(["poetry"] + sys.argv[1:])
    sys.exit(0)
