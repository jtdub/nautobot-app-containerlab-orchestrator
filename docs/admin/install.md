# Installing the App in Nautobot

Here you will find detailed instructions on how to **install** and **configure** the App within your Nautobot environment.

## Prerequisites

- The app is compatible with Nautobot 3.1.0 and higher.
- Databases supported: PostgreSQL, MySQL

!!! note
    Please check the [dedicated page](compatibility_matrix.md) for a full compatibility matrix and the deprecation policy.

### Access Requirements

- Each Nautobot worker server needs HTTPS or HTTP access to the URL of each containerlab host. The app jobs make all
  the calls to the hosts, and the jobs run on the workers.
- The app needs no SSH access. It uses only the clab-api-server REST API of each host.
- The calls use the `HTTP_PROXIES` setting of Nautobot only. The `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY`
  environment variables have no effect on them.

[External Interactions](../user/external_interactions.md) lists each call.

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

To install a version that is not on PyPI, install the app from Git. Give the SHA of a commit, because a branch
changes. The installed app shows the version `0.1.0a0`, and the package does not include the built docs of the app.

```shell
pip install "git+https://github.com/jtdub/nautobot-app-containerlab-orchestrator@<commit SHA>"
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
| `request_timeout` | `60` | `300` | The timeout in seconds of each call to a clab-api-server host. The value is an integer of 1 or more. A deploy sends this value, up to 7200, as the containerlab timeout, and waits up to that value plus 360 seconds. A destroy also sends this value, up to 7200, as the containerlab timeout, and waits up to that value plus 480 seconds. An inspect waits up to this value. If you set more than 300, raise the time limits of the Deploy Lab, Destroy Lab, Redeploy Lab, Inspect Lab, and Sync Labs jobs. |
| `stub_node_image` | `"registry.local/alpine:3"` | `"alpine:latest"` | The image of the stub nodes. A stub node is a `linux` node that stands for a device outside the lab, at the far end of a cable. The value must have no space, no `$`, and no `{{`. |

!!! warning ""
    WARNING: The host lets a clab-api-server superuser remove the lab of each server user. Destroy Lab and Redeploy
    Lab can then remove a lab of another server user. Do not put a clab-api-server superuser in the Secrets Group of
    a host. Inspect Lab fails for a lab of another server user, but it does not stop the other jobs.

## Set up a containerlab host

A containerlab host is a Linux server with Docker that runs clab-api-server. clab-api-server has containerlab built
in. The commands below install the tested version. The [Compatibility Matrix](compatibility_matrix.md) gives the
tested versions.

Use one of the two methods below. Each method serves the API on port 8090 with HTTPS.

### Method 1: systemd service

1. Run the installer as root. The installer writes the server binary, the file
   `/etc/clab-api-server/clab-api-server.env` with a random `JWT_SECRET`, and a systemd service that runs as root.
   It also makes the groups `clab_api` and `clab_admins`.

    ```shell
    curl -fsSL https://raw.githubusercontent.com/srl-labs/clab-api-server/v0.10.0/install.sh \
      | sudo bash -s -- install --version v0.10.0
    ```

2. In `/etc/clab-api-server/clab-api-server.env`, set `API_SERVER_HOST` to the host name of the URL that Nautobot
   uses. The server puts this name in its certificate.

    ```shell
    sudoedit /etc/clab-api-server/clab-api-server.env
    ```

3. Make a Linux account for the app, and put it in the group `clab_api`. The server signs in with the Linux
   accounts of the host.

    ```shell
    sudo useradd -m -G clab_api nautobot
    sudo passwd nautobot
    ```

4. Start the service.

    ```shell
    sudo systemctl enable --now clab-api-server
    sudo systemctl status clab-api-server
    ```

After you change the env file, run `sudo systemctl restart clab-api-server`.

### Method 2: container

1. Make the group `clab_api` and a Linux account for the app. The container uses the accounts and the groups of the
   host. The image does not make the group `clab_api`.

    ```shell
    sudo groupadd clab_api
    sudo useradd -m -G clab_api nautobot
    sudo passwd nautobot
    ```

2. Start the container. Set `API_SERVER_HOST` to the host name of the URL that Nautobot uses. Set `JWT_SECRET` to a
   random value, because the image has a public example value.

    ```shell
    docker run -d \
      --name clab-api-server \
      --privileged \
      --network host \
      --pid host \
      -e API_SERVER_HOST=clab01.example.com \
      -e JWT_SECRET="$(openssl rand -hex 32)" \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -v /var/run/netns:/var/run/netns \
      -v /var/lib/docker/containers:/var/lib/docker/containers \
      -v /etc/passwd:/etc/passwd:ro \
      -v /etc/shadow:/etc/shadow:ro \
      -v /etc/group:/etc/group:ro \
      -v /etc/gshadow:/etc/gshadow:ro \
      -v /home:/home \
      -v /var/lib/clab-api-server:/var/lib/clab-api-server \
      ghcr.io/srl-labs/clab-api-server/clab-api-server:v0.10.0
    ```

### The host account

Put the account of the app in `clab_api` only. Do not put it in `clab_admins`. A member of `clab_admins` is a
clab-api-server superuser. Read the WARNING at the end of [App Configuration](#app-configuration).

### TLS

Both methods use a self-signed certificate that the server makes. The certificate holds only `localhost`,
`127.0.0.1`, `::1`, the host name of the server, and `API_SERVER_HOST`.

The app has no setting for a CA file. Each host has the "Verify SSL" setting only. The `REQUESTS_CA_BUNDLE` and
`CURL_CA_BUNDLE` environment variables have no effect on the app.

!!! warning ""
    WARNING: With "Verify SSL" off, the app sends the password of the host account over TLS, but it does not check
    the certificate of the server. A system that sits between Nautobot and the host can read the password. Turn
    "Verify SSL" off only on a network that you trust.

Use one of these two choices:

- Use a certificate from a CA that the `certifi` CA bundle of the Python environment of Nautobot trusts. The
  certificate must hold the host name of the URL. Set `TLS_CERT_FILE` and `TLS_KEY_FILE` to the certificate file
  and the key file. Keep "Verify SSL" on. The CA store of the operating system does not apply to the app.
- Use the self-signed certificate, and turn "Verify SSL" off for the host.

For Method 2, mount the folder of the certificate into the container. Then add `-e TLS_CERT_FILE=<path>` and
`-e TLS_KEY_FILE=<path>` to the `docker run` command. The server does not start when it cannot find a file.

## Add a host in Nautobot

### Permissions

A user needs these permissions to add a host:

| Task | Permissions |
| --- | --- |
| Add a Secret | `extras.add_secret` |
| Add a Secrets Group with its Secrets | `extras.add_secretsgroup`, `extras.view_secret` |
| See the hosts | `containerlab_orchestrator.view_containerlabhost` |
| Add a host | `containerlab_orchestrator.add_containerlabhost`, `extras.view_secretsgroup` |
| Change a host | `containerlab_orchestrator.change_containerlabhost`, `extras.view_secretsgroup` |
| Delete a host | `containerlab_orchestrator.delete_containerlabhost` |

### Steps

1. Go to **Secrets > Secrets**. Add a Secret for the username of the host account, and a Secret for its password.
   Use a secrets provider that your Nautobot has.
2. Go to **Secrets > Secrets Groups**, and add a Secrets Group.
3. In the group, add the username Secret with the access type "HTTP(S)" and the secret type "Username".
4. Add the password Secret with the access type "HTTP(S)" and the secret type "Password".
5. Go to **Containerlab > Containerlab Hosts**, and add a host.
6. Set the URL to the address of the server, with the port 8090, for example `https://clab01.example.com:8090`.
7. Set "Verify SSL" as the [TLS](#tls) section tells you.
8. In "Secrets group", select the Secrets Group. Save the host.

The URL must start with `http://` or `https://`. It must not hold a username or a password. When the group does not
have the two Secrets, the form shows one of these messages:

- "The Secrets Group must have an HTTP(S) username secret and an HTTP(S) password secret."
- "The Secrets Group must have an HTTP(S) username secret."
- "The Secrets Group must have an HTTP(S) password secret."

### Check the login

Run this command on each Nautobot worker server. Use the URL, the username, and the password of the host. Add `-k`
only when "Verify SSL" is off.

```shell
curl -X POST https://clab01.example.com:8090/login \
  -H "Content-Type: application/json" \
  -d '{"username": "nautobot", "password": "<password>"}'
```

The result is JSON with a `token` key. The jobs use the same login.
