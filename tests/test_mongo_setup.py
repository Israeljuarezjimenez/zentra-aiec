from urllib.parse import unquote, urlsplit

import pytest

from aiec.mongo_setup import with_password


def test_replaces_placeholder_and_encodes_special_characters():
    uri = "mongodb+srv://test-user:<db_password>@example.invalid/?retryWrites=true&w=majority"
    password = "test:@/#? %<>"
    result = with_password(uri, password)
    parts = urlsplit(result)
    assert unquote(parts.password) == password
    assert parts.username == "test-user"
    assert parts.hostname == "example.invalid"
    assert parts.query == "retryWrites=true&w=majority"
    assert parts.fragment == ""
    assert "<db_password>" not in result


def test_existing_encoded_username_and_database_are_preserved():
    result = with_password("mongodb+srv://user%40demo:old@example.invalid/aiec?authSource=admin", "new")
    parts = urlsplit(result)
    assert unquote(parts.username) == "user@demo"
    assert parts.path == "/aiec"
    assert parts.query == "authSource=admin"


@pytest.mark.parametrize("uri,password", [("", "test"), ("https://example.invalid", "test"),
                                            ("mongodb+srv://user:old@example.invalid", "")])
def test_rejects_missing_connection_or_empty_password(uri, password):
    with pytest.raises(ValueError):
        with_password(uri, password)
