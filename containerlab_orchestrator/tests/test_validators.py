"""Tests for the node mapping validators."""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from containerlab_orchestrator.validators import (
    validate_clab_kind,
    validate_clab_memory,
    validate_image_reference,
    validate_json_object,
    validate_string_map,
)

TEXT_VALUE_MESSAGE = "Enter a text value."
JSON_OBJECT_MESSAGE = 'Enter a JSON object, for example {"key": "value"}.'
STRING_MAP_MESSAGE = "Each key and each value must be a string."


class ValidatorTestCase(SimpleTestCase):
    """Base class with helpers for the validator tests."""

    def assert_accepts(self, validator, values):
        """Assert that the validator accepts each value."""
        for value in values:
            with self.subTest(value=value):
                validator(value)

    def assert_rejects(self, validator, values, message=None):
        """Assert that the validator rejects each value, with the message if one is given."""
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as context:
                    validator(value)
                if message is not None:
                    self.assertEqual(context.exception.messages, [message])


class JsonObjectValidatorTestCase(ValidatorTestCase):
    """Test validate_json_object."""

    def test_accepts_object(self):
        self.assert_accepts(validate_json_object, [{}, {"binds": ["a:b"]}])

    def test_rejects_value_that_is_not_object(self):
        self.assert_rejects(validate_json_object, [[], "", (), [1], "text", 5, None], JSON_OBJECT_MESSAGE)


class StringMapValidatorTestCase(ValidatorTestCase):
    """Test validate_string_map."""

    def test_accepts_object_of_strings(self):
        self.assert_accepts(validate_string_map, [{}, {"CLAB_MGMT_VRF": "MGMT"}])

    def test_rejects_value_that_is_not_object(self):
        self.assert_rejects(validate_string_map, [[], "", [1]], JSON_OBJECT_MESSAGE)

    def test_rejects_value_that_is_not_string(self):
        self.assert_rejects(validate_string_map, [{"A": 1}, {"A": ["1"]}, {"A": {"B": "1"}}], STRING_MAP_MESSAGE)

    def test_rejects_key_that_is_not_string(self):
        self.assert_rejects(validate_string_map, [{1: "1"}], STRING_MAP_MESSAGE)


class KindValidatorTestCase(ValidatorTestCase):
    """Test validate_clab_kind."""

    def test_accepts_kind(self):
        self.assert_accepts(validate_clab_kind, ["ceos", "nokia_srlinux", "vr-sros"])

    def test_rejects_bad_kind(self):
        self.assert_rejects(
            validate_clab_kind,
            ["", "Ceos", "nokia srl", "1ceos", "ceos\n"],
            'Use lowercase letters, digits, "_", and "-", and start with a letter.',
        )

    def test_rejects_value_that_is_not_text(self):
        self.assert_rejects(validate_clab_kind, [5], TEXT_VALUE_MESSAGE)


class ImageValidatorTestCase(ValidatorTestCase):
    """Test validate_image_reference."""

    def test_accepts_image(self):
        self.assert_accepts(
            validate_image_reference,
            ["alpine:latest", "ghcr.io/nokia/srlinux:24.10", "registry:5000/ceos@sha256:abc"],
        )

    def test_rejects_bad_image(self):
        self.assert_rejects(
            validate_image_reference,
            ["", "alpine latest", "alpine:latest\n", "alpine\x00"],
            "The image name must contain only printable characters and no spaces.",
        )

    def test_rejects_value_that_is_not_text(self):
        self.assert_rejects(validate_image_reference, [5], TEXT_VALUE_MESSAGE)


class MemoryValidatorTestCase(ValidatorTestCase):
    """Test validate_clab_memory."""

    def test_accepts_number_with_unit(self):
        self.assert_accepts(validate_clab_memory, ["512mb", "1Gb", "1.5GiB", "2 GB", "512k"])

    def test_rejects_bad_memory(self):
        self.assert_rejects(
            validate_clab_memory,
            ["512", "1 gigabyte", "1Gi B", "1Gb\n", "1\nGb", "１Gb"],
            "Enter a number and a unit, for example 512mb or 1Gb.",
        )

    def test_rejects_value_that_is_not_text(self):
        self.assert_rejects(validate_clab_memory, [5], TEXT_VALUE_MESSAGE)
