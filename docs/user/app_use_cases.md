# Using the App

This document describes common use-cases and scenarios for this App.

## General Usage

### Review and download the YAML

After a build, the lab page has a "Topology YAML" tab. The tab shows the stored topology YAML as text. The "Download
YAML" button on the tab saves the file as `<lab>.clab.yml`. A lab with no build shows "This lab has no topology. Run
the Build Lab Topology job." and no button.

The tab shows a YAML of 1,000,000 characters or less. For a larger YAML, the tab shows the size and the button. Use
the button to get the file.

Each user with the view permission on the lab can read and download the YAML. The REST API gives the same file:

```bash
curl -H "Authorization: Token $TOKEN" -OJ "https://<nautobot>/api/plugins/containerlab-orchestrator/labs/<id>/topology/"
```

| Response | Cause |
|---|---|
| 200, `Content-Type: application/yaml` | The lab has a topology YAML. |
| 404, `Lab <lab> has no topology. Run the Build Lab Topology job first.` | The lab has no build. |
| 404, with other text | The user cannot view the lab, or the URL has `?format=yaml` or `?format=csv`. |
| 403 | The user has no `containerlab_orchestrator.view_lab` permission. |
| 406 | The `Accept` header allows only `application/yaml` or `text/csv`. Send no `Accept` header, or `*/*`. |

The labs REST API does not return `topology_yaml`, because the text can be large. Use the topology URL above.

## Use-cases and common workflows

### Startup configs

The topology builder can give each lab node a startup config from Nautobot data. The topology builder makes the
config. The Build Lab Topology job writes it into the stored topology YAML. Stub nodes get no startup config.

#### Config source

The builder finds the config source of each node in this order:

1. The config source of the node mapping of the device, if it is set.
2. The default config source of the lab.
3. None, if the lab has no default config source.

A device with no node mapping uses the default mapping, which has no config source. So the lab default applies.

| Config source | Result |
|---|---|
| None (kind default) | The node gets no startup config. containerlab uses the default config of the kind. |
| Jinja2 template | The builder renders the config template of the node mapping. |
| Golden Config intended, Golden Config backup | The builder reads the intended or the backup config of the device from Golden Config, then renames the interfaces. |

The build continues when a node gets no config because of a problem. The build gives one warning for the node. A
missing template, a template error, and an empty output each give a warning.

#### Jinja2 template

The template gets these variables:

- `device`: the Nautobot device of the node.
- `lab`: the lab.
- `interface_map`: a dict of each physical interface name to its container interface name. It holds each row of the
  interface table of the node mapping. It also holds the name that the builder gave to each linked interface, for
  example a fallback `eth3`.

The render does not HTML-escape a value. So `a&b<c>` stays `a&b<c>`.

!!! warning
    A template can read Nautobot data. Each user who can view the lab can see the result. Give the change permission
    on node mappings only to users that you trust.

#### Golden Config

The Golden Config sources need the `golden-config` extra and the apps `nautobot_plugin_nornir` and
`nautobot_golden_config` in `PLUGINS` (see the install guide). If Golden Config is not installed, a node with a
Golden Config source gets a warning and no startup config.

If Golden Config is not installed, the node mapping and lab forms do not show the Golden Config sources, and the
REST API rejects a new Golden Config source. A record that already has a Golden Config source keeps it:

- The edit form shows the source as "Golden Config intended (Golden Config not installed)" or "Golden Config backup
  (Golden Config not installed)". A save does not change it.
- A save of another field passes in the forms, in a bulk edit, and in the REST API.
- To stop the warning, select another source.

The builder reads the Golden Config record of the device with the permissions of the build user. These cases give a
warning and no startup config:

- The device has no Golden Config record, or the build user cannot view the record.
- The intended or the backup config of the record is empty, or has only spaces.

#### Interface rename

A Golden Config text uses the physical interface names of the device. The builder changes each name that has a row in
the interface table of the node mapping to the `container_name` of the row. These are the rules:

- The builder uses the rows of the interface table only. A linked interface with no row gets a warning, and its name
  in the text does not change.
- A name changes only as a whole word. `GigabitEthernet0/0/1` does not change in `GigabitEthernet0/0/10`,
  `TenGigabitEthernet0/0/1`, or `GigabitEthernet0/0/1/2`.
- A subinterface keeps its number. `Gi0/0/1.100` becomes `<container_name>.100`.
- The builder also changes the short form of each name. The short forms are `Gi` (`GigabitEthernet`), `Te`
  (`TenGigabitEthernet`, `TenGigE`), `Fa` (`FastEthernet`), `Et` (`Ethernet`), `Hu` (`HundredGigabitEthernet`,
  `HundredGigE`), and `Po` (`Port-channel`).
- If two names give the same short form, for example `TenGigabitEthernet0/1` and `TenGigE0/1`, the builder does not
  change that short form. If a short form is also a name in the table, the row of that name applies.
- The longest name matches first. The builder changes all names in one pass, so a new name does not change again.
- The match is case-sensitive. `gigabitethernet0/0/1` does not change.
- The rename does not change an interface range correctly. In `Gi0/1-Gi0/4`, only the first name changes, so the
  text becomes `eth1-Gi0/4`. Write each interface on its own line in the Golden Config text.

The node OS must accept the new name. containerlab v0.56.0 and later accept the interface alias of the kind as a
link name, for example `Ethernet1` for `ceos`. Put that alias in the `container_name` column, so the config gets a
name that the node OS uses.

The Jinja2 source does not rename. A template uses `interface_map` directly.

#### What the stored YAML shows

containerlab changes the text of a startup config before the node gets it. It replaces a `$` variable with a value
from the host environment, and it runs a `{{` action as a Go template. Some kinds do this two times. So the builder
escapes each `$` and `{{` for the kind of the node, and the node gets the text unchanged. Each `$` becomes `$$` or
more, and each `{{` becomes ``{{`{{`}}`` or more. Do not edit the escaped text by hand.

| Kinds | `$` in the stored YAML | Template layers of `{{` |
|---|---|---|
| `cisco_vios`, `arrcus_arcos`, `6wind_vsr` | `$$` | 1 |
| `cisco_iol` | `$$` | 2 |
| `nokia_srlinux` and `srl` with a CLI config, and each other kind | `$$$$` | 2 |
| `nokia_srlinux` and `srl` with a JSON config, `nokia_srsim` | `$$$$$$$$` | 3 |

The builder adds a final newline to each config. So containerlab always reads the text as a config, and never as a
path on the host or a URL.

containerlab also replaces `__clabNodeName__`, `__clabDir__`, `__clabNodeDir__`, and `__clabLabName__` in the text.
The builder does not escape these names.

#### Full and partial configs

For most kinds, the startup config replaces the default config of the kind. The kinds `cisco_iol`, `cisco_vios`,
`nokia_srsim`, `nokia_sros`, and `ciena_saos` read the text as a partial config. containerlab adds a partial config
to the default config of the kind.

#### Redeploy

The builder writes `enforce-startup-config: true` on each node with a startup config. So a deploy of a new build
writes the new config, and a redeploy removes a change that you make in the running lab. There are two exceptions:

- A `cisco_iol` node keeps its NVRAM file when it exists, so it boots with its saved config.
- A `nokia_sros` node applies the partial config only when the lab directory has no saved config.

#### Who can see a startup config

The startup config text is in the topology YAML of the lab. A Golden Config text can hold secrets of the device.
The app leaves the topology YAML out of the change log, the webhook payloads, the job hook payloads, and the labs
REST API. Each user who can view the lab can see it on the Topology YAML tab, in the topology download, GraphQL,
export templates, and custom links. containerlab also
writes a copy of the text to the disk of the containerlab host.

## Screenshots

!!! warning "Developer Note - Remove Me!"
    Ideally captures every view exposed by the App. Should include a relevant dataset.
