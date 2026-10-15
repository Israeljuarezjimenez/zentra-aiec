import pytest

from aiec.config import ROOT
from aiec.demo_documents import load_examples


def test_examples_can_be_installed_from_a_fresh_clone_without_overwriting_private_docs(tmp_path):
    destination = tmp_path / "knowledge"
    assert load_examples(destination=destination) == {"copied": 19, "skipped": 0}
    document = destination / "00 Guia del catalogo.md"
    document.write_text("Contenido privado del operador.")
    assert load_examples(destination=destination) == {"copied": 0, "skipped": 19}
    assert document.read_text() == "Contenido privado del operador."


def test_example_loader_refuses_symlinked_destinations(tmp_path):
    actual = tmp_path / "private"
    actual.mkdir()
    link = tmp_path / "knowledge"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="simbólicos"):
        load_examples(destination=link)
    assert not list(actual.iterdir())


def test_only_declared_fictional_documents_are_distributed():
    import json
    business = json.loads((ROOT / "aiec" / "catalog.json").read_text())
    source = ROOT / "examples" / "knowledge"
    distributed = {file.relative_to(source).as_posix() for file in source.rglob("*.md")}
    assert distributed == set(business["knowledge_documents"])
    for file in source.rglob("*.md"):
        assert "Material de demostración de Estudio Órbita." in file.read_text()
