# Upgrading the App

Here you will find any steps necessary to upgrade the App in your Nautobot environment.

## Upgrade Guide

!!! warning ""
    WARNING: The app needs Nautobot 3.2.8 or later. If your Nautobot is older, `pip` also upgrades Nautobot, and
    the Nautobot database migrations are hard to undo. Back up the Nautobot database. Upgrade Nautobot with its own
    upgrade steps before you install or upgrade the app. To keep `pip` from a change to Nautobot, pin `nautobot` in
    a constraints file and use `pip install -c <file>`.

1. Read the [release notes](release_notes/index.md) of each version after your version.
2. Upgrade the package. For a Git install, use the `pip install` command of the [Install Guide](install.md#install-guide)
   with the SHA of the new commit.

    ```shell
    pip install --upgrade containerlab-orchestrator
    ```

3. Run the migrations and clear the cache.

    ```shell
    nautobot-server post_upgrade
    ```

4. Restart the Nautobot, worker, and scheduler services.

    ```shell
    sudo systemctl restart nautobot nautobot-worker nautobot-scheduler
    ```
