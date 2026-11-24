import pytest

from app.clients import client_name, conversation_metadata, ordering_client
from app.settings import Settings

S = Settings(car_client_id="car-app", web_client_id="web-app", cli_client_id="cli-app")


@pytest.mark.parametrize("azp, test, name", [
    ("car-app", False, "car"), ("web-app", False, "web"), ("cli-app", False, "cli"),
    ("04b07795-8ddb-461a-bbee-02f9e1bf7b46", False, "other"), ("", False, "other"),
    ("car-app", True, "test"), ("someone-else", True, "test"),
])
def test_client_name(azp, test, name):
    assert client_name(S, azp, test) == name


def test_an_empty_setting_never_matches_an_empty_azp():
    assert client_name(Settings(car_client_id="", web_client_id="", cli_client_id=""), "", False) == "other"


def test_metadata_has_the_model_only_when_the_catalog_has_it():
    assert conversation_metadata("o", "car", "loop", "Voltrieve Spark") == {
        "owner_oid": "o", "client": "car", "backend": "loop", "car_model": "spark"}
    assert "car_model" not in conversation_metadata("o", "car", "loop", "Volvo EX30")
    assert "car_model" not in conversation_metadata("o", "car", "loop", None)


AZURE_CLI = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"


@pytest.mark.parametrize("azp, client", [
    ("car-app", "car"), ("web-app", "web"), ("cli-app", "cli"), (AZURE_CLI, "cli"), ("someone-else", "other"),
    ("", "other"),
])
def test_ordering_client(azp, client):
    for header in (None, "", "web", "cli", "other", "test", "1", "carx", "CAR"):
        assert ordering_client(S, azp, header) == client
    assert ordering_client(S, azp, "car") == "car"


@pytest.mark.parametrize("azp", ["", "car-app", "web-app"])
def test_empty_settings_match_no_azp(azp):
    empty = Settings(car_client_id="", web_client_id="", cli_client_id="")
    assert ordering_client(empty, azp, None) == "other"


def test_the_azure_cli_is_not_the_cli_in_the_usage_count():
    assert client_name(S, AZURE_CLI, False) == "other"
