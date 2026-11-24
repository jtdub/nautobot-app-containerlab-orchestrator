# Frequently Asked Questions

## The lab page shows no Build, Deploy, or other job button

The lab page shows a button only for a job that is on and that you can run. The app turns on each job when you
install the app, but an administrator can turn a job off. See [Who sees a button](app_use_cases.md#who-sees-a-button)
and "Turn on the job" of [Build a lab](app_use_cases.md#turn-on-the-job).

## Deploy Lab fails with "Host `<host>` does not have these images: `<images>`."

The host does not have each image of the topology. The job does not pull images. Pull each image on the host. Then
run the job again. See [Before the deploy](app_use_cases.md#before-the-deploy).

## Deploy Lab fails with "Host `<host>` already has a lab named `<lab>`, or another operation of the lab runs."

The host gave HTTP 409, and the job did not change the lab. Wait until the other operation ends. Then run the job
again. Before you replace a lab on the host, read [What a deploy changes](app_use_cases.md#what-a-deploy-changes).

## The build fails with "Lab `<lab>` changed during the build."

Somebody saved the lab while the job ran. Run the job again. See
[What a build changes](app_use_cases.md#what-a-build-changes).

## The build log has "Device `<device>`, interface `<interface>`: the interface table has no row."

The node mapping of the device has no row for the interface, so the node uses a fallback `eth<N>` name. Add a row to
the interface table of the mapping. See [Fallback names](app_use_cases.md#fallback-names).

## The build fails with "You cannot view `<count>` of the devices that lab `<lab>` selects."

The lab selects devices that your permissions do not show to you. Ask for the view permission on these devices, or
remove them from the lab. See [Select the devices](app_use_cases.md#select-the-devices).

## The REST API gives "Golden Config is not installed, so this config source is not available."

The REST API rejects a new Golden Config source when Golden Config is not installed. Install Golden Config, or use
another config source. A node with an older Golden Config source gets a warning and no startup config. See
[Golden Config](app_use_cases.md#golden-config).

## The build fails with "Lab `<lab>` selects `<count>` devices."

The lab selects more devices than the `max_lab_devices` setting allows. Select fewer devices, or ask an administrator
to raise the limit. See [The device limit](app_use_cases.md#the-device-limit).

## A new lab does not save: "The Planned status is not available for labs."

The app needs the statuses Planned and Built for labs. Without Built, the build fails with "The Built status is not
available for labs." An administrator goes to **Organization > Statuses**, and adds labs to the content types of
the status. See [The owner and the status](app_use_cases.md#the-owner-and-the-status).

## Can the host account be a clab-api-server superuser?

No. A superuser can remove the labs of other server users. See [The host account](../admin/install.md#the-host-account).
