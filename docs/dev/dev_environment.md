# Building Your Development Environment

## Quickstart Guide

The development environment can be used in two ways:

1. **(Recommended)** All services, including Nautobot, are spun up using Docker containers and a volume mount so you can develop locally.
2. With a local Poetry environment if you wish to develop outside of Docker, with the caveat of using external services provided by Docker for the database (PostgreSQL by default, MySQL optionally) and Redis services.

This is a quick reference guide if you're already familiar with the development environment provided, which you can read more about later in this document.

### Invoke

The [Invoke](http://www.pyinvoke.org/) library is used to provide some helper commands based on the environment. There are a few configuration parameters which can be passed to Invoke to override the default configuration:

- `nautobot_ver`: the version of Nautobot to use as a base for any built docker containers (default: 3.2.8)
- `project_name`: the default docker compose project name (default: `containerlab-orchestrator`)
- `python_ver`: the version of Python to use as a base for any built docker containers (default: 3.12)
- `local`: a boolean flag indicating if invoke tasks should be run on the host or inside the docker containers (default: False, commands will be run in docker containers)
- `compose_dir`: the full path to a directory containing the project compose files
- `compose_files`: a list of compose files applied in order (see [Multiple Compose files](https://docs.docker.com/compose/extends/#multiple-compose-files) for more information)
- `ephemeral_ports`: Setting this value to `true` and not using any custom compose files will make all Nautobot containers with published ports expose themselves with dynamic ports. This is useful when running multiple Nautobot versions at the same time on the same machine so you won't experience system port conflicts. If setting `compose_files`, this will have no effect so please ensure to manually add the applicable `docker-compose.ephemeral-ports.yml` file or files to your list.

Using **Invoke** these configuration options can be overridden using [several methods](https://docs.pyinvoke.org/en/stable/concepts/configuration.html). Perhaps the simplest is setting an environment variable `INVOKE_CONTAINERLAB_ORCHESTRATOR_VARIABLE_NAME` where `VARIABLE_NAME` is the variable you are trying to override. The only exception is `compose_files`, because it is a list it must be overridden in a YAML file. There is an example `invoke.yml` (`invoke.example.yml`) in this directory which can be used as a starting point.

### Docker Development Environment

!!! tip
    This is the recommended option for development.

This project is managed by [Python Poetry](https://python-poetry.org/) and has a few requirements to setup your development environment:

1. Install Poetry, see the [Poetry documentation](https://python-poetry.org/docs/#installation) for your operating system.
2. Install Docker, see the [Docker documentation](https://docs.docker.com/get-docker/) for your operating system.
3. Install Docker-compose, see the [Docker-compose documentation](https://github.com/docker/compose) for your operation system.

Once you have Poetry and Docker installed you can run the following commands (in the root of the repository) to install all other development dependencies in an isolated Python virtual environment:

```shell
poetry self add poetry-plugin-shell
poetry shell
poetry install
invoke build
invoke start
```

The Nautobot server can now be accessed at [http://localhost:8080](http://localhost:8080) and the live documentation at [http://localhost:8001](http://localhost:8001).

Every `invoke start` and `invoke debug` writes the published host port mappings to `.service_ports.json`, listing only the services that publish a port to the host. When ephemeral ports are enabled, Docker assigns dynamic host ports and this file captures the resulting values; with fixed ports it captures the static values instead. You can also inspect them with `invoke ps` or `docker compose port`, for example `docker compose port nautobot 8080`. To enable ephemeral ports with an environment variable, set `INVOKE_CONTAINERLAB_ORCHESTRATOR_EPHEMERAL_PORTS=1`; to disable them, unset the environment variable, set it to an empty value, or set it to `0`.

To either stop or destroy the development environment use the following options.

- **invoke stop** - Stop the containers, but keep all underlying systems intact
- **invoke destroy** - Stop and remove all containers, volumes, etc. (This results in data loss due to the volume being deleted)

### Local Poetry Development Environment

- Create an `invoke.yml` file with the following contents at the root of the repo and edit as necessary

```yaml
---
containerlab_orchestrator:
  local: true
```

Run the following commands:

```shell
poetry self add poetry-plugin-shell
poetry shell
poetry install --extras nautobot
export $(cat development/development.env | xargs)
export $(cat development/creds.env | xargs)
invoke start && sleep 5
nautobot-server migrate
```

!!! note
    If you want to develop on the latest develop branch of Nautobot, run the following command: `poetry add --optional git+https://github.com/nautobot/nautobot@develop`. After the `@` symbol must match either a branch or a tag.

You can now run `nautobot-server` commands as you would from the [Nautobot documentation](https://docs.nautobot.com/projects/core/en/stable/user-guide/administration/tools/nautobot-server/) for example to start the development server:

```shell
nautobot-server runserver 0.0.0.0:8080 --insecure
```

Nautobot server can now be accessed at [http://localhost:8080](http://localhost:8080).

It is typically recommended to launch the Nautobot **runserver** command in a separate shell so you can keep developing and manage the webserver separately.

### Updating the Documentation

Documentation dependencies are pinned to exact versions to ensure consistent results. For the development environment, they are defined in the `pyproject.toml` file.

### CLI Helper Commands

The project features a CLI helper based on [Invoke](https://www.pyinvoke.org/) to help setup the development environment. The commands are listed below in 3 categories:

- `dev environment`
- `utility`
- `testing`

Each command can be executed with `invoke <command>`. All commands support the arguments `--nautobot-ver` and `--python-ver` if you want to manually define the version of Python and Nautobot to use. Each command also has its own help `invoke <command> --help`

#### Local Development Environment

```
  build            Build all docker images.
  debug            Start Nautobot and its dependencies in debug mode.
  destroy          Destroy all containers and volumes.
  restart          Restart Nautobot and its dependencies in detached mode.
  start            Start Nautobot and its dependencies in detached mode.
  stop             Stop Nautobot and its dependencies.
```

#### Utility

```
  cli              Launch a bash shell inside the running Nautobot container.
  create-user      Create a new user in django (default: admin), will prompt for password.
  makemigrations   Run Make Migration in Django.
  nbshell          Launch a nbshell session.
```

#### Testing

```
  ruff             Run ruff to perform code formatting and/or linting.
  pylint           Run pylint code analysis.
  markdownlint     Run pymarkdown linting.
  tests            Run all tests for this app.
  unittest         Run Django unit tests for the app.
  djlint           Run djlint to perform django template linting.
  djhtml           Run djhtml to perform django template formatting.
```

## Project Overview

This project provides the ability to develop and manage the Nautobot server locally (with supporting services being *Dockerized*) or by using only Docker containers to manage Nautobot. The main difference between the two environments is the ability to debug and use **pdb** when developing locally. Debugging with **pdb** within the Docker container is more complicated, but can still be accomplished by either entering into the container (via `docker exec`) or attaching your IDE to the container and running the Nautobot service manually within the container.

The upside to having the Nautobot service handled by Docker rather than locally is that you do not have to manage the Nautobot server. The [Docker logs](#docker-logs) provide the majority of the information you will need to help troubleshoot, while getting started quickly and not requiring you to perform several manual steps and remembering to have the Nautobot server running in a separate terminal while you develop.

!!! note
	The local environment still uses Docker containers for the supporting services (Postgres, Redis, and RQ Worker), but the Nautobot server is handled locally by you, the developer.

Follow the directions below for the specific development environment that you choose.

## Poetry

Poetry is used in lieu of the "virtualenv" commands and is leveraged in both environments. The virtual environment will provide all of the Python packages required to manage the development environment such as **Invoke**. See the [Local Development Environment](#local-poetry-development-environment) section to see how to install Nautobot if you're going to be developing locally (i.e. not using the Docker container).

The `pyproject.toml` file outlines all of the relevant dependencies for the project:

- `tool.poetry.dependencies` - the main list of dependencies.
- `tool.poetry.group.dev.dependencies` - development dependencies, to facilitate linting, testing, and documentation building.

The `poetry shell` command is used to create and enable a virtual environment managed by Poetry, so all commands ran going forward are executed within the virtual environment. This is similar to running the `source venv/bin/activate` command with virtualenvs. To install project dependencies in the virtual environment, you should run `poetry install` - this will install **both** project and development dependencies.

For more details about Poetry and its commands please check out its [online documentation](https://python-poetry.org/docs/).

In Poetry version 2, the shell command was moved out of the main Poetry project and into a plugin. For more details about the Poetry shell plugin, refer to its [GitHub repository](https://github.com/python-poetry/poetry-plugin-shell).

## Full Docker Development Environment

This project is set up with a number of **Invoke** tasks consumed as simple CLI commands to get developing fast. You'll use a few `invoke` commands to get your environment up and running.

### Copy the credentials file for Nautobot

First, you may create/overwrite the `development/creds.env` file - it stores a bunch of private information such as passwords and tokens for your local Nautobot install. You can make a copy of the `development/creds.example.env` and modify it to suit you.

```shell
cp development/creds.example.env development/creds.env
```

### Invoke - Building the Docker Image

The first thing you need to do is build the necessary Docker image for Nautobot that installs the specific `nautobot_ver`. The image is used for Nautobot and the Celery worker service used by Docker Compose.

```bash
➜ invoke build
... <omitted for brevity>
#14 exporting to image
#14 sha256:e8c613e07b0b7ff33893b694f7759a10d42e180f2b4dc349fb57dc6b71dcab00
#14 exporting layers
#14 exporting layers 1.2s done
#14 writing image sha256:2d524bc1665327faa0d34001b0a9d2ccf450612bf8feeb969312e96a2d3e3503 done
#14 naming to docker.io/containerlab-orchestrator/nautobot:3.2.8-py3.12 done
```

### Invoke - Starting the Development Environment

Next, you need to start up your Docker containers.

```bash
➜ invoke start
Starting Nautobot in detached mode...
Running docker-compose command "up --detach"
Creating network "containerlab_orchestrator_default" with the default driver
Creating volume "containerlab_orchestrator_postgres_data" with default driver
Creating containerlab_orchestrator_redis_1 ...
Creating containerlab_orchestrator_docs_1  ...
Creating containerlab_orchestrator_postgres_1 ...
Creating containerlab_orchestrator_postgres_1 ... done
Creating containerlab_orchestrator_redis_1    ... done
Creating containerlab_orchestrator_nautobot_1 ...
Creating containerlab_orchestrator_docs_1     ... done
Creating containerlab_orchestrator_nautobot_1 ... done
Creating containerlab_orchestrator_worker_1   ...
Creating containerlab_orchestrator_worker_1   ... done
Docker Compose is now in the Docker CLI, try `docker compose up`
```

This will start all of the Docker containers used for hosting Nautobot. You should see the following containers running after `invoke start` is finished.

```bash
➜ docker ps
****CONTAINER ID   IMAGE                            COMMAND                  CREATED          STATUS          PORTS                                       NAMES
ee90fbfabd77   containerlab-orchestrator/nautobot:3.2.8-py3.12  "nautobot-server rqw…"   16 seconds ago   Up 13 seconds                                               containerlab_orchestrator_worker_1
b8adb781d013   containerlab-orchestrator/nautobot:3.2.8-py3.12  "/docker-entrypoint.…"   20 seconds ago   Up 15 seconds   0.0.0.0:8080->8080/tcp, :::8080->8080/tcp   containerlab_orchestrator_nautobot_1
d64ebd60675d   containerlab-orchestrator/nautobot:3.2.8-py3.12  "mkdocs serve -v -a …"   25 seconds ago   Up 18 seconds   0.0.0.0:8001->8080/tcp, :::8001->8080/tcp   containerlab_orchestrator_docs_1
e72d63129b36   postgres:13-alpine               "docker-entrypoint.s…"   25 seconds ago   Up 19 seconds   0.0.0.0:5432->5432/tcp, :::5432->5432/tcp   containerlab_orchestrator_postgres_1
96c6ff66997c   redis:6-alpine                   "docker-entrypoint.s…"   25 seconds ago   Up 21 seconds   0.0.0.0:6379->6379/tcp, :::6379->6379/tcp   containerlab_orchestrator_redis_1
```

Once the containers are fully up, you should be able to open up a web browser, and view:

- The Nautobot homepage at [http://localhost:8080](http://localhost:8080)
- A live version of the documentation at [http://localhost:8001](http://localhost:8001)

!!! note
	Sometimes the containers take a minute to fully spin up. If the page doesn't load right away, wait a minute and try again.

### Invoke - Creating a Superuser

The Nautobot development image will automatically provision a super user when specifying the following variables within `creds.env` which is the default when copying `creds.example.env` to `creds.env`.

- `NAUTOBOT_CREATE_SUPERUSER=true`
- `NAUTOBOT_SUPERUSER_API_TOKEN=0123456789abcdef0123456789abcdef01234567`
- `NAUTOBOT_SUPERUSER_PASSWORD=admin`

!!! note
	The default username is **admin**, but can be overridden by specifying **NAUTOBOT_SUPERUSER_USERNAME**.

If you need to create additional superusers, run the follow commands.

```bash
➜ invoke createsuperuser
Running docker-compose command "ps --services --filter status=running"
Running docker-compose command "exec nautobot nautobot-server createsuperuser --username admin"
Error: That username is already taken.
Username: ntc
Email address: ntc@networktocode.com
Password:
Password (again):
Superuser created successfully.
```

### Invoke - Loading the Demo Data

Run `invoke generate-test-data` to load the demo data. The command makes these objects at the location "Demo DC1":

- The Arista spines `dc1-spine1` and `dc1-spine2`, model DCS-7280CR3-32P4.
- The Arista leafs `dc1-leaf1` to `dc1-leaf4`, model DCS-7050SX3-48YC8.
- The Linux hosts `dc1-host1` to `dc1-host4`. Each host connects `eth1` to `Ethernet1` of the leaf with the same number.
- One cable from each leaf to each spine. Leaf port `Ethernet49` goes to spine 1, and `Ethernet50` goes to spine 2. The leaf number is the spine port, for example `dc1-leaf3` connects to `Ethernet3`.
- A node mapping of each Arista model to the `ceos` kind with the image `ceos:4.37.0F`, and of the Linux model to the `linux` kind with the image `alpine:latest`. Each data port `EthernetN` or `ethN` maps to `ethN`.
- The Arista node mappings use the config source "Golden Config backup", with "Rename interfaces" off. The Linux nodes get no startup config.
- The Git repository "Demo device configs" of [clab-orchestrator-demo](https://github.com/jtdub/clab-orchestrator-demo). It provides Golden Config backup configs at `configs/<device>.cfg`.
- The Golden Config setting "Demo DC1", with the Dynamic Group "Demo DC1 Arista devices" and the GraphQL query "Demo DC1 Golden Config".
- The Containerlab host `demo-clab`. It uses `CLAB_DEV_HOST_URL` and the two `CLAB_DEV_HOST_*` account variables from `creds.env`. See [Local clab-api-server](#local-clab-api-server).
- The lab `demo-spine-leaf` in the Planned status. Its device filter selects the devices of "Demo DC1". The first superuser is its owner.

The demo data uses these prefixes of the Global namespace. Each prefix belongs to "Demo DC1".

| Prefix | Type | Use |
| --- | --- | --- |
| `10.1.0.0/24` | Container | One /31 for each spine to leaf link. The spine gets the even address, and the leaf gets the odd address. |
| `10.1.1.0/24` | Container | One /31 for each leaf to host link. The leaf gets the even address, and the host gets the odd address. |
| `10.20.20.0/24` | Network | The management prefix, in the VRF `MGMT`. |

The management interface of each device is in the VRF `MGMT`. Its address is the primary IPv4 of the device. The lab uses `10.20.20.0/24` as its management subnet. Thus each primary IPv4 is also the management address that containerlab gives the node.

After the command saves the data, it clones the repository and saves the backup of each Arista device in Golden Config. Then it starts the "Git Repository: Sync" job. The Nautobot container must have access to GitHub. If the clone fails, the command gives a warning, and the Arista nodes get no startup config.

If Golden Config is not installed, the command does not make the Golden Config data. The Arista nodes then get no startup config.

A second run makes no duplicate. A second run also loads the backups again.

!!! warning
    The flush deletes each prefix and IP address inside the three demo prefixes, also when you made them. Do not keep your own data in these ranges.

To delete the demo data and load it again, run `invoke generate-test-data --flush`.

The flush keeps the manufacturers, the platforms, and the roles, because other data can use them. It also keeps the VRF `MGMT` when other devices or prefixes use it.

!!! note
    Arista does not publish the cEOS image to a registry. Import the image as `ceos:4.37.0F` on the containerlab host before you deploy the lab. Read the [lab rules for the local server](#lab-rules-for-the-local-server) before you deploy ten nodes.

### Invoke - Stopping the Development Environment

The last command to know for now is `invoke stop`.

```bash
➜ invoke stop
Stopping Nautobot...
Running docker-compose command "down"
Stopping containerlab_orchestrator_worker_1   ...
Stopping containerlab_orchestrator_nautobot_1 ...
Stopping containerlab_orchestrator_docs_1     ...
Stopping containerlab_orchestrator_redis_1    ...
Stopping containerlab_orchestrator_postgres_1 ...
Stopping containerlab_orchestrator_worker_1   ... done
Stopping containerlab_orchestrator_nautobot_1 ... done
Stopping containerlab_orchestrator_postgres_1 ... done
Stopping containerlab_orchestrator_redis_1    ... done
Stopping containerlab_orchestrator_docs_1     ... done
Removing containerlab_orchestrator_worker_1   ...
Removing containerlab_orchestrator_nautobot_1 ...
Removing containerlab_orchestrator_docs_1     ...
Removing containerlab_orchestrator_redis_1    ...
Removing containerlab_orchestrator_postgres_1 ...
Removing containerlab_orchestrator_postgres_1 ... done
Removing containerlab_orchestrator_docs_1     ... done
Removing containerlab_orchestrator_worker_1   ... done
Removing containerlab_orchestrator_redis_1    ... done
Removing containerlab_orchestrator_nautobot_1 ... done
Removing network containerlab_orchestrator_default
```

This will safely shut down all of your running Docker containers for this project. When you are ready to spin containers back up, it is as simple as running `invoke start` again [as seen previously](#invoke-starting-the-development-environment).

!!! warning
	If you're wanting to reset the database and configuration settings, you can use the `invoke destroy` command, but **you will lose any data stored in those containers**, so make sure that is what you want to do.

### Real-Time Updates? How Cool!

Your environment should now be fully setup, all necessary Docker containers are created and running, and you're logged into Nautobot in your web browser. Now what?

Now you can start developing your app in the project folder!

The magic here is the root directory is mounted inside your Docker containers when built and ran, so **any** changes made to the files in here are directly updated to the Nautobot app code running in Docker. This means that as you modify the code in your app folder, the changes will be instantly updated in Nautobot.

!!! warning
	There are a few exceptions to this, as outlined in the section [To Rebuild or Not To Rebuild](#to-rebuild-or-not-to-rebuild).

The back-end Django process is setup to automatically reload itself (it only takes a couple of seconds) every time a file is updated (saved). So for example, if you were to update one of the files like `tables.py`, then save it, the changes will be visible right away in the web browser!

!!! note
	You may get connection refused while Django reloads, but it should be refreshed fairly quickly.

### Docker Logs

When trying to debug an issue, one helpful thing you can look at are the logs within the Docker containers.

```bash
➜ docker logs <name of container> -f
```

!!! note
	The `-f` tag will keep the logs open, and output them in realtime as they are generated.

!!! info
    Want to limit the log output even further? Use the `--tail <#>` command line argument in conjunction with `-f`.

So for example, our app is named `containerlab-orchestrator`, the command would most likely be `docker logs containerlab_orchestrator_nautobot_1 -f`. You can find the name of all running containers via `docker ps`.

If you want to view the logs specific to the worker container, simply use the name of that container instead.

## To Rebuild or Not to Rebuild

Most of the time, you will not need to rebuild your images. Simply running `invoke start` and `invoke stop` is enough to keep your environment going.

However there are a couple of instances when you will want to.

### Updating Environment Variables

To add environment variables to your containers, thus allowing Nautobot to use them, you will update/add them in the `development/development.env` file. However, doing so is considered updating the underlying container shell, instead of Django (which auto restarts itself on changes).

To get new environment variables to take effect, you will need stop any running images, rebuild the images, then restart them. This can easily be done with 3 commands:

```bash
➜ invoke stop
➜ invoke build
➜ invoke start
```

Once completed, the new/updated environment variables should now be live.

### Installing Additional Python Packages

If you want your app to leverage another available Nautobot app or another Python package, you can easily add them into your Docker environment.

```bash
➜ poetry add <package_name>
```

Once the dependencies are resolved, stop the existing containers, rebuild the Docker image, and then start all containers again.

```bash
➜ invoke stop
➜ invoke build
➜ invoke start
```

### Installing Additional Nautobot Apps

Let's say for example you want the new app you're creating to integrate into Slack. To do this, you will want to integrate into the existing Nautobot ChatOps App.

```bash
➜ poetry add nautobot-chatops
```

Once you activate the virtual environment via Poetry, you then tell Poetry to install the new app.

Before you continue, you'll need to update the file `development/nautobot_config.py` accordingly with the name of the new app under `PLUGINS` and any relevant settings as necessary for the app under `PLUGINS_CONFIG`. Since you're modifying the underlying OS (not just Django files), you need to rebuild the image. This is a similar process to updating environment variables, which was explained earlier.

```bash
➜ invoke stop
➜ invoke build
➜ invoke start
```

Once the containers are up and running, you should now see the new app installed in your Nautobot instance.

!!! note
    You can even launch an `ngrok` service locally on your laptop, pointing to port 8080 (such as for chatops development), and it will point traffic directly to your Docker images.

### Updating Python Version

To update the Python version, you can update it within `tasks.py`.

```python
namespace = Collection("containerlab_orchestrator")
namespace.configure(
    {
        "containerlab_orchestrator": {
            ...
            "python_ver": "3.12",
	    ...
        }
    }
)
```

Or set the `INVOKE_CONTAINERLAB_ORCHESTRATOR_PYTHON_VER` variable.

### Updating Nautobot Version

To update the Nautobot version, you can update it within `tasks.py`.

```python
namespace = Collection("containerlab_orchestrator")
namespace.configure(
    {
        "containerlab_orchestrator": {
            ...
            "nautobot_ver": "3.2.8",
	    ...
        }
    }
)
```

Or set the `INVOKE_CONTAINERLAB_ORCHESTRATOR_NAUTOBOT_VER` variable.

## Local clab-api-server

The file `development/docker-compose.clab.yml` adds an optional clab-api-server container to the dev environment. The app jobs can then deploy labs on your own computer. The stack starts the container only when `compose_files` has the file.

!!! warning
    The container is privileged, uses the host PID namespace, and has the Docker socket. So it has root control of the Docker host. It can stop or remove any container, and it can read the environment of each container, the Nautobot secret key included. On Docker Desktop it can also read and write the folders that Docker Desktop shares from your computer, for example `/Users`.

    The container uses host networking, so Docker Desktop opens port 8090 on all the network interfaces of your computer. Any computer on your network can reach the server. With the dev password, that computer gets root control of the Docker host. If a client asks the server for an SSH session, the server also opens a port from 2223 to 2322 on all interfaces. The app does not ask for SSH sessions.

    Use the container only on a dev computer on a network that you trust. Change the dev password. Stop the container when you do not use it.

To turn on the local server:

1. On Docker Desktop, turn on Settings > Resources > Network > "Enable host networking", then click "Apply & restart". The server must use the network of the Docker host, because containerlab must see the bridges of the lab networks that it makes.
2. Make sure that `development/creds.env` has these lines. An existing `creds.env` is a copy of an older `creds.example.env`, so it can be without them. Add the missing lines, and change the password. Use a password without `$`, `#`, quotes, or spaces. If `invoke start` stops with "required variable CLAB_DEV_HOST_USERNAME is missing a value", `creds.env` does not have these lines.

    ```shell
    CLAB_DEV_HOST_URL=https://host.docker.internal:8090
    CLAB_DEV_HOST_USERNAME=nautobot
    CLAB_DEV_HOST_PASSWORD=changeme
    ```

    The container does not start with the example password `changeme`. It stops with the message "Change CLAB_DEV_HOST_PASSWORD in development/creds.env. The example value is public." To see the message, run `invoke logs --service clab-api-server`.

3. Add `docker-compose.clab.yml` to `compose_files` in `invoke.yml`. The list replaces the default list, so also give the four default files. `invoke.example.yml` has an example.

    ```yaml
    ---
    containerlab_orchestrator:
      compose_files:
        - "docker-compose.base.yml"
        - "docker-compose.redis.yml"
        - "docker-compose.postgres.yml"
        - "docker-compose.dev.yml"
        - "docker-compose.clab.yml"
    ```

4. Run `invoke start`.
5. Make sure that the login works from the worker. Run `invoke cli --service worker`, then the command below. The result has a `token` key.

    ```shell
    curl -k -X POST https://host.docker.internal:8090/login \
      -H "Content-Type: application/json" \
      -d "{\"username\": \"$CLAB_DEV_HOST_USERNAME\", \"password\": \"$CLAB_DEV_HOST_PASSWORD\"}"
    ```

!!! note
    The `ephemeral_ports` setting has no effect when `compose_files` is not the default list. If port 8080 or 8001 is in use, add `docker-compose.ephemeral-ports.yml` to the end of the list.

When `compose_files` has the file, `invoke` gives these values to the container:

- `CLAB_DEV_HOST_USERNAME` and `CLAB_DEV_HOST_PASSWORD` from `creds.env`. The container gets no other value from `creds.env`, so it does not get the Nautobot secrets.
- `CLAB_LABS_ROOT`, the absolute path of `development/.clab-labs`. Git ignores this folder. The server keeps the topology file of each lab there.

At each start, the container makes the account `CLAB_DEV_HOST_USERNAME` in the group `clab_api`. It also locks the `admin` account of the image and makes a random JWT secret. A token from an earlier start does not work.

### Add the local server in Nautobot

1. Make two Secrets with the "Environment Variable" provider. One reads the variable `CLAB_DEV_HOST_USERNAME`, and one reads `CLAB_DEV_HOST_PASSWORD`. The Nautobot and worker containers get these variables from `creds.env`.
2. Make a Secrets Group. Add the username Secret with the access type HTTP(S) and the secret type Username.
3. Add the password Secret with the access type HTTP(S) and the secret type Password.
4. Make a Containerlab host with the URL `https://host.docker.internal:8090` and the Secrets Group. Turn "Verify SSL" off, because the server uses a self-signed certificate.
5. Turn on the four lab jobs. Do the steps of "Turn on the job" for [Build Lab Topology](../user/app_use_cases.md#turn-on-the-job), [Deploy Lab](../user/app_use_cases.md#turn-on-the-job_1), [Destroy Lab](../user/app_use_cases.md#turn-on-the-job_2), and [Inspect Lab](../user/app_use_cases.md#turn-on-the-job_4). Nautobot adds each job turned off, so the lab page shows no button for it.

### Lab rules for the local server

- The Docker Desktop VM holds the Nautobot stack and all the lab nodes in its memory. Run one lab at a time. Run `docker stats --no-stream` before a deploy. Do not deploy when less than 2 GB is free.
- The management subnet of a lab must not overlap a Docker network. Docker gives each compose project a network, for example `172.31.0.0/16`. Before you use a subnet, look at the subnet of each network:

    ```shell
    for n in $(docker network ls -q); do docker network inspect $n --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'; done
    ```

- Pull each node image before the deploy, for example `docker pull alpine:latest`. The Deploy Lab job stops when an image is not on the host.
- Destroy each lab when you do not use it.

### Switch the Nautobot version

A database of a newer Nautobot version does not work with an older version. Start each version from an empty database:

1. Destroy each lab.
2. Run `invoke destroy`.
3. Set `nautobot_ver` in `invoke.yml`, for example `"3.2.9"`.
4. Run `invoke build start`.
5. Add the Secrets, the Secrets Group, and the host again.

### Turn off the local server

1. Destroy each lab.
2. Remove `docker-compose.clab.yml` from `compose_files`.
3. Run `invoke stop`.
4. Delete `development/.clab-labs/`.
5. Remove each lab container that stays. `docker ps -a --filter label=containerlab` shows them.
6. Remove each lab network that stays. `docker network ls --filter label=containerlab` shows them.

### Differences from the upstream container method

The upstream `docker run` method of clab-api-server is for a Linux host. The dev container is different:

- It does not mount `/etc/passwd`, `/etc/shadow`, `/etc/group`, `/etc/gshadow`, or `/home`. On Docker Desktop they would come from macOS, so the container uses its own accounts.
- It does not mount `/var/run/netns`. The upstream entrypoint makes that folder in the container.
- It does not mount `/var/lib/docker/containers`. Only the container logs need it, and the app does not read logs.
- It does not mount `/var/lib/clab-api-server`. The server keeps its restore and `fcling` files there, so a new container does not have them. The app does not use restore or `fcling`.
- It uses the image tag `v0.10.0` with its digest. The upstream example uses `latest`.
- It keeps the lab files in `CLAB_LABS_ROOT`, at the same path on the Docker host and in the container, in place of `/home`.
- It locks the `admin` account of the image and makes a random JWT secret at each start.

## Other Miscellaneous Commands To Know

### Python Shell

To drop into a Django shell for Nautobot (in the Docker container) run:

```bash
➜ invoke nbshell
```

This is the same as running:

```bash
➜ invoke cli
➜ nautobot-server nbshell
```

### iPython Shell Plus

Django also has a more advanced shell that uses iPython and that will automatically import all the models:

```bash
➜ invoke shell-plus
```

This is the same as running:

```bash
➜ invoke cli
➜ nautobot-server shell_plus
```

### Tests

To run tests against your code, you can run all of the tests that the CI runs against any new PR with:

```bash
➜ invoke tests
```

To run an individual test, you can run any or all of the following:

```bash
➜ invoke unittest
➜ invoke ruff
➜ invoke pylint
```

### App Configuration Schema

In the package source, there is the `containerlab_orchestrator/app-config-schema.json` file, conforming to the [JSON Schema](https://json-schema.org/) format. This file is used to validate the configuration of the app in CI pipelines.

If you make changes to `PLUGINS_CONFIG` or the configuration schema, you can run the following command to validate the schema:

```bash
invoke validate-app-config
```

To generate the `app-config-schema.json` file based on the current `PLUGINS_CONFIG` configuration, run the following command:

```bash
invoke generate-app-config-schema
```

This command can only guess the schema, so it's up to the developer to manually update the schema as needed.
