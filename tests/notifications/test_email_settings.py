import pytest
from pydantic import ValidationError

from app.schemas.email_settings import EmailSenderSettingsUpdate


def test_email_sender_settings_normalizes_address_and_name():
    data = EmailSenderSettingsUpdate(from_email="  Orders@Example.com ", from_name="  Toti Cakery  ")
    assert data.from_email == "orders@example.com"
    assert data.from_name == "Toti Cakery"


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "x@y.com\r\nBcc:evil@example.com"])
def test_email_sender_settings_rejects_invalid_address(email):
    with pytest.raises(ValidationError):
        EmailSenderSettingsUpdate(from_email=email, from_name="Toti Cakery")


@pytest.mark.parametrize("name", ["", "  ", "Toti\r\nBcc:evil@example.com"])
def test_email_sender_settings_rejects_empty_or_header_injection_name(name):
    with pytest.raises(ValidationError):
        EmailSenderSettingsUpdate(from_email="orders@example.com", from_name=name)
