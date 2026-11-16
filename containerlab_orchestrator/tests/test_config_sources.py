"""Tests for the startup config sources and the startup config keys of the topology builder."""

from unittest import mock

import yaml
from celery.exceptions import SoftTimeLimitExceeded
from django.db import DatabaseError, connection
from nautobot.apps.testing import TestCase
from nautobot.apps.utils import render_jinja2
from nautobot.dcim.models import Device

from containerlab_orchestrator import config_sources, topology
from containerlab_orchestrator.choices import NodeMappingConfigSourceChoices
from containerlab_orchestrator.config_sources import (
    W_EMPTY_CONFIG,
    W_NO_TEMPLATE,
    W_SOURCE_NOT_AVAILABLE,
    W_TEMPLATE_ERROR,
    W_UNKNOWN_SOURCE,
    source_of,
)
from containerlab_orchestrator.mapper import DefaultMapping
from containerlab_orchestrator.models import NodeMapping
from containerlab_orchestrator.tests.test_topology import TopologyTestCase, load_clab_schema, new_lab
from containerlab_orchestrator.topology import SRL_KINDS, W_OVERRIDE, build_topology, to_yaml

ONE_LAYER = "{{`{{`}}"
"""The escape of {{ for one template pass."""

TWO_LAYERS = "{{`{{`}}`{{`{{`}}`}}"
"""The escape of {{ for two template passes."""

THREE_LAYERS = "{{`{{`}}`{{`{{`}}`}}`{{`{{`}}`{{`{{`}}`}}`}}"
"""The escape of {{ for three template passes."""

PROBE_CASES = {
    "n1": "hostname r1\nenable secret 9 $9$abc$def\n",
    "n2": "banner ${HOME} $HOME $$ $1 ${1} ${HOME:-x}\n",
    "n3": "x {{ .Env }} {{- 1 -}} {{{ }} }}} {{`a`}}\n",
    "n7": "{{",
    "n8": "a $",
}
"""The probe inputs of plan section 11 that have a $ or a {{."""

SRL_JSON_TEXT = ' \n{"system": {"banner": "${HOME} {{ x }}"}}\n'
"""An SR Linux JSON config, which containerlab reads with one more env and template pass."""


containerlab_text = topology._containerlab_text  # pylint: disable=protected-access


def lab_with_source(source):
    """Return an unsaved lab with the default config source."""
    lab = new_lab()
    lab.config_source = source
    return lab


class ContainerlabTextTestCase(TestCase):
    """Test the escape of a startup config for each kind group."""

    def test_containerlab_text_escapes_for_each_kind_group(self):
        groups = (
            ("ceos", "$$$$", TWO_LAYERS),
            ("cisco_iol", "$$", TWO_LAYERS),
            ("cisco_vios", "$$", ONE_LAYER),
            ("arrcus_arcos", "$$", ONE_LAYER),
            ("srl", "$$$$", TWO_LAYERS),
            ("nokia_srlinux", "$$$$", TWO_LAYERS),
            ("nokia_srsim", "$" * 8, THREE_LAYERS),
            ("nokia_sros", "$$$$", TWO_LAYERS),
            ("NOKIA_SRSIM", "$" * 8, THREE_LAYERS),
        )
        for kind, dollar, brace in groups:
            for case, text in PROBE_CASES.items():
                if kind in SRL_KINDS and case == "n7":
                    continue
                with self.subTest(kind=kind, case=case):
                    expected = text.replace("$", dollar).replace("{{", brace)
                    self.assertEqual(containerlab_text(text, kind), expected.rstrip("\n") + "\n")
        for kind in SRL_KINDS:
            with self.subTest(kind=kind, case="json"):
                expected = SRL_JSON_TEXT.replace("$", "$" * 8).replace("{{", THREE_LAYERS)
                self.assertEqual(containerlab_text(SRL_JSON_TEXT, kind), expected)
                self.assertEqual(containerlab_text(PROBE_CASES["n7"], kind), f"{THREE_LAYERS}\n")

    def test_containerlab_text_adds_final_newline(self):
        for text in ("/etc/passwd", "https://example.com/config.txt", "hostname r1"):
            with self.subTest(text=text):
                self.assertEqual(containerlab_text(text, "linux"), f"{text}\n")
        self.assertEqual(containerlab_text("hostname r1\n", "linux"), "hostname r1\n")


class StartupConfigTestCase(TopologyTestCase):
    """Base class with the helper that sets the startup config fields of the Arista mapping."""

    def set_arista_config(self, template="", source=NodeMappingConfigSourceChoices.JINJA2):
        """Set the config template and the config source of the Arista mapping row."""
        NodeMapping.objects.filter(pk=self.arista_mapping.pk).update(config_source=source, config_template=template)


class StartupConfigSourceTestCase(StartupConfigTestCase):  # pylint: disable=too-many-ancestors
    """Test the source order and the Jinja2 source of the startup config."""

    def nodes_and_warnings(self, devices, source=NodeMappingConfigSourceChoices.NONE, user=None):
        """Return the nodes and the warnings of a topology of the devices with the lab default source."""
        built, warnings, _ = build_topology(lab_with_source(source), devices, user)
        return built["topology"]["nodes"], warnings

    def test_mapping_source_wins_over_lab_source(self):
        self.set_arista_config("hostname {{ device.name }}")
        nodes, warnings = self.nodes_and_warnings([self.arista1])
        self.assertEqual(nodes["arista1"]["startup-config"], "hostname arista1\n")
        self.assertEqual(warnings, [])
        self.set_arista_config("hostname {{ device.name }}", NodeMappingConfigSourceChoices.NONE)
        nodes, warnings = self.nodes_and_warnings([self.arista1], NodeMappingConfigSourceChoices.JINJA2)
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertEqual(warnings, [])

    def test_empty_mapping_source_uses_lab_source(self):
        self.set_arista_config("hostname {{ device.name }}", "")
        nodes, _ = self.nodes_and_warnings([self.arista1], NodeMappingConfigSourceChoices.JINJA2)
        self.assertEqual(nodes["arista1"]["startup-config"], "hostname arista1\n")
        default_mapping = DefaultMapping(kind="linux", image="alpine:latest")
        for lab_source, expected in (
            (NodeMappingConfigSourceChoices.JINJA2, NodeMappingConfigSourceChoices.JINJA2),
            ("", NodeMappingConfigSourceChoices.NONE),
        ):
            with self.subTest(lab_source=lab_source):
                self.assertEqual(source_of(default_mapping, lab_with_source(lab_source)), expected)
        nodes, warnings = self.nodes_and_warnings([self.arista1, self.leaf1], "")
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertNotIn("startup-config", nodes["leaf1"])
        self.assertEqual(warnings, [])

    def test_none_source_gives_no_startup_config_keys(self):
        self.set_arista_config("hostname {{ device.name }}", NodeMappingConfigSourceChoices.NONE)
        nodes, warnings = self.nodes_and_warnings([self.arista1, self.leaf1, self.nokia1])
        for node in nodes.values():
            self.assertNotIn("startup-config", node)
            self.assertNotIn("enforce-startup-config", node)
        self.assertEqual(warnings, [])

    def test_jinja2_source_renders_device_lab_and_interface_map(self):
        self.set_arista_config(
            "hostname {{ device.name }}\nlab {{ lab.name }}\nserial {{ device.serial }}\n"
            "{% for name, container in interface_map|dictsort %}{{ name }}={{ container }}\n{% endfor %}",
        )
        self.arista1.serial = "a&b<c>"
        nodes, warnings = self.nodes_and_warnings([self.arista1])
        self.assertEqual(
            nodes["arista1"]["startup-config"],
            "hostname arista1\nlab lab-a\nserial a&b<c>\n"
            "GigabitEthernet0/1=eth1\nGigabitEthernet0/10=eth10\nGigabitEthernet0/2=eth2\n",
        )
        self.assertEqual(warnings, [])

    def test_jinja2_without_template_warns_and_gives_no_config(self):
        self.set_arista_config()
        nodes, warnings = self.nodes_and_warnings([self.arista1, self.leaf1], NodeMappingConfigSourceChoices.JINJA2)
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertNotIn("startup-config", nodes["leaf1"])
        self.assertEqual(
            warnings,
            [
                W_NO_TEMPLATE.format(device="arista1", mapping=self.arista_mapping),
                W_NO_TEMPLATE.format(device="leaf1", mapping="Default mapping"),
            ],
        )

    def test_template_error_warns_and_build_continues(self):
        NodeMapping.objects.filter(pk=self.nokia_mapping.pk).update(
            config_source=NodeMappingConfigSourceChoices.JINJA2, config_template="hostname {{ device.name }}"
        )
        for template, error in (
            ("{{ missing.name }}", "'missing' is undefined"),
            ("{% if %}", "Expected an expression, got 'end of statement block'"),
            ("{{ device.name }}", "a template error of any type"),
        ):
            with self.subTest(template=template):
                self.set_arista_config(template)
                any_error = ValueError(error) if template == "{{ device.name }}" else None
                with mock.patch.object(config_sources, "render_jinja2", side_effect=self.raise_for_arista(any_error)):
                    nodes, warnings = self.nodes_and_warnings([self.arista1, self.nokia1])
                self.assertNotIn("startup-config", nodes["arista1"])
                self.assertEqual(nodes["nokia1"]["startup-config"], "hostname nokia1\n")
                self.assertEqual(
                    warnings, [W_TEMPLATE_ERROR.format(device="arista1", mapping=self.arista_mapping, error=error)]
                )

    @staticmethod
    def raise_for_arista(error):
        """Return a render_jinja2 stand-in that raises the error for the arista1 template, else renders."""

        def render(template_code, context):
            if error is not None and context["device"].name == "arista1":
                raise error
            return render_jinja2(template_code, context)

        return render

    def test_soft_time_limit_stops_the_build(self):
        self.set_arista_config("hostname {{ device.name }}")
        with mock.patch.object(config_sources, "render_jinja2", side_effect=SoftTimeLimitExceeded()):
            with self.assertRaises(SoftTimeLimitExceeded):
                build_topology(new_lab(), [self.arista1])

    def test_empty_template_output_warns(self):
        self.set_arista_config("{% if false %}x{% endif %} \n\t")
        nodes, warnings = self.nodes_and_warnings([self.arista1])
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertEqual(warnings, [W_EMPTY_CONFIG.format(device="arista1", source="Jinja2 template")])

    def test_golden_config_source_warns_in_c1(self):
        for source, label in (
            (NodeMappingConfigSourceChoices.GOLDEN_CONFIG_INTENDED, "Golden Config intended"),
            (NodeMappingConfigSourceChoices.GOLDEN_CONFIG_BACKUP, "Golden Config backup"),
        ):
            with self.subTest(source=source):
                nodes, warnings = self.nodes_and_warnings([self.leaf1], source)
                self.assertNotIn("startup-config", nodes["leaf1"])
                self.assertEqual(warnings, [W_SOURCE_NOT_AVAILABLE.format(device="leaf1", source=label)])

    def test_unknown_source_warns(self):
        self.set_arista_config("hostname {{ device.name }}", "golden")
        nodes, warnings = self.nodes_and_warnings([self.arista1])
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertEqual(warnings, [W_UNKNOWN_SOURCE.format(device="arista1", source="golden")])

    def test_template_database_error_rolls_back_its_savepoint(self):
        self.set_arista_config("hostname {{ device.name }}")

        def broken_query(*_args):
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM containerlab_orchestrator_no_such_table")

        with mock.patch.object(config_sources, "render_jinja2", side_effect=broken_query):
            nodes, warnings = self.nodes_and_warnings([self.arista1])
        self.assertNotIn("startup-config", nodes["arista1"])
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("Device arista1: the config template of "))
        self.assertTrue(Device.objects.filter(pk=self.arista1.pk).exists())
        with mock.patch.object(config_sources, "render_jinja2", side_effect=DatabaseError("lost")):
            _, warnings = self.nodes_and_warnings([self.arista1])
        self.assertEqual(
            warnings, [W_TEMPLATE_ERROR.format(device="arista1", mapping=self.arista_mapping, error="lost")]
        )


class StartupConfigBuildTestCase(StartupConfigTestCase):  # pylint: disable=too-many-ancestors
    """Test the startup config keys that build_topology writes on the nodes."""

    def test_build_escapes_jinja2_output_and_keeps_it(self):
        self.set_arista_config("enable secret 9 $9$abc\nbanner {{ '{{' }} x")
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        self.assertEqual(
            built["topology"]["nodes"]["arista1"]["startup-config"],
            f"enable secret 9 $$$$9$$$$abc\nbanner {TWO_LAYERS} x\n",
        )
        self.assertEqual(warnings, [])

    def test_startup_config_node_has_enforce_key(self):
        self.set_arista_config("hostname {{ device.name }}")
        built, _, _ = build_topology(new_lab(), [self.arista1, self.leaf1])
        nodes = built["topology"]["nodes"]
        self.assertIs(nodes["arista1"]["enforce-startup-config"], True)
        self.assertNotIn("enforce-startup-config", nodes["leaf1"])

    def test_interface_map_holds_table_rows_and_builder_names(self):
        self.set_arista_config(
            "{% for name, container in interface_map|dictsort %}{{ name }}={{ container }}\n{% endfor %}"
        )
        wan1 = self.add_device("wan1")
        self.connect(
            self.add_interfaces(self.arista1, "Ethernet5")[0], self.add_interfaces(self.arista2, "Ethernet7")[0]
        )
        self.connect(self.add_interfaces(self.arista1, "Ethernet6")[0], self.add_interfaces(wan1, "Ethernet1")[0])
        self.add_permissions("dcim.view_device", constraints={"name__in": ["arista1", "arista2"]})
        with mock.patch.object(topology, "map_interfaces", wraps=topology.map_interfaces) as mapper:
            built, _, _ = build_topology(new_lab(), [self.arista1, self.arista2], self.user)
        self.assertEqual(mapper.call_count, 2)
        nodes = built["topology"]["nodes"]
        table = "GigabitEthernet0/1=eth1\nGigabitEthernet0/10=eth10\nGigabitEthernet0/2=eth2\n"
        self.assertEqual(nodes["arista1"]["startup-config"], f"Ethernet5=eth3\nEthernet6=eth4\n{table}")
        self.assertEqual(nodes["arista2"]["startup-config"], f"Ethernet7=eth3\n{table}")

    def test_overrides_cannot_set_startup_config(self):
        self.set_arista_config("hostname {{ device.name }}")
        self.set_arista_overrides({"startup-config": "/etc/passwd", "enforce-startup-config": False})
        built, warnings, _ = build_topology(new_lab(), [self.arista1])
        node = built["topology"]["nodes"]["arista1"]
        self.assertEqual((node["startup-config"], node["enforce-startup-config"]), ("hostname arista1\n", True))
        self.assertEqual(
            warnings,
            [W_OVERRIDE.format(mapping=self.arista_mapping, keys="enforce-startup-config, startup-config")],
        )

    def test_stub_nodes_get_no_startup_config(self):
        self.set_arista_config("hostname {{ device.name }}")
        wan1 = self.add_device("wan1")
        self.connect(self.add_interfaces(self.arista1, "Ethernet5")[0], self.add_interfaces(wan1, "Ethernet1")[0])
        built, _, _ = build_topology(lab_with_source(NodeMappingConfigSourceChoices.JINJA2), [self.arista1])
        nodes = built["topology"]["nodes"]
        self.assertEqual(nodes["arista1"]["startup-config"], "hostname arista1\n")
        self.assertEqual(set(nodes["stub-wan1"]), {"kind", "image", "mgmt-ipv4"})

    def test_topology_with_startup_config_passes_schema(self):
        self.use_full_kind_names()
        self.set_arista_config("hostname {{ device.name }}\nusername admin secret $9$x {{ '{{' }}\n")
        built, _, _ = build_topology(new_lab(), [self.arista1, self.arista2, self.leaf1])
        self.assertIn("startup-config", built["topology"]["nodes"]["arista2"])
        self.assertEqual([error.message for error in load_clab_schema().iter_errors(built)], [])

    def test_to_yaml_writes_block_style_and_round_trips(self):
        self.set_arista_config("hostname {{ device.name }}\nbanner x\n")
        built, _, _ = build_topology(new_lab(), [self.arista1, self.arista2])
        nodes = built["topology"]["nodes"]
        nodes["arista2"]["startup-config"] = "trailing space \ncrlf line\r\nnext\r\n"
        text = to_yaml(built)
        self.assertIn("      startup-config: |\n        hostname arista1\n        banner x\n", text)
        self.assertIn('startup-config: "trailing space', text)
        self.assertIn("name: lab-a\n", text)
        self.assertEqual(yaml.safe_load(text), built)
        self.assertNotIn("|", yaml.safe_dump({"a": "x\ny\n"}))
