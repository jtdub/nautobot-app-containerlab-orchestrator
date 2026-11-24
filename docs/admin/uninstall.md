# Uninstall the App from Nautobot

Here you will find any steps necessary to cleanly remove the App from your Nautobot environment.

## Destroy the labs

The lab containers stay on the hosts when you remove the app. Run the
[Destroy Lab](../user/app_use_cases.md#destroy-a-lab) job for each lab that has containers on a host.

## Database Cleanup

Before you remove the app from `nautobot_config.py`, roll back the migrations of the app.

!!! warning ""
    WARNING: This command deletes all the data of the app: the hosts, the node mappings, the interface mappings, the
    labs, and the lab nodes. You cannot undo it. Make a backup of the database before you run the command.

```shell
nautobot-server migrate containerlab_orchestrator zero
```

The Secrets and the Secrets Groups of the hosts are Nautobot objects, so they stay. Delete them if you do not need
them.

## Remove App configuration

Remove the configuration you added in `nautobot_config.py` from `PLUGINS` & `PLUGINS_CONFIG`.

## Uninstall the package

```shell
pip uninstall nautobot-containerlab-orchestrator
```

Remove `nautobot-containerlab-orchestrator` from `local_requirements.txt`. Then restart the Nautobot, worker, and scheduler
services.

```shell
sudo systemctl restart nautobot nautobot-worker nautobot-scheduler
```
