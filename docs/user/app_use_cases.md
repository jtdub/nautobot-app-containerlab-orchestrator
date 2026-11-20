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

### Build a lab

The Build Lab Topology job builds the containerlab topology of a lab from Nautobot data. It stores the topology YAML,
the build time, and one node row for each node on the lab.

#### Turn on the job

Nautobot adds the job turned off. An administrator does these steps one time:

1. Open Jobs > Jobs, and edit the job "Build Lab Topology" in the group "Containerlab Orchestrator".
2. Select "Enabled", and save the job.
3. Give each user who builds labs the permissions in the table below.

| Permission | Reason |
|---|---|
| `extras.view_job` | The user finds the job under Jobs > Jobs. |
| `extras.run_job` | The user runs the job. |
| `extras.view_jobresult` | The user opens the job result. |
| `extras.view_joblogentry` | The user reads the job log, with the warnings and the errors. |
| `containerlab_orchestrator.view_lab` | The user selects the lab, and the job checks it. |
| `containerlab_orchestrator.change_lab` | The job checks it before the build. |
| `containerlab_orchestrator.view_labnode` | The user sees the node rows on the lab page. |
| `dcim.view_device` | The build uses only the devices that the user can view. |

The user must also be the owner of the lab, or a superuser.

#### Who can build a lab

The job checks the job user in this order. The first check that fails stops the job, and the lab does not change.

| Check | Message |
|---|---|
| The user can view the lab. | "You cannot build this lab." The message does not name the lab. |
| The user is the owner of the lab, or a superuser. | "Only the owner of lab <lab> or a superuser can build it." |
| The user has the change permission on the lab. | "You need the change permission on lab <lab> to build it." |

A lab with no owner, for example after Nautobot deletes the owner account, can get a build from a superuser only.
The build also uses the view permission of the job user on devices. If the user cannot view some selected devices,
the build fails.
The table in "Turn on the job" gives each permission that a builder needs.

#### What a build changes

- A Planned, Built, or Destroyed lab gets the Built status. A Deployed or Failed lab keeps its status.
- A node row matches a node by its name, with a case-sensitive match. A kept row keeps its state and container ID.
- The build deletes each node row whose name is not in the new topology.
- The setting `max_lab_devices` limits the number of selected devices. Stub nodes do not count.

The build reads Nautobot data with no lock, and then writes the lab in one short transaction. If a save of the lab
row occurs during the build, the build fails with "Lab <lab> changed during the build. Run the job again." A change
to a device, a cable, a node mapping, or a config during the build does not make the build fail. Run the job again
to get such a change into the topology YAML.

#### The job log

The job log shows one line at the warning level for each builder warning, for example a skipped cable or a node with
no startup config. It shows one line at the failure level for each error, as `<field>: <message>` for a field error.
A successful build ends with one summary line. The job result holds only the node, link, and warning counts.

### Deploy a lab

The Deploy Lab job deploys the stored topology YAML of a lab to the containerlab host of the lab. It sets the lab
status, and it writes the state, the container ID, and the management address of each node row.

#### Turn on the job

Nautobot adds the job turned off. An administrator does these steps one time:

1. Open Jobs > Jobs, and edit the job "Deploy Lab" in the group "Containerlab Orchestrator".
2. Select "Enabled", and save the job.
3. Give each user who deploys labs the permissions in the table below.

| Permission | Reason |
|---|---|
| `extras.view_job` | The user finds the job under Jobs > Jobs. |
| `extras.run_job` | The user runs the job. |
| `extras.view_jobresult` | The user opens the job result. |
| `extras.view_joblogentry` | The user reads the job log, with the warnings and the errors. |
| `containerlab_orchestrator.view_lab` | The user selects the lab, and the job checks it. |
| `containerlab_orchestrator.change_lab` | The job checks it before the deploy. |
| `containerlab_orchestrator.view_labnode` | The user sees the node state on the lab page. |

The user must also be the owner of the lab, or a superuser. The job reads the host credentials from the Secrets Group
of the host, so the user needs no permission on secrets.

#### Before you use the job in production

The job has no test on a live clab-api-server yet. All its tests use a mocked host. An administrator does this check
one time for each host:

1. Build a lab of two `linux` nodes, and pull `alpine:latest` on the host.
2. Run Deploy Lab. Make sure that the lab is Deployed, and that the two node rows have the state `running`.
3. Remove an image from the host. Then build and deploy a second lab that uses it. Make sure that the job stops with
   the list of the missing images.
4. Run Deploy Lab on the first lab again. Make sure that the job fails with the 409 message, and that the status does
   not change.

#### Who can deploy a lab

The job checks the job user in this order. The first check that fails stops the job. The lab does not change, and
the job makes no call to the host.

| Check | Message |
|---|---|
| The user can view the lab. | "You cannot deploy this lab." The message does not name the lab. |
| The user is the owner of the lab, or a superuser. | "Only the owner of lab <lab> or a superuser can deploy it." |
| The user has the change permission on the lab. | "You need the change permission on lab <lab> to deploy it." |

A lab with no owner can get a deploy from a superuser only.

#### Before the deploy

Only a Built, Destroyed, or Failed lab with a stored topology YAML can deploy. A Planned or Deployed lab stops the job.
If the lab name changed after the build, run the Build Lab Topology job again.

Before the deploy call, the job reads the image list of the host. If the host does not have the image of a node, the
job stops, and the status does not change. The message lists each missing image with its nodes. The job also stops
for a public image that containerlab can pull, and for a node with `image-pull-policy`. Pull each image on the host,
then run the job again.

The job compares the image names in a normal form:

- `index.docker.io/` becomes `docker.io/`.
- A name with no registry gets `docker.io/`. A Docker Hub name with no path gets `library/`.
- A name with no tag and no digest gets the tag `latest`. A name with a digest drops its tag.

So `alpine` matches `alpine:latest` and `docker.io/library/alpine:latest`. A Podman image built on the host is
`localhost/<name>`, and it does not match the node image `<name>`. If the job reports an image that the host has,
write the full image name in the node mapping, or pull the image with the exact name of the topology.

#### What a deploy changes

| Result | Lab status |
|---|---|
| The deploy call returns. | Deployed. Each node row gets the state, the container ID, and the management address from the host. A row with no container gets an empty state and container ID, and the address of the build. The stored inspect result gets the container list. |
| The inspect call fails, or the soft time limit stops it. | Deployed. The node rows get an empty state and container ID, and the address of the build. The stored inspect result gets `{}`. A failed inspect call gives a warning. |
| The deploy call gives HTTP 500 or more, or a read timeout. | Failed. After a read timeout, the lab state on the host is unknown. The stored inspect result gets `{}`. |
| The deploy call gives HTTP 409, another HTTP error, or a connection error. | No change. |
| A check, the image list call, a missing image, or the soft time limit stops the job before the deploy call. | No change. |
| The soft time limit stops the deploy call. | Failed. The lab state on the host is unknown. The stored inspect result gets `{}`. |
| The hard time limit stops the job. | No change. The lab can run on the host. A next run gets HTTP 409. Run Inspect Lab to correct the status. |
| The lab is deleted during the deploy. | None. The lab can still run on the host. |

A container with no node row gives a warning. A tool container, such as SSHX or GoTTY, is one. A management address
that is not the address of the build gives a warning.

A 409 means that the host already has a lab of this name, or that another operation of the lab runs. The job does
not replace the lab. Wait for the other operation, then run the job again.

!!! warning
    The option "Replace a lab that exists on the host" removes the containers and the lab directory of the lab of
    that name on the host, also a lab that Nautobot did not deploy. A change that you made in the running lab is lost.
    Make sure that the lab on the host is yours before you select it.

To replace the lab on the host, select the option "Replace a lab that exists on the host". The option is off by
default. With the option on, the job sends `reconfigure=true` to the host.

#### The job log of a deploy

A deploy ends with one summary line, with the node, node state, and warning counts. The job result holds only these
counts. The job writes no secret, no topology YAML, and no startup config text of its own into the job log.

The job log holds the names of the lab, the host, the nodes, and the images. For a connection error, it holds the URL
of the host. For an error of the host, it holds up to 500 characters of the containerlab error text. That text can
quote a part of the topology, a startup config included. A Golden Config text can hold device secrets.

Each user with `extras.view_jobresult` and `extras.view_joblogentry` can read the job log, also a user who cannot
view the lab. Give these permissions only to users who may read the topology of the lab.

#### Time limits

The job has a soft time limit of 1800 seconds and a hard time limit of 1900 seconds. The job makes four calls to the
host: login, image list, deploy, and inspect. With the default `request_timeout` of 300 seconds, the calls take up
to 1600 seconds:

- Login, image list, and inspect: 3 x (10 + 300) = 930 seconds.
- Deploy: 10 + 300 + 300 + 60 = 670 seconds. The connect timeout is 10 seconds. The host gives containerlab 300
  seconds, and adds a margin of 300 seconds. The job waits 60 seconds more for the response.

The rest is for the database work. If you set a larger `request_timeout`, set the soft time limit to 4 x
`request_timeout` + 600, and the hard time limit to 100 seconds more. Change the limits on the edit page of the job.
The job does not support a c9s (Kubernetes) host. The image list of a c9s host gives HTTP 500, so the job stops before
the deploy call. The containerlab default timeout for c9s is 10 minutes, so a c9s host also needs a `request_timeout` of
600 or more.

### Destroy a lab

The Destroy Lab job removes the lab of a Deployed or Failed lab from its containerlab host. It sets the Destroyed
status, and it clears the state and the container ID of each node row.

#### Turn on the job

Nautobot adds the job turned off. An administrator does these steps one time:

1. Open Jobs > Jobs, and edit the job "Destroy Lab" in the group "Containerlab Orchestrator".
2. Select "Enabled", and save the job.
3. Give each user who destroys labs the permissions in the table below.

| Permission | Reason |
|---|---|
| `extras.view_job` | The user finds the job under Jobs > Jobs. |
| `extras.run_job` | The user runs the job. |
| `extras.view_jobresult` | The user opens the job result. |
| `extras.view_joblogentry` | The user reads the job log, with the warnings and the errors. |
| `containerlab_orchestrator.view_lab` | The user selects the lab, and the job checks it. |
| `containerlab_orchestrator.change_lab` | The job checks it before the destroy. |
| `containerlab_orchestrator.view_labnode` | The user sees the cleared node state on the lab page. |

The user must also be the owner of the lab, or a superuser. The job reads the host credentials from the Secrets Group
of the host, so the user needs no permission on secrets.

#### Before you use the job in production

The job has no test on a live clab-api-server yet. All its tests use a mocked host. An administrator does this check
one time for each host:

1. Deploy the lab of two `linux` nodes of the Deploy Lab check. Run Destroy Lab. Make sure that the lab is Destroyed,
   and that the two node rows have no state and no container ID.
2. Run Destroy Lab again. Make sure that the job stops with `Lab <lab> is Destroyed. Only a Deployed or Failed lab can
   destroy.`
3. Deploy the lab again, and remove it on the host with `containerlab destroy`. Run Destroy Lab. Make sure that the
   job succeeds with the warning `Host <host> has no lab named <lab>.`.

#### Who can destroy a lab

The job checks the job user in this order. The first check that fails stops the job. The lab does not change, and
the job makes no call to the host.

| Check | Message |
|---|---|
| The user can view the lab. | `You cannot destroy this lab.` The message does not name the lab. |
| The user is the owner of the lab, or a superuser. | `Only the owner of lab <lab> or a superuser can destroy it.` |
| The user has the change permission on the lab. | `You need the change permission on lab <lab> to destroy it.` |

A lab with no owner can get a destroy from a superuser only.

#### What the job removes on the host

!!! warning ""
    WARNING: This deletes the containers of the lab of this name on the host, also a lab that Nautobot did not
    deploy. Make sure that the lab on the host is yours before you run the job.

The job sends the lab name to the host. The host removes these items of the lab:

- Each container of the lab.
- The tool containers of the lab: SSHX, GoTTY, and Tailscale.
- The management network of the lab.
- The `/etc/hosts` entries and the SSH config of the lab.
- The network namespace links of the lab.
- The staged restore snapshots of the lab.
- The lab directory `clab-<lab>`, unless "Keep the lab directory" is on.

The host finds the lab by its name. So the job also removes these labs:

- A lab of that name that the server account of the host made by hand.
- A shared lab of that name.
- Each lab of that name, if the server account is a clab-api-server superuser.

Two host records with the same server URL share one set of labs. A destroy from one record removes the lab of the
other record.

The job does not remove the images or the topology file on the host. If the host has no lab of that name, or another
user of the host owns it, the job removes nothing.

#### Keep the lab directory

The lab directory holds the saved configs, the `cisco_iol` NVRAM file, the `nokia_sros` saved config, and the TLS
files of the nodes. Select "Keep the lab directory" to keep it on the host. The option is off by default. With the
option on, the job sends `cleanup=false` to the host.

Only a later Deploy Lab job with the replace option off uses the kept directory again. The replace option of Deploy
Lab removes it.

#### What a destroy changes

| Result | Lab status |
|---|---|
| The destroy call returns. | Destroyed. Each node row gets an empty state and an empty container ID. The stored inspect result gets an empty list. |
| The host has no lab of that name. | Destroyed, with a warning. |
| Another user of the host owns a lab of that name. | No change. Check the Secrets Group of the host. |
| The destroy call gives another HTTP 404. | No change. |
| The destroy call gives HTTP 500 or more, a read timeout, or a closed connection. | Failed. The lab state on the host is unknown. The stored inspect result gets `{}`. |
| The destroy call gives HTTP 409. | No change. Another operation of the lab runs. Wait for it to end, then run the job again. |
| The destroy call gives HTTP 400, 401, or 403, or the host cannot be reached. | No change. |
| A check, the login, or the soft time limit stops the job before the destroy call. | No change. |
| The soft time limit stops the destroy call. | Failed. The lab state on the host is unknown. The stored inspect result gets `{}`. |
| The soft time limit stops the job after the destroy call. | No change. The host no longer has the lab, but the job log says that the state is unknown. Run Inspect Lab to correct the status. |
| The status of the lab changes during the job. | No change. The job writes nothing. The job log tells if the host still has the lab. |
| The lab is deleted during the job. | The lab row is gone. The job log tells if the host still has the lab. |

When the status changes or the lab is deleted during the job, the job log tells the state of the host:

- `Host <host> no longer has lab <lab>.` The destroy call removed the lab, or the host had no lab of that name.
- `The lab can still run on host <host>.` The lab was deleted, and the destroy call failed with a host error.
- `Lab <lab> changed its status during the destroy, so the job did not set Failed.` The status changed, and the
  destroy call failed with a host error. The lab can still run on the host.

A container that the host cannot delete gives HTTP 500, so the lab becomes Failed. The host can also give HTTP 500
after a full destroy. Then the next Destroy Lab job finds no lab on the host, and sets Destroyed.

#### The job log of a destroy

A destroy ends with one summary line, with the count of cleared node rows and the warning count. The job result holds
only these counts. The job writes no secret, no topology YAML, and no startup config text of its own into the job
log. For an error of the host, the job log holds up to 500 characters of the error text. The deploy rules of "The job
log of a deploy" also apply.

#### Time limits

The job has a soft time limit of 1800 seconds and a hard time limit of 1900 seconds. The job makes two calls to the
host: login and destroy. With the default `request_timeout` of 300 seconds, the calls take up to 1100 seconds:

- Login: 10 + 300 = 310 seconds.
- Destroy: 10 + 300 + 300 + 120 + 60 = 790 seconds. The host gives containerlab 300 seconds, and adds a margin of 300
  seconds. The owner check of the host takes up to 120 seconds more. The job waits 60 seconds more for the response.

The Deploy Lab rule also covers Destroy Lab: set the soft time limit to 4 x `request_timeout` + 600, and the hard time
limit to 100 seconds more. A host runs one containerlab operation at a time. On a busy host, the destroy call can
wait for the other operations and give a read timeout. Then the lab becomes Failed.

### Redeploy a lab

The Redeploy Lab job removes the lab of a Deployed or Failed lab from its containerlab host, with its lab directory.
Then it deploys the stored topology YAML of the lab again. It sets the lab status, and it writes the state, the
container ID, and the management address of each node row.

#### Turn on the job

Nautobot adds the job turned off. An administrator does these steps one time:

1. Open Jobs > Jobs, and edit the job "Redeploy Lab" in the group "Containerlab Orchestrator".
2. Select "Enabled", and save the job.
3. Give each user who redeploys labs the permissions in the table below.

| Permission | Reason |
|---|---|
| `extras.view_job` | The user finds the job under Jobs > Jobs. |
| `extras.run_job` | The user runs the job. |
| `extras.view_jobresult` | The user opens the job result. |
| `extras.view_joblogentry` | The user reads the job log, with the warnings and the errors. |
| `containerlab_orchestrator.view_lab` | The user selects the lab, and the job checks it. |
| `containerlab_orchestrator.change_lab` | The job checks it before the redeploy. |
| `containerlab_orchestrator.view_labnode` | The user sees the node state on the lab page. |

The user must also be the owner of the lab, or a superuser. The job reads the host credentials from the Secrets Group
of the host, so the user needs no permission on secrets.

#### Before you use the job in production

The job has no test on a live clab-api-server yet. All its tests use a mocked host. An administrator does this check
one time for each host:

1. Deploy the lab of two `linux` nodes of the Deploy Lab check. Run Redeploy Lab. Make sure that the lab is Deployed,
   and that the two node rows have new container IDs.
2. Remove the lab on the host with `containerlab destroy`. Run Redeploy Lab. Make sure that the job succeeds with the
   warning `Host <host> has no lab named <lab>.`.

#### Who can redeploy a lab

The job checks the job user in this order. The first check that fails stops the job. The lab does not change, and
the job makes no call to the host.

| Check | Message |
|---|---|
| The user can view the lab. | `You cannot redeploy this lab.` The message does not name the lab. |
| The user is the owner of the lab, or a superuser. | `Only the owner of lab <lab> or a superuser can redeploy it.` |
| The user has the change permission on the lab. | `You need the change permission on lab <lab> to redeploy it.` |

A lab with no owner can get a redeploy from a superuser only.

#### Before the redeploy

Only a Deployed or Failed lab with a stored topology YAML can redeploy. A Planned, Built, or Destroyed lab stops the
job with `Lab <lab> is <status>. Only a Deployed or Failed lab can redeploy.` For a Built or Destroyed lab, run Deploy
Lab. Labs must be able to have the Deployed, Failed, and Destroyed statuses.

Before it removes the lab, the job reads the image list of the host. If the host does not have the image of a node,
the job stops, and the lab does not change. The image rules of "Before the deploy" apply.

The job deploys the topology YAML that the lab has when the job starts. If a build writes a new topology during the
job, the job log gives a warning. Then run Redeploy Lab again.

#### What a redeploy removes

!!! warning ""
    WARNING: This deletes the containers and the lab directory of the lab of this name on the host, also a lab that
    Nautobot did not deploy. Each change in the running lab is lost. Make sure that the lab on the host is yours
    before you run the job.

The job always removes the lab directory `clab-<lab>`. So these items are lost:

- The saved configs of the nodes.
- The NVRAM file of each `cisco_iol` node.
- The saved config of each `nokia_sros` node.
- The TLS files of the nodes.

The job also removes each other item of "What the job removes on the host" of Destroy Lab: the containers, the tool
containers, the management network, the `/etc/hosts` entries, the SSH config, the network namespace links, and the
staged restore snapshots. The host finds the lab by its name. If the server account of the host is a clab-api-server
superuser, or if it made a lab of that name by hand, the job removes that lab.

#### How a redeploy runs

The job does these steps:

1. It checks the images on the host. A missing image stops the job before any change.
2. It removes the lab on the host. The destroy rules of "What a destroy changes" apply. A failure stops the job
   before the deploy.
3. It deploys the stored topology YAML. The deploy rules of "What a deploy changes" apply, from the Destroyed status.
4. It reads the node state, and it sets Deployed.

| Result | Lab status |
|---|---|
| The deploy call returns. | Deployed. Each node row gets the new state, container ID, and management address from the host. The stored inspect result gets the container list. |
| The host has no lab of that name at the destroy. | Deployed, with a warning. |
| A check, the image list call, a missing image, or the soft time limit stops the job before the destroy call. | No change. |
| The destroy call fails, or the soft time limit stops it. | As for a destroy. The job does not deploy. Run Inspect Lab to correct the status. |
| The deploy call gives HTTP 500 or more, a read timeout, a closed connection, or a response that the job cannot read. | Failed. The stored inspect result gets `{}`. |
| The deploy call gives HTTP 409, another HTTP error, or a connection error. | Destroyed. |
| The soft time limit stops the deploy call. | Failed. The lab state on the host is unknown. The stored inspect result gets `{}`. Run Inspect Lab to correct the status. |
| The inspect call fails. | Deployed. The node rows get an empty state and container ID, and the address of the build. The stored inspect result gets `{}`. The job log gives a warning. |

When the deploy call fails after the destroy, the job log adds this line:

`The job removed lab <lab> from host <host> before the deploy failed. Run Deploy Lab after you fix the cause.`

A redeploy ends with one summary line:

`Redeployed lab <lab> on host <host>: <nodes> nodes, <states> node states, <warnings> warnings.`

The job result holds only these counts. The rules of "The job log of a deploy" also apply.

#### Time limits

The job has a soft time limit of 2600 seconds and a hard time limit of 2700 seconds. The job makes five calls to the
host. With the default `request_timeout` of 300 seconds, the calls take up to 2390 seconds:

- Login, image list, and inspect: 3 x (10 + 300) = 930 seconds.
- Destroy: 790 seconds, as for Destroy Lab.
- Deploy: 670 seconds, as for Deploy Lab.

The rest is for the database work. If you set a larger `request_timeout`, set the soft time limit to 5 x
`request_timeout` + 1100, and the hard time limit to 100 seconds more. The host gets a containerlab timeout of 7200
seconds or less. Change the limits on the edit page of the job.

### Inspect a lab

The Inspect Lab job reads the containers of a lab on its containerlab host. It writes the state, the container ID,
and the management address of each node row, and it sets the lab status. The job changes nothing on the host.

Run the job when the lab page does not agree with the host. Two time limits can leave a wrong status:

- The hard time limit stops Deploy Lab after the host deployed the lab. The lab runs, but the status stays Built,
  Destroyed, or Failed.
- The soft time limit stops Destroy Lab after the host removed the lab. The status stays Deployed.

#### Turn on the job

Nautobot adds the job turned off. An administrator does these steps one time:

1. Open Jobs > Jobs, and edit the job "Inspect Lab" in the group "Containerlab Orchestrator".
2. Select "Enabled", and save the job.
3. Give each user who inspects labs the permissions in the table below.

| Permission | Reason |
|---|---|
| `extras.view_job` | The user finds the job under Jobs > Jobs. |
| `extras.run_job` | The user runs the job. |
| `extras.view_jobresult` | The user opens the job result. |
| `extras.view_joblogentry` | The user reads the job log, with the warnings and the errors. |
| `containerlab_orchestrator.view_lab` | The user selects the lab, and the job checks it. |
| `containerlab_orchestrator.change_lab` | The job writes the status and the node rows. |
| `containerlab_orchestrator.view_labnode` | The user sees the node state on the lab page. |

The user does not need to be the owner of the lab. The job reads the host credentials from the Secrets Group of the
host, so the user needs no permission on secrets.

#### Before you use the job in production

The job has no test on a live clab-api-server yet. All its tests use a mocked host. An administrator does this check
one time for each host:

1. Deploy the lab of two `linux` nodes of the Deploy Lab check. Run Inspect Lab. Make sure that the lab is Deployed,
   and that each node row has the state `running` and a management address.
2. On the host, run `docker stop` on one node container. Run Inspect Lab. Make sure that the lab is Failed, and that
   the job log names the node.
3. Remove the lab on the host with `containerlab destroy`. Run Inspect Lab. Make sure that the lab is Destroyed,
   with the warning `Host <host> has no lab named <lab>.`.
4. If your labs use a tool container, such as SSHX or GoTTY, deploy a lab with one. Run Inspect Lab. Make sure that
   the job does not fail with the message of "Labs of other host users".

#### Who can inspect a lab

The job checks the job user in this order. The first check that fails stops the job. The lab does not change, and
the job makes no call to the host.

| Check | Message |
|---|---|
| The user can view the lab. | `You cannot inspect this lab.` The message does not name the lab. |
| The user has the change permission on the lab. | `You need the change permission on lab <lab> to inspect it.` |

The job does not check the owner. A user with the two permissions can inspect a lab of another owner, or a lab with
no owner.

#### Before the inspect

The job inspects only a Built, Deployed, Failed, or Destroyed lab. A Planned lab stops the job with
`Lab <lab> is Planned. The job inspects only a Built, Deployed, Failed, or Destroyed lab.` Labs must be able to have
the Deployed, Failed, and Destroyed statuses.

The job also checks the stored topology YAML, as Deploy Lab does. A lab with no topology, a topology that the job
cannot read, or a lab that changed its name after the build stops the job. Run Build Lab Topology again. In each
case, the job makes no call to the host.

#### What an inspect changes

!!! warning ""
    WARNING: The job can record a lab of this name that Nautobot did not deploy. The host user of the Secrets Group
    can make such a lab, or a second host record can have the same URL. A Built or Destroyed lab can then become
    Deployed or Failed. Make sure that the lab on the host is yours before you run Destroy Lab or Redeploy Lab.

| Result of the inspect call | Built or Destroyed lab | Deployed or Failed lab |
|---|---|---|
| Each node row has a running container. | Deployed, if the containers match the build. Else no change, with a warning. | Deployed. |
| A node row has no container, or its container does not run. | Failed, with a warning, if the containers match the build. Else no change, with a warning. | Failed, with a warning that names the nodes. |
| The host has no lab of that name. | No change, with a warning. | Destroyed, with a warning. |
| The host has only the helper containers of the lab. | Failed, with a warning. | Failed, with a warning. |
| Another user of the host owns the lab. | The job fails. No change. | The job fails. No change. |
| The host gives another error, a read timeout, or a result that the job cannot read. | The job fails. No change. | The job fails. No change. |
| The status, a field, or the build of the lab changes during the job. | The job fails. No change. | The job fails. No change. |

A node runs when the state of its container is `running`. This is a Docker and Podman state.

The containers match the build when these are true:

- Each container has the node name of a node row.
- Each node row has one container.
- The image of each container is the image of its node row. The image rules of "Before the deploy" apply.

A tool container is not a node row, so it stops a match. A Built or Destroyed lab then keeps its status, with the
warning `Host <host> runs a lab named <lab> that does not match the build of lab <lab>. The job did not change the
status.`

A helper container is a container that containerlab adds for a node, for example a Tailscale sidecar. When the host
has only these, the warning tells you to run Inspect Lab again. If the result does not change, run Destroy Lab.

When the job fails, the status, the node rows, and the stored inspect result do not change.

#### Labs of other host users

The job compares the owner of each container with the username of the Secrets Group of the host. If a container has
another owner or no owner, the job fails with this message, and the lab does not change:

`Host <host> has a lab named <lab> that another user of the host owns. The job did not read it. Check the credentials of host <host>.`

The job reads the credentials of the host from the Secrets Group of the host.

The job fails in the same way when the host user is a clab-api-server superuser, or when another user shares the lab
with the host user. In both cases, the containers have another owner. Use a host user that is not a clab-api-server
superuser.

The host reads the owner of a container from its `clab-owner` label. A tool container, such as SSHX or GoTTY,
possibly has no such label. Then each Inspect Lab job of that lab fails with this message, and changes nothing. Check
this case on your host with step 4 of the production check.

#### What the job writes

For each node row, the job finds the container whose node name is the row name. Then it writes these fields:

- The state and the container ID of the container. A row with no container gets an empty state and container ID.
- The management address that the host reports. A row with no container, or a container with no address, gets the
  address of the build. If the build has no address for the row, the row keeps its address.

The job log gives a warning in these cases:

- `Node <node> of lab <lab> has the management address <live> on host <host>, but the build gave <planned>.`
- `Host <host> has containers of lab <lab> with no node row: <names>.` A tool container, such as SSHX or GoTTY, is
  one. A container that the replace option of Deploy Lab left on the host is another.

The job also writes the stored inspect result. This is the `last_inspect` field of the lab in the REST API, at
`GET /api/plugins/containerlab-orchestrator/labs/<id>/`. The lab page does not show it. It has one of these values:

| Value | Meaning |
|---|---|
| A list of containers | The containers that the host gave to the last inspect of Inspect Lab, Deploy Lab, or Redeploy Lab. A Failed status from Inspect Lab keeps the list, so you can see which node does not run. |
| `[]` | The host had no container of the lab, the host had only helper containers, or Destroy Lab removed the lab. |
| `{}` | No result. The lab never had an inspect, an inspect call of Deploy Lab or Redeploy Lab failed, or Deploy Lab, Redeploy Lab, or Destroy Lab set Failed. |

Each user who can view the lab can read the stored inspect result. Each container in it holds the server user name
(`owner`) and the path of the topology file on the host (`labPath` and `absLabPath`). It holds no secret.

#### Inspect during another job

Do not run Inspect Lab while a Deploy Lab, Destroy Lab, or Redeploy Lab job of the lab runs. If Inspect Lab writes
first, the other job can fail with `Lab <lab> changed its status while the job ran...`. If the other job writes
first, Inspect Lab fails with `Lab <lab> changed while the job ran, so the job wrote nothing. Run the job again.`.
A build or an edit of the lab during the job gives the same failure. Then run Inspect Lab again.

Sync Labs skips a lab while another lab job of that lab runs. See "Sync all labs".

#### Rename or move a lab

Inspect Lab stops for a renamed lab, as Deploy Lab does. Run Build Lab Topology again first.

Destroy a lab before you change its host. Inspect Lab and Destroy Lab read the new host. If the new host has no lab
of that name, Inspect Lab sets Destroyed while the lab runs on the old host.

#### The job log of an inspect

An inspect ends with one summary line:

`Inspected lab <lab> on host <host>: <nodes> nodes, <running> running, <warnings> warnings. Status: <status>.`

The job result holds only the node, running, and warning counts. From the containers, the job log holds only the
container name, the node name, and the management address. It does not hold the state, the owner, the image, or the
topology paths. The rules of "The job log of a deploy" also apply.

#### Time limits

The job has a soft time limit of 700 seconds and a hard time limit of 800 seconds. The job makes two calls to the
host: login and inspect. With the default `request_timeout` of 300 seconds, the calls take up to 2 x (10 + 300) = 620
seconds. The rest is for the database work.

If you set a larger `request_timeout`, set the soft time limit to 2 x `request_timeout` + 100, and the hard time
limit to 100 seconds more. Change the limits on the edit page of the job.

If the login token ends during the inspect call, the job logs in again and sends the call again. Then the job makes
four calls, up to 1240 seconds, and the soft time limit can stop it. The job then fails with
`Lab <lab>: the job time limit stopped the inspect. The lab did not change.` Run the job again.

### Sync all labs

The Sync Labs job inspects each lab in scope, with the rules of Inspect Lab. Schedule it, and each lab page then
shows the current node state and status. The job changes nothing on the host. After the production check of Inspect
Lab, run Sync Labs one time by hand, and make sure that each lab in scope has a result line.

#### Turn on the job

Turn on "Sync Labs" as "Turn on the job" of Inspect Lab shows. Give the user of the schedule these permissions:

| Permission | Reason |
|---|---|
| `extras.view_job` | The user finds the job under Jobs > Jobs. |
| `extras.run_job` | The user runs the job, and makes a schedule of it. |
| `extras.view_scheduledjob` | The user sees the schedule under Jobs > Scheduled Jobs. |
| `extras.view_jobresult` | The user opens the job result. |
| `extras.view_joblogentry` | The user reads the job log, with the warnings and the errors. |
| `containerlab_orchestrator.view_lab` | The job reads only the labs that the user can view. |
| `containerlab_orchestrator.change_lab` | The job writes only the labs that the user can change. |
| `containerlab_orchestrator.view_labnode` | The user sees the node state on the lab page. |

#### Schedule the job

1. Open Jobs > Jobs > Sync Labs, and click Run.
2. Select "Recurring daily", or "Recurring custom" with a crontab such as `0 */2 * * *`.
3. Make the interval longer than the hard time limit of the job.

The job runs as the user who makes the schedule. If your site has an approval workflow, the schedule waits for
approval. The job is a singleton. A second run that starts while a run continues ends Failed with
`Job 'Sync Labs' is a singleton and already running.`, and changes nothing. Do not select "Ignore singleton lock".

If somebody deletes the user of the schedule, Nautobot turns off the schedule and sets its state to Errored. Nautobot
3.2.9 also writes one Failed result with the text "Use 'Assume Ownership' on the scheduled job detail view to reassign
it.". Use "Assume Ownership" on the scheduled job, then turn on the schedule again. Nautobot 3.1 writes no result and
has no "Assume Ownership". Delete the schedule, and make it again as another user.

#### Which labs the job reads

The job reads each Deployed or Failed lab that the user can view and change, in the order of the host name, then the
lab name. It reads the list one time, when it starts. Other labs get no log line and no call. A lab that the job sets
to Destroyed leaves the scope. Run Inspect Lab for a Built or Destroyed lab.

For each host, the job logs in one time, reads each lab, then writes each lab that it read. When the job reads 2 or
more labs of a host, and the host reports no lab for each one, the job changes none of them. Each lab then gets
`Lab <lab> on host <host>: the host reported no lab for each of its <count> labs, so the job did not change them.
Check the host, then run Inspect Lab.` The count is only the labs that the job read, not the skipped or failed labs.
When the job reads one lab only, it sets that lab to Destroyed. Destroy a lab before you change its host.

#### What a sync changes

The "Deployed or Failed lab" column of the table in "What an inspect changes" applies. The job does not save a lab
with no change. A change in the order of the containers or in the uptime text of a container is no change. A lab
with no change gets no change log entry, its last update time stays the same, and `last_inspect` keeps the older
uptime text. Inspect Lab saves the lab on each run.

#### Labs with a lab job that runs

Just before it reads a lab, the job checks for a Build Lab Topology, Deploy Lab, Redeploy Lab, Destroy Lab, or Inspect
Lab job of that lab that is not done. If Nautobot queued that job in the last 6 hours, Sync Labs skips the lab with
the warning `Lab <lab> on host <host> has a lab job that runs, so the job did not sync the lab. Run Inspect Lab after
that job ends.` A skip alone does not make the run Failed. Keep the hard time limit of each lab job less than 6 hours.
A lab job can still start after the check. Then one of the two jobs fails, as "Inspect during another job" shows.

#### Hosts that fail

A login error fails each lab of that host. When a call to a host gets no response, each later lab of that host gets
`Lab <lab> on host <host>: the host gave no response to an earlier call of this job, so the job did not sync the
lab.` An unexpected error, such as a database lock timeout, gives `Lab <lab> on host <host>: the job got an unexpected
error (<error type>). The job continued with the next lab.` The other hosts continue, and the run ends Failed.

#### The job log of a sync

The job logs one result line for each lab in scope. A lab that synced gets its inspect warnings, then one of these:

- `Synced lab <lab> on host <host>: <nodes> nodes, <running> running, <warnings> warnings. Status: <status>.`
- `Lab <lab> on host <host> did not change: <nodes> nodes, <running> running, <warnings> warnings. Status: <status>.`

A lab with an error gets `Lab <lab> on host <host>: <error>`. The job ends with
`Sync Labs read <labs> labs: <changed> changed, <unchanged> did not change, <skipped> skipped, <failed> failed.` The
job result holds only these counts. The rules of "The job log of an inspect" also apply.

To see the last sync of a lab, open Jobs > Job Results, and filter by the job "Sync Labs". The lab page does not.

#### Time limits

The job has a soft time limit of 3500 seconds and a hard time limit of 3600 seconds. The job makes one login for each
host, and one inspect for each lab. A 401 adds one login and one retry. So the worst case is (labs + hosts) x (10 +
`request_timeout`) seconds, plus 2 calls for each 401. With the default `request_timeout` of 300 seconds, 3500 covers
10 slow calls. A host that gives no response uses about one wait, because the job skips its other labs.

Set the soft time limit to the worst case + 100, and the hard time limit to 100 seconds more. Change the limits on
the edit page of the job. If the soft time limit stops the job, each lab with no result line gets `Lab <lab> on host
<host>: the job time limit stopped the job before it logged a result for this lab. Run Inspect Lab to check the lab.`

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
writes the new config, and a redeploy removes a change that you make in the running lab.

Redeploy Lab and the option "Replace a lab that exists on the host" of Deploy Lab remove the lab directory. So a
`cisco_iol` node gets a new NVRAM file, and a `nokia_sros` node gets the new partial config.

There are two exceptions. They apply only to a Deploy Lab job with the replace option off, after a Destroy Lab job
with "Keep the lab directory" on:

- A `cisco_iol` node keeps its NVRAM file when it exists, so it boots with its saved config.
- A `nokia_sros` node applies the partial config only when the lab directory has no saved config.

#### Who can see a startup config

The startup config text is in the topology YAML of the lab. A Golden Config text can hold secrets of the device.
The app leaves the topology YAML out of the change log, the webhook payloads, the job hook payloads, and the labs
REST API. Each user who can view the lab can see it on the Topology YAML tab, in the topology download, GraphQL,
export templates, and custom links. containerlab also
writes a copy of the text to the disk of the containerlab host.

#### Who can read the job log

The job log of a build holds no topology YAML and no startup config text, with one exception. A warning for a
config template that did not render holds the error text of the template. Each user with the view permission on job
results and job log entries can read the job log.

## Screenshots

!!! warning "Developer Note - Remove Me!"
    Ideally captures every view exposed by the App. Should include a relevant dataset.
