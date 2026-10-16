"""``python -m macos`` runs the native menu-bar app (macOS with PyObjC only)."""
from macos.menubar import main

if __name__ == "__main__":
    raise SystemExit(main())
