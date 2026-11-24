# App Overview

This document provides an overview of the App including critical information and important considerations when applying it to your Nautobot environment.

!!! note
    Throughout this documentation, the terms "app" and "plugin" will be used interchangeably.

## Description

Containerlab Orchestrator makes containerlab labs from the devices and cables in Nautobot. It builds a containerlab
topology from the devices of a lab, and stores the topology on the lab. Then it deploys the lab to a containerlab
host, reads the state of each node, and destroys the lab.

The app talks to each host only through the clab-api-server REST API. It needs no SSH access.

## Audience (User Personas) - Who should use this App?

- A Nautobot administrator installs the app, sets up the containerlab hosts, and adds them in Nautobot. See the
  [Installation Guide](../admin/install.md).
- A network engineer adds node mappings and labs. Then the engineer builds, deploys, inspects, and destroys the labs.
  See [Getting Started](app_getting_started.md) and [Using the App](app_use_cases.md).

## Authors and Maintainers

- `jtdub`

## Nautobot Features Used

- Models: containerlab hosts, node mappings, interface mappings, labs, and lab nodes.
- Statuses: a lab has the status Planned, Built, Deployed, Failed, or Destroyed.
- Jobs: Build Lab Topology, Deploy Lab, Redeploy Lab, Destroy Lab, Inspect Lab, and Sync Labs. The app turns on
  each job when you install the app.
- Navigation: the **Containerlab** menu, with Labs, Containerlab Hosts, and Node Mappings.
- The lab page: a button for each lab job, the Devices panel, the Nodes table, and the Topology and Topology YAML tabs.
- Panels: a Node Mappings panel on the Device Type page and on the Platform page.
- REST API and GraphQL: each model of the app. See [External Interactions](external_interactions.md).
- Secrets: each host gets the username and the password of its host account from a Secrets Group.
