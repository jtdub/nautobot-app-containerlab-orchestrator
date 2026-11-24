# v1.0 Release Notes

This document describes all new features and changes in the release. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- towncrier release notes start -->

## [v1.0.0 (2026-11-24)](https://github.com/jtdub/nautobot-app-containerlab-orchestrator/releases/tag/v1.0.0)

### Added

- Added containerlab hosts. A host stores the clab-api-server URL, the Verify SSL setting, and a Secrets Group.
- Added node mappings and interface mappings. They give the containerlab kind, image, and interface names for a Device Type or a Platform.
- Added labs. A lab selects Nautobot devices, and the lab page shows the nodes, a Topology graph, and the topology YAML.
- Added the Build Lab Topology job, which builds the containerlab topology of a lab from its devices and cables.
- Added the Deploy Lab, Redeploy Lab, Destroy Lab, and Inspect Lab jobs, which run a lab on its host through clab-api-server.
- Added the Sync Labs job, which updates the status of each Deployed or Failed lab on a schedule.
- Added Golden Config intended and backup configs as the startup config of a lab node.
- Added job buttons to the lab page. The app turns on all six jobs when you install it.
- Added hosts, node mappings, interface mappings, labs, and lab nodes to the REST API and GraphQL.
