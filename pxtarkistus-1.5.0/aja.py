#!/usr/bin/env python3
"""pxtarkistus – komentorivin käynnistin.

Varsinainen käyttöliittymä on moduulissa pxtarkistus/cli.py, jotta sen voi
asentaa myös komennoksi (`pip install .` -> `pxtarkistus ...`). Tämä tiedosto
on ennallaan siksi, että totuttu `python aja.py ...` toimii edelleen.

    python aja.py tarkista --joukko henkilo_kaikki --vuodet kaikki
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pxtarkistus.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
