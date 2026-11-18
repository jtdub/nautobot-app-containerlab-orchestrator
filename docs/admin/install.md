# Installing the App in Nautobot

Here you will find detailed instructions on how to **install** and **configure** the App within your Nautobot environment.

!!! warning "Developer Note - Remove Me!"
    Detailed instructions on installing the App. You will need to update this section based on any additional dependencies or prerequisites.

## Prerequisites

- The app is compatible with Nautobot 3.1.0 and higher.
- Databases supported: PostgreSQL, MySQL

!!! note
    Please check the [dedicated page](compatibility_matrix.md) for a full compatibility matrix and the deprecation policy.

### Access Requirements

!!! warning "Developer Note - Remove Me!"
    What external systems (if any) it needs access to in order to work.

## Install Guide

!!! note
    Apps can be installed from the [Python Package Index](https://pypi.org/) or locally. See the [Nautobot documentation](https://docs.nautobot.com/projects/core/en/stable/user-guide/administration/installation/app-install/) for more details. The pip package name for this app is [`containerlab-orchestrator`](https://pypi.org/project/containerlab-orchestrator/).

The app is available as a Python package via PyPI and can be installed with `pip`:

```shell
pip install containerlab-orchestrator
```

To ensure Containerlab Orchestrator is automatically re-installed during future upgrades, create a file named `local_requirements.txt` (if not already existing) in the Nautobot root directory (alongside `requirements.txt`) and list the `containerlab-orchestrator` package:

```shell
echo containerlab-orchestrator >> local_requirements.txt
```

To use the Golden Config startup config sources, install the `golden-config` extra. It installs Nautobot Golden
Config and Nautobot Plugin Nornir:

```shell
pip install "containerlab-orchestrator[golden-config]"
```

Once installed, the app needs to be enabled in your Nautobot configuration. The following block of code below shows the additional configuration required to be added to your `nautobot_config.py` file:

- Append `"containerlab_orchestrator"` to the `PLUGINS` list.
- Append the `"containerlab_orchestrator"` dictionary to the `PLUGINS_CONFIG` dictionary and override any defaults.

```python
# In your nautobot_config.py
PLUGINS = ["containerlab_orchestrator"]

PLUGINS_CONFIG = {
    "containerlab_orchestrator": {
        "default_node_mapping": {"kind": "linux", "image": "alpine:latest"},
        "max_lab_devices": 100,
        "request_timeout": 300,
        "stub_node_image": "alpine:latest",
    }
}
```

With the `golden-config` extra, also add `"nautobot_plugin_nornir"` and `"nautobot_golden_config"` to the `PLUGINS`
list. Golden Config needs the Nornir app:

```python
PLUGINS = ["containerlab_orchestrator", "nautobot_plugin_nornir", "nautobot_golden_config"]
```

Once the Nautobot configuration is updated, run the Post Upgrade command (`nautobot-server post_upgrade`) to run migrations and clear any cache:

```shell
nautobot-server post_upgrade
```

Then restart (if necessary) the Nautobot services which may include:

- Nautobot
- Nautobot Workers
- Nautobot Scheduler

```shell
sudo systemctl restart nautobot nautobot-worker nautobot-scheduler
```

## App Configuration

Use these settings to control the app:

| Key | Example | Default | Description |
| --- | ------- | ------- | ----------- |
| `default_node_mapping` | `{"kind": "nokia_srlinux", "image": "ghcr.io/nokia/srlinux:latest"}` | `{"kind": "linux", "image": "alpine:latest"}` | The containerlab node kind and image that the app uses when no NodeMapping matches a device. When you set this value, you must give both `kind` and `image`. |
| `max_lab_devices` | `20` | `100` | The largest number of devices that a lab can select. A build of a lab that selects more devices fails. Stub nodes do not count. The value is an integer of 1 or more. |
| `request_timeout` | `60` | `300` | The timeout in seconds of each call to a clab-api-server host. The value is an integer of 1 or more. A deploy sends this value, up to 7200, as the containerlab timeout, and waits up to that value plus 360 seconds. A destroy also sends this value, up to 7200, as the containerlab timeout, and waits up to that value plus 480 seconds. If you set more than 300, raise the time limits of the Deploy Lab, Destroy Lab, and Redeploy Lab jobs. |
| `stub_node_image` | `"registry.local/alpine:3"` | `"alpine:latest"` | The image of the stub nodes. A stub node is a `linux` node that stands for a device outside the lab, at the far end of a cable. The value must have no space, no `$`, and no `{{`. |
