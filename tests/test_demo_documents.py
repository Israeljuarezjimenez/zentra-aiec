import pytest

from aiec.config import ROOT
from aiec.demo_documents import load_examples


def test_examples_can_be_installed_from_a_fresh_clone_without_overwriting_private_docs(tmp_path):
    destination = tmp_path / "knowledge"
    assert load_examples(destination=destination) == {"copied": 8, "skipped": 0}
    document = destination / "00 Guia de la imprenta.md"
    document.write_text("Contenido privado del operador.")
    assert load_examples(destination=destination) == {"copied": 0, "skipped": 8}
    assert document.read_text() == "Contenido privado del operador."


def test_example_loader_refuses_symlinked_destinations(tmp_path):
    actual = tmp_path / "private"
    actual.mkdir()
    link = tmp_path / "knowledge"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="simbólicos"):
        load_examples(destination=link)
    assert not list(actual.iterdir())


def test_public_sources_are_declared_and_external_prices_never_become_own_rates():
    import json
    business = json.loads((ROOT / "aiec" / "catalog.json").read_text())
    source = ROOT / "examples" / "knowledge"
    distributed = {file.relative_to(source).as_posix() for file in source.rglob("*.md")}
    assert distributed == set(business["knowledge_documents"])
    for reference in business["reference_prices"]:
        assert reference["ownership"] == "externo"
        assert reference["approved_for_sale"] is False
        assert reference["url"].startswith("https://")
    assert all(item["price"] is None for item in business["services"])
