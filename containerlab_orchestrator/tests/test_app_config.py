"""Tests for the app settings and the app config schema."""

import copy
import json
from pathlib import Path

import jsonschema
from django.conf import settings
from django.test import override_settings
from nautobot.apps.config import get_app_settings_or_config
from nautobot.apps.testing import TestCase

import containerlab_orchestrator
from containerlab_orchestrator import ContainerlabOrchestratorConfig

APP_NAME = "containerlab_orchestrator"
DEFAULT_SETTINGS = {
    "default_node_mapping": {"kind": "linux", "image": "alpine:latest"},
    "request_timeout": 300,
}
CUSTOM_SETTINGS = {
    "default_node_mapping": {"kind": "nokia_srlinux", "image": "ghcr.io/nokia/srlinux:latest"},
    "request_timeout": 60,
}
SCHEMA_PATH = Path(containerlab_orchestrator.__file__).parent / "app-config-schema.json"


def validated_config(user_config):
    """Return a copy of the user config after the app applies its defaults."""
    config = copy.deepcopy(user_config)
    ContainerlabOrchestratorConfig.validate(config, settings.VERSION)
    return config


class AppConfigTestCase(TestCase):
    """Test the default settings of the app and how Nautobot applies them."""

    def test_default_settings_declare_node_mapping_and_timeout(self):
        self.assertEqual(ContainerlabOrchestratorConfig.default_settings, DEFAULT_SETTINGS)

    def test_validate_applies_defaults_to_empty_config(self):
        self.assertEqual(validated_config({}), DEFAULT_SETTINGS)

    def test_get_app_settings_returns_defaults(self):
        with override_settings(PLUGINS_CONFIG={APP_NAME: validated_config({})}):
            for key, expected in DEFAULT_SETTINGS.items():
                with self.subTest(key=key):
                    self.assertEqual(get_app_settings_or_config(APP_NAME, key), expected)

    def test_validate_keeps_custom_values(self):
        self.assertEqual(validated_config(CUSTOM_SETTINGS), CUSTOM_SETTINGS)

    def test_validate_applies_default_for_missing_setting(self):
        config = validated_config({"request_timeout": 60})
        self.assertEqual(config["request_timeout"], 60)
        self.assertEqual(config["default_node_mapping"], DEFAULT_SETTINGS["default_node_mapping"])

    def test_get_app_settings_returns_custom_values(self):
        with override_settings(PLUGINS_CONFIG={APP_NAME: validated_config(CUSTOM_SETTINGS)}):
            for key, expected in CUSTOM_SETTINGS.items():
                with self.subTest(key=key):
                    self.assertEqual(get_app_settings_or_config(APP_NAME, key), expected)


class AppConfigSchemaTestCase(TestCase):
    """Test that the app config schema accepts valid settings and rejects invalid settings."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = json.loads(SCHEMA_PATH.read_text())

    def test_schema_accepts_default_settings(self):
        jsonschema.validate(DEFAULT_SETTINGS, self.schema)

    def test_schema_accepts_custom_settings(self):
        jsonschema.validate(CUSTOM_SETTINGS, self.schema)

    def test_schema_rejects_invalid_settings(self):
        invalid_configs = {
            "no kind": {**DEFAULT_SETTINGS, "default_node_mapping": {"image": "alpine:latest"}},
            "no image": {**DEFAULT_SETTINGS, "default_node_mapping": {"kind": "linux"}},
            "integer kind": {**DEFAULT_SETTINGS, "default_node_mapping": {"kind": 1, "image": "alpine:latest"}},
            "integer image": {**DEFAULT_SETTINGS, "default_node_mapping": {"kind": "linux", "image": 1}},
            "string timeout": {**DEFAULT_SETTINGS, "request_timeout": "300"},
            "float timeout": {**DEFAULT_SETTINGS, "request_timeout": 2.5},
            "zero timeout": {**DEFAULT_SETTINGS, "request_timeout": 0},
            "unknown top-level key": {**DEFAULT_SETTINGS, "request_timeuot": 300},
            "unknown nested key": {
                **DEFAULT_SETTINGS,
                "default_node_mapping": {"kind": "linux", "image": "alpine:latest", "imgae": "x"},
            },
        }
        for case, config in invalid_configs.items():
            with self.subTest(case=case):
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(config, self.schema)
