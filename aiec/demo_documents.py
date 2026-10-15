"""Instalar únicamente los documentos ficticios distribuidos con el proyecto."""
import os
from pathlib import Path

from .config import ROOT


def load_examples(source=None, destination=None):
    source = Path(source or ROOT / "examples" / "knowledge")
    destination = Path(destination or ROOT / "knowledge")
    if source.is_symlink() or destination.is_symlink():
        raise ValueError("Las carpetas de ejemplos y conocimiento no pueden ser enlaces simbólicos.")
    files = sorted(source.rglob("*.md"))
    if not files:
        raise ValueError("No se encontraron ejemplos en examples/knowledge.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    copied, skipped = 0, 0
    for file in files:
        relative = file.relative_to(source)
        if any((source / part).is_symlink() for part in [relative, *relative.parents]):
            raise ValueError("No se copian enlaces simbólicos.")
        if any((destination / part).is_symlink() for part in [relative, *relative.parents]):
            raise ValueError("Un destino de conocimiento es un enlace simbólico; no se modificó.")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            skipped += 1
            continue
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(file.read_bytes())
        copied += 1
    return {"copied": copied, "skipped": skipped}
