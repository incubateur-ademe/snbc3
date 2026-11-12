"""
config/logging_config.py
=========================
Configuration du logging racine avec sortie colorée dans le terminal.
"""

import logging
import sys

LEVEL_COLORS = {
    "DEBUG": "cyan",
    "INFO": "green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold_red",
}


def setup_logging(verbose: bool = False) -> None:
    """Configure le logger racine (couleurs si le terminal les supporte)."""
    level = logging.DEBUG if verbose else logging.INFO
    use_color = sys.stderr.isatty()

    if use_color:
        try:
            import colorama
            colorama.init()
        except ImportError:
            pass

    try:
        import colorlog
    except ImportError:
        colorlog = None

    if colorlog is not None and use_color:
        formatter = colorlog.ColoredFormatter(
            "%(cyan)s%(asctime)s%(reset)s %(log_color)s%(levelname)-8s%(reset)s %(message)s",
            datefmt="%H:%M:%S",
            log_colors=LEVEL_COLORS,
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
        )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    if colorlog is None and use_color:
        root.warning(
            "colorlog non installé dans cet environnement Python (%s) : "
            "logs non colorés. Vérifiez que la commande est lancée via "
            "'uv run ...' (environnement du projet).", sys.executable,
        )
