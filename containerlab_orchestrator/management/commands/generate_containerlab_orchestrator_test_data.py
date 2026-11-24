"""Generate the demo data of the Containerlab Orchestrator app.

The demo data is one location with an Arista spine and leaf fabric, a Linux host on each leaf, the IP addresses of
each link and each management interface, the node mappings, a containerlab host, and a lab of the location devices.
"""

import ipaddress
import os
from typing import NamedTuple

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS, transaction
from nautobot.apps.choices import (
    InterfaceTypeChoices,
    PrefixTypeChoices,
    SecretsGroupAccessTypeChoices,
    SecretsGroupSecretTypeChoices,
)
from nautobot.dcim.models import (
    Cable,
    Device,
    DeviceType,
    Interface,
    InterfaceTemplate,
    Location,
    LocationType,
    Manufacturer,
    Platform,
)
from nautobot.extras.models import Role, Secret, SecretsGroup, SecretsGroupAssociation, Status
from nautobot.ipam.models import VRF, IPAddress, Namespace, Prefix, VRFDeviceAssignment, VRFPrefixAssignment

from containerlab_orchestrator.choices import LabStatusChoices, NodeMappingConfigSourceChoices
from containerlab_orchestrator.models import ContainerlabHost, InterfaceMapping, Lab, NodeMapping


class DemoModel(NamedTuple):
    """A device model of the demo data, with its interfaces and its containerlab node mapping."""

    manufacturer: str
    model: str
    platform: str
    network_driver: str
    mgmt_interface: str
    port_prefix: str
    port_ranges: tuple
    """The first port, the last port, and the interface type of each data port range."""

    kind: str
    image: str

    def ports(self):
        """Return the name and the interface type of each data port."""
        return [
            (f"{self.port_prefix}{number}", interface_type)
            for first, last, interface_type in self.port_ranges
            for number in range(first, last + 1)
        ]


class DemoLink(NamedTuple):
    """A cable of the demo data and the /31 prefix of its two ends. End A gets the first address."""

    device_a: str
    port_a: str
    device_b: str
    port_b: str
    network: ipaddress.IPv4Network


LOCATION_TYPE_NAME = "Site"
LOCATION_NAME = "Demo DC1"
NAMESPACE_NAME = "Global"
SPINE_ROLE = "spine"
LEAF_ROLE = "leaf"
SERVER_ROLE = "server"

SPINE = DemoModel(
    "Arista",
    "DCS-7280CR3-32P4",
    "Arista EOS",
    "arista_eos",
    "Management1",
    "Ethernet",
    ((1, 32, InterfaceTypeChoices.TYPE_100GE_QSFP28),),
    "ceos",
    "ceos:4.37.0F",
)
LEAF = DemoModel(
    "Arista",
    "DCS-7050SX3-48YC8",
    "Arista EOS",
    "arista_eos",
    "Management1",
    "Ethernet",
    ((1, 48, InterfaceTypeChoices.TYPE_25GE_SFP28), (49, 56, InterfaceTypeChoices.TYPE_100GE_QSFP28)),
    "ceos",
    "ceos:4.37.0F",
)
SERVER = DemoModel(
    "Generic",
    "Linux server",
    "Linux",
    "linux",
    "eth0",
    "eth",
    ((1, 1, InterfaceTypeChoices.TYPE_25GE_SFP28),),
    "linux",
    "alpine:latest",
)
DEMO_MODELS = (SPINE, LEAF, SERVER)

SPINE_NAMES = ("dc1-spine1", "dc1-spine2")
LEAF_NAMES = ("dc1-leaf1", "dc1-leaf2", "dc1-leaf3", "dc1-leaf4")
SERVER_NAMES = ("dc1-host1", "dc1-host2", "dc1-host3", "dc1-host4")
"""The Linux hosts. Each host connects to the leaf with the same number."""

DEVICE_GROUPS = ((SPINE_ROLE, SPINE, SPINE_NAMES), (LEAF_ROLE, LEAF, LEAF_NAMES), (SERVER_ROLE, SERVER, SERVER_NAMES))
LEAF_FIRST_UPLINK = 49
"""The number of the leaf Ethernet port that connects to the first spine."""

LEAF_SERVER_PORT = "Ethernet1"
SERVER_UPLINK_PORT = "eth1"

MGMT_VRF_NAME = "MGMT"
MGMT_PREFIX = "10.20.20.0/24"
"""The management prefix. The lab uses it as its management subnet, so each primary IP is the containerlab address."""

FIRST_MGMT_OFFSET = 2
"""The offset of the first device address in MGMT_PREFIX. Docker gives the address at offset 1 to the gateway."""

FABRIC_PREFIX = "10.1.0.0/24"
"""The container prefix of the /31 prefixes between the spines and the leafs."""

SERVER_LINK_PREFIX = "10.1.1.0/24"
"""The container prefix of the /31 prefixes between the leafs and the Linux hosts."""

DEMO_PREFIXES = (MGMT_PREFIX, FABRIC_PREFIX, SERVER_LINK_PREFIX)

HOST_NAME = "demo-clab"
HOST_URL_VARIABLE = "CLAB_DEV_HOST_URL"
HOST_URL_DEFAULT = "https://host.docker.internal:8090"
SECRETS_GROUP_NAME = "Demo containerlab credentials"
SECRET_VARIABLES = {
    SecretsGroupSecretTypeChoices.TYPE_USERNAME: "CLAB_DEV_HOST_USERNAME",
    SecretsGroupSecretTypeChoices.TYPE_PASSWORD: "CLAB_DEV_HOST_PASSWORD",
}
"""The environment variable that each demo host secret reads, so that the database holds no secret value."""

LAB_NAME = "demo-spine-leaf"
LAB_MGMT_SUBNET = MGMT_PREFIX


def demo_links():
    """Return the spine to leaf links, then the leaf to Linux host links."""
    fabric = ipaddress.IPv4Network(FABRIC_PREFIX).subnets(new_prefix=31)
    links = [
        DemoLink(spine, f"Ethernet{leaf_number}", leaf, f"Ethernet{LEAF_FIRST_UPLINK + spine_index}", next(fabric))
        for spine_index, spine in enumerate(SPINE_NAMES)
        for leaf_number, leaf in enumerate(LEAF_NAMES, start=1)
    ]
    server_links = ipaddress.IPv4Network(SERVER_LINK_PREFIX).subnets(new_prefix=31)
    links.extend(
        DemoLink(leaf, LEAF_SERVER_PORT, server, SERVER_UPLINK_PORT, next(server_links))
        for leaf, server in zip(LEAF_NAMES, SERVER_NAMES)
    )
    return links


def demo_mgmt_addresses():
    """Return the management address of each device name, in the node order of a lab build."""
    network = ipaddress.IPv4Network(MGMT_PREFIX)
    names = sorted(SPINE_NAMES + LEAF_NAMES + SERVER_NAMES)
    return {
        name: f"{network.network_address + FIRST_MGMT_OFFSET + index}/{network.prefixlen}"
        for index, name in enumerate(names)
    }


class Command(BaseCommand):
    """Load the demo data: one location with an Arista spine and leaf fabric, Linux hosts, and a lab."""

    help = __doc__

    def add_arguments(self, parser):
        """Add command line arguments."""
        parser.add_argument(
            "--database",
            default=DEFAULT_DB_ALIAS,
            help='The database to generate the test data in. Defaults to the "default" database.',
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete the demo data of a previous run before the command loads it again.",
        )

    def handle(self, *args, **options):
        """Load the demo data, and first delete the old demo data if --flush is set."""
        database = options["database"]
        with transaction.atomic(using=database):
            if options["flush"]:
                self.stdout.write("Deleting the existing Containerlab Orchestrator demo data...")
                self._flush(database)
            self._generate_static_data(database)
        self.stdout.write(self.style.SUCCESS(f"Database {database} populated with app data successfully!"))

    def _flush(self, db):
        """Delete the objects that only the demo data uses.

        Keep the shared manufacturers, platforms, and roles. Keep the MGMT VRF if other devices or prefixes use it.
        """
        Lab.objects.using(db).filter(name=LAB_NAME, host__name=HOST_NAME).delete()
        ContainerlabHost.objects.using(db).filter(name=HOST_NAME).delete()
        SecretsGroup.objects.using(db).filter(name=SECRETS_GROUP_NAME).delete()
        Secret.objects.using(db).filter(name__startswith=f"{SECRETS_GROUP_NAME} ").delete()
        demo_device_types = DeviceType.objects.using(db).none()
        for demo_model in DEMO_MODELS:
            demo_device_types |= DeviceType.objects.using(db).filter(
                manufacturer__name=demo_model.manufacturer, model=demo_model.model
            )
        NodeMapping.objects.using(db).filter(device_type__in=demo_device_types).delete()
        demo_devices = Device.objects.using(db).filter(location__name=LOCATION_NAME)
        cable_ids = (
            Interface.objects.using(db)
            .filter(device__in=demo_devices)
            .values_list("cable_termination__cable", flat=True)
        )
        Cable.objects.using(db).filter(pk__in=cable_ids).delete()
        namespace = Namespace.objects.using(db).get(name=NAMESPACE_NAME)
        for demo_prefix in DEMO_PREFIXES:
            IPAddress.objects.using(db).filter(parent__namespace=namespace).net_host_contained(demo_prefix).delete()
        demo_devices.delete()
        prefixes = Prefix.objects.using(db).none()
        for demo_prefix in DEMO_PREFIXES:
            prefixes |= Prefix.objects.using(db).filter(namespace=namespace).net_contained_or_equal(demo_prefix)
        for prefix in prefixes.order_by("-prefix_length"):
            prefix.delete()
        VRF.objects.using(db).filter(
            name=MGMT_VRF_NAME, namespace=namespace, devices__isnull=True, prefixes__isnull=True
        ).delete()
        demo_device_types.filter(devices__isnull=True).delete()
        Location.objects.using(db).filter(name=LOCATION_NAME, devices__isnull=True).delete()

    def _generate_static_data(self, db):
        """Create each demo object that does not exist. A second run changes nothing."""
        device_content_type = ContentType.objects.db_manager(db).get_for_model(Device)
        active = Status.objects.using(db).get_for_model(Device).get(name="Active")
        location = self._location(db, device_content_type, active)
        devices = {}
        for role_name, demo_model, names in DEVICE_GROUPS:
            device_type, platform = self._device_type(db, demo_model)
            role, _ = Role.objects.using(db).get_or_create(name=role_name)
            role.content_types.add(device_content_type)
            for name in names:
                devices[name], _ = Device.objects.using(db).get_or_create(
                    name=name,
                    location=location,
                    defaults={"device_type": device_type, "role": role, "platform": platform, "status": active},
                )
        namespace = Namespace.objects.using(db).get(name=NAMESPACE_NAME)
        self._address_links(db, devices, namespace, location)
        self._address_management(db, devices, namespace, location)
        self._lab(db, self._host(db))

    def _location(self, db, device_content_type, status):
        """Return the demo location, and create it and its location type if they do not exist.

        The location type permits devices and prefixes.
        """
        location_type, _ = LocationType.objects.using(db).get_or_create(name=LOCATION_TYPE_NAME)
        location_type.content_types.add(device_content_type, ContentType.objects.db_manager(db).get_for_model(Prefix))
        location, _ = Location.objects.using(db).get_or_create(
            name=LOCATION_NAME, defaults={"location_type": location_type, "status": status}
        )
        return location

    def _device_type(self, db, demo_model):
        """Return the device type and the platform of the model, with the interface templates and the node mapping."""
        manufacturer, _ = Manufacturer.objects.using(db).get_or_create(name=demo_model.manufacturer)
        platform, _ = Platform.objects.using(db).get_or_create(
            name=demo_model.platform,
            defaults={"manufacturer": manufacturer, "network_driver": demo_model.network_driver},
        )
        device_type, created = DeviceType.objects.using(db).get_or_create(
            manufacturer=manufacturer, model=demo_model.model
        )
        if created:
            templates = [
                InterfaceTemplate(
                    device_type=device_type,
                    name=demo_model.mgmt_interface,
                    type=InterfaceTypeChoices.TYPE_1GE_FIXED,
                    mgmt_only=True,
                )
            ]
            templates.extend(
                InterfaceTemplate(device_type=device_type, name=name, type=interface_type)
                for name, interface_type in demo_model.ports()
            )
            InterfaceTemplate.objects.using(db).bulk_create(templates)
        node_mapping, created = NodeMapping.objects.using(db).get_or_create(
            device_type=device_type,
            defaults={
                "kind": demo_model.kind,
                "image": demo_model.image,
                "config_source": NodeMappingConfigSourceChoices.NONE,
            },
        )
        if created:
            InterfaceMapping.objects.using(db).bulk_create(
                InterfaceMapping(
                    node_mapping=node_mapping,
                    physical_name=name,
                    container_name=f"eth{name.removeprefix(demo_model.port_prefix)}",
                )
                for name, _ in demo_model.ports()
            )
        return device_type, platform

    def _prefix(self, db, prefix, namespace, location, prefix_type):
        """Return the prefix of the location, and create it if it does not exist."""
        active = Status.objects.using(db).get_for_model(Prefix).get(name="Active")
        saved, _ = Prefix.objects.using(db).get_or_create(
            prefix=prefix, namespace=namespace, defaults={"status": active, "type": prefix_type}
        )
        saved.locations.add(location)
        return saved

    def _assign_address(self, db, interface, address, namespace):
        """Return the IP address, and assign it to the interface if it is not assigned."""
        active = Status.objects.using(db).get_for_model(IPAddress).get(name="Active")
        ip_address, _ = IPAddress.objects.using(db).get_or_create(
            address=address, namespace=namespace, defaults={"status": active}
        )
        if not interface.ip_addresses.filter(pk=ip_address.pk).exists():
            interface.add_ip_addresses(ip_address)
        return ip_address

    def _address_links(self, db, devices, namespace, location):
        """Cable each demo link, and give its two ends the two addresses of its /31 prefix."""
        connected = Status.objects.using(db).get_for_model(Cable).get(name="Connected")
        for container in (FABRIC_PREFIX, SERVER_LINK_PREFIX):
            self._prefix(db, container, namespace, location, PrefixTypeChoices.TYPE_CONTAINER)
        for link in demo_links():
            port_a = devices[link.device_a].interfaces.get(name=link.port_a)
            port_b = devices[link.device_b].interfaces.get(name=link.port_b)
            if port_a.cable is None and port_b.cable is None:
                Cable(termination_a=port_a, termination_b=port_b, status=connected).validated_save()
            self._prefix(db, str(link.network), namespace, location, PrefixTypeChoices.TYPE_NETWORK)
            address_a, address_b = (f"{host}/{link.network.prefixlen}" for host in link.network)
            self._assign_address(db, port_a, address_a, namespace)
            self._assign_address(db, port_b, address_b, namespace)

    def _address_management(self, db, devices, namespace, location):
        """Put each management interface in the MGMT VRF, and make its address the primary IPv4 of the device."""
        vrf, _ = VRF.objects.using(db).get_or_create(name=MGMT_VRF_NAME, namespace=namespace)
        mgmt_prefix = self._prefix(db, MGMT_PREFIX, namespace, location, PrefixTypeChoices.TYPE_NETWORK)
        VRFPrefixAssignment.objects.using(db).get_or_create(vrf=vrf, prefix=mgmt_prefix)
        for name, address in demo_mgmt_addresses().items():
            device = devices[name]
            VRFDeviceAssignment.objects.using(db).get_or_create(vrf=vrf, device=device)
            mgmt_interface = device.interfaces.get(mgmt_only=True)
            if mgmt_interface.vrf_id != vrf.pk:
                mgmt_interface.vrf = vrf
                mgmt_interface.validated_save()
            ip_address = self._assign_address(db, mgmt_interface, address, namespace)
            if device.primary_ip4_id != ip_address.pk:
                device.primary_ip4 = ip_address
                device.validated_save()

    def _host(self, db):
        """Return the demo containerlab host. Its secrets read the CLAB_DEV_HOST_* variables of creds.env."""
        secrets_group, _ = SecretsGroup.objects.using(db).get_or_create(name=SECRETS_GROUP_NAME)
        for secret_type, variable in SECRET_VARIABLES.items():
            secret, _ = Secret.objects.using(db).get_or_create(
                name=f"{SECRETS_GROUP_NAME} {secret_type}",
                defaults={"provider": "environment-variable", "parameters": {"variable": variable}},
            )
            SecretsGroupAssociation.objects.using(db).get_or_create(
                secrets_group=secrets_group,
                access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
                secret_type=secret_type,
                defaults={"secret": secret},
            )
        host, _ = ContainerlabHost.objects.using(db).get_or_create(
            name=HOST_NAME,
            defaults={
                "remote_url": os.environ.get(HOST_URL_VARIABLE, HOST_URL_DEFAULT),
                "verify_ssl": False,
                "secrets_group": secrets_group,
                "description": "The containerlab host of the demo data",
            },
        )
        return host

    def _lab(self, db, host):
        """Create the demo lab in the Planned status, if it does not exist. It selects the devices of the location."""
        if Lab.objects.using(db).filter(name=LAB_NAME, host=host).exists():
            return
        lab = Lab(
            name=LAB_NAME,
            host=host,
            owner=get_user_model().objects.db_manager(db).filter(is_superuser=True).order_by("date_joined").first(),
            status=Status.objects.using(db).get_for_model(Lab).get(name=LabStatusChoices.PLANNED),
            device_filter={"location": [LOCATION_NAME]},
            mgmt_subnet=LAB_MGMT_SUBNET,
            config_source=NodeMappingConfigSourceChoices.NONE,
        )
        lab.validated_save()
