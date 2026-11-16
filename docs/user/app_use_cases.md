# Using the App

This document describes common use-cases and scenarios for this App.

## General Usage

## Use-cases and common workflows

### Startup configs

The topology builder can give each lab node a startup config from Nautobot data. The Build job writes the config
into the stored topology YAML. Stub nodes get no startup config.

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
| Golden Config intended, Golden Config backup | Not available yet. The build gives a warning, and the node gets no startup config. |

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
writes the new config, and a change that you make in the running lab is lost on redeploy. There are two exceptions:

- A `cisco_iol` node keeps its NVRAM file when it exists, so it boots with its saved config.
- A `nokia_sros` node applies the partial config only when the lab directory has no saved config.

#### Who can see a startup config

The startup config text is in the topology YAML of the lab. The app leaves the topology YAML out of the change log,
the webhook payloads, and the job hook payloads. Each user who can view the lab can see it in the REST API, GraphQL,
export templates, and custom links. containerlab also writes a copy of the text to the disk of the containerlab host.

## Screenshots

!!! warning "Developer Note - Remove Me!"
    Ideally captures every view exposed by the App. Should include a relevant dataset.
