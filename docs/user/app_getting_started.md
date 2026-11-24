# Getting Started with the App

This document provides a step-by-step tutorial on how to get the App going and how to use it.

The tutorial starts from a Nautobot with no data. It deploys, inspects, and destroys a lab of two Nokia SR Linux nodes
and one `linux` node. Use a superuser account for each step.

## First steps with the App

### Step 1: Install the app

Install the app. Add it to `PLUGINS`. See [Install Guide](../admin/install.md#install-guide). The default
[App Configuration](../admin/install.md#app-configuration) is correct for this tutorial.

### Step 2: Add a containerlab host

1. Set up a Linux server with Docker as a containerlab host. See
   [Set up a containerlab host](../admin/install.md#set-up-a-containerlab-host).
2. On the host, pull the two images of the tutorial. The Deploy Lab job does not pull images.

    ```shell
    docker pull ghcr.io/nokia/srlinux:24.10.1
    docker pull alpine:latest
    ```

3. Add the host in Nautobot with the name `local-clab`. See
   [Add a host in Nautobot](../admin/install.md#add-a-host-in-nautobot).
4. Do the [login check](../admin/install.md#check-the-login) from each worker server.

### Step 3: Make a node mapping

1. Go to **Devices > Manufacturers**. Add the manufacturer `Nokia`.
2. Go to **Devices > Platforms**. Add the platform `Nokia SR Linux` with the manufacturer `Nokia`.
3. Go to **Containerlab > Node Mappings**. Add a node mapping with the values of the table below. See
   [Add a node mapping](app_use_cases.md#add-a-node-mapping) and [Nokia SR Linux](app_use_cases.md#nokia-sr-linux).
4. Put the two interface rows in "Interface Mappings". Click "Add another interface" for the second row.
5. Click **Create**.

| Field or physical name | Value or container name |
| --- | --- |
| Platform | `Nokia SR Linux` |
| Kind | `nokia_srlinux` |
| Image | `ghcr.io/nokia/srlinux:24.10.1` |
| `ethernet-1/1` | `ethernet-1/1` |
| `ethernet-1/2` | `ethernet-1/2` |

### Step 4: Make the devices and the cables

1. Make the objects that a device needs: a Location Type for devices, a Location, and a Role for devices.
2. Add the manufacturer `Generic`.
3. Add the Device Type `7220 IXR-D2L` with the manufacturer `Nokia`.
4. Add the Device Type `Linux server` with the manufacturer `Generic`.
5. Make these three devices, each with the status Active and with the interfaces of the table.

    | Device | Device Type | Platform | Interfaces |
    | --- | --- | --- | --- |
    | `srl1` | `7220 IXR-D2L` | `Nokia SR Linux` | `ethernet-1/1`, `ethernet-1/2` |
    | `srl2` | `7220 IXR-D2L` | `Nokia SR Linux` | `ethernet-1/1`, `ethernet-1/2` |
    | `linux1` | `Linux server` | (none) | `eth1` |

6. Connect `srl1` `ethernet-1/1` to `srl2` `ethernet-1/1` with a cable.
7. Connect `srl1` `ethernet-1/2` to `linux1` `eth1` with a cable.

`linux1` has no platform, so no node mapping matches it. The builder uses the
[default mapping](app_use_cases.md#the-default-mapping), the kind `linux` with the image `alpine:latest`.

### Step 5: Make the lab

1. Go to **Containerlab > Labs**. Add a lab.
2. Enter the name `nbdocs-demo`. Select the host `local-clab`.
3. In "Devices", select `srl1`, `srl2`, and `linux1`.
4. Enter the management subnet `10.250.250.0/24`. Use a subnet that no network on the host uses.
5. Click **Create**.

The lab page shows you as the owner, and the status Planned. See [Make a lab](app_use_cases.md#make-a-lab).

### Step 6: Find the job buttons

The app turns on each of its jobs when you install the app. So the lab page shows the buttons Build, Deploy,
Destroy, and Inspect. See [Start a lab job from the lab page](app_use_cases.md#start-a-lab-job-from-the-lab-page).

If a button is not there, an administrator turned its job off, or you cannot run the job. See
[Who sees a button](app_use_cases.md#who-sees-a-button).

### Step 7: Build the lab

1. On the lab page, click **Build**. The run form of the job opens with the lab selected.
2. Click **Run Job Now**.
3. Wait until the job result shows that the job completed.
4. The job log has a warning for `linux1` `eth1`, because the default mapping has no interface table. The node uses
   `eth1`. See [Fallback names](app_use_cases.md#fallback-names).
5. Open the lab page. The status is Built.
6. Open the "Topology YAML" tab. It shows the three nodes, their management addresses from `10.250.250.2`, and the two
   links. See [Review and download the YAML](app_use_cases.md#review-and-download-the-yaml).
7. Open the "Topology" tab. Each node is grey, "Not inspected". See
   [View the topology graph](app_use_cases.md#view-the-topology-graph).

A device of the lab can have a cable to a device that is not in the lab. The build then adds a stub node for that
device. See [Stub nodes](app_use_cases.md#stub-nodes).

### Step 8: Deploy and inspect the lab

1. On the lab page, click **Deploy**. Keep "Replace a lab that exists on the host" off.
2. Click **Run Job Now**.
3. Wait until the job completes. The lab status is Deployed.
4. Open the lab page. Click **Inspect**. Then click **Run Job Now**.
5. Open the lab page again. In the Nodes table, each node has the state `running`.
6. Open the "Topology" tab. Each node is green, "Running".

See [Deploy a lab](app_use_cases.md#deploy-a-lab) and [Inspect a lab](app_use_cases.md#inspect-a-lab).

### Step 9: Destroy the lab

1. On the lab page, click **Destroy**. Read the WARNING. Then confirm it.
2. Click **Run Job Now**.
3. Wait until the job completes.
4. Open the lab page. The status is Destroyed, and the nodes have no state.

See [Destroy a lab](app_use_cases.md#destroy-a-lab).

## What are the next steps?

- Add a node mapping for each other device model. See [Examples](app_use_cases.md#examples).
- Select the devices of a lab with a Dynamic Group or a device filter. See
  [Select the devices](app_use_cases.md#select-the-devices).
- Give each node a startup config. See [Startup configs](app_use_cases.md#startup-configs).
- Schedule the Sync Labs job, so that each lab page shows the current node state. See
  [Sync all labs](app_use_cases.md#sync-all-labs).

You can check out the [Use Cases](app_use_cases.md) section for more examples.
