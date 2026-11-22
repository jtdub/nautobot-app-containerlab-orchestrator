# External Interactions

This page lists the calls that the app makes to other systems, and the APIs that the app gives to other systems.

## External System Integrations

### From the App to Other Systems

The app calls the clab-api-server REST API of each containerlab host. It does not use SSH. Only the lab jobs make
the calls, and Nautobot runs the jobs on the workers. The Build Lab Topology job makes no call.

| Call | Jobs | What it does |
| --- | --- | --- |
| `POST /login` | Deploy Lab, Redeploy Lab, Destroy Lab, Inspect Lab, Sync Labs | Sends the HTTP(S) username and password of the Secrets Group of the host, and gets a token. |
| `GET /api/v1/images` | Deploy Lab, Redeploy Lab | Gets the images on the host. The job stops when the host does not have an image of a node. |
| `POST /api/v1/labs` | Deploy Lab, Redeploy Lab | Deploys the lab from its topology YAML. |
| `GET /api/v1/labs/<lab>` | Deploy Lab, Redeploy Lab, Inspect Lab, Sync Labs | Gets the containers of the lab. |
| `DELETE /api/v1/labs/<lab>` | Destroy Lab, Redeploy Lab | Destroys the lab. |

Each call after the login sends the token in the header `Authorization: Bearer <token>`. When the server refuses an
earlier token with HTTP 401, the job logs in again and sends the call one more time.

The connection timeout of each call is 10 seconds. The `request_timeout` setting gives the other time limits. Refer
to [App Configuration](../admin/install.md#app-configuration).

#### TLS and proxies

- The "Verify SSL" setting of the host turns the check of the server certificate on or off. The app has no setting
  for a CA file. Refer to [TLS](../admin/install.md#tls).
- The calls use the `HTTP_PROXIES` setting of Nautobot only. The `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, and
  `REQUESTS_CA_BUNDLE` environment variables have no effect on them.

### From Other Systems to the App

#### Nautobot REST API endpoints

The app adds these endpoints under `/api/plugins/containerlab-orchestrator/`:

| Endpoint | Objects |
| --- | --- |
| `containerlab-hosts/` | Containerlab hosts |
| `node-mappings/` | Node mappings |
| `interface-mappings/` | Interface mappings |
| `labs/` | Labs |
| `labs/<id>/topology/` | The topology YAML of a lab. Refer to [Review and download the YAML](app_use_cases.md#review-and-download-the-yaml). |
| `lab-nodes/` | Lab nodes. This endpoint is read-only. |

This command gets the labs:

```shell
curl -H "Authorization: Token $TOKEN" -H "Accept: application/json" \
  "https://<nautobot>/api/plugins/containerlab-orchestrator/labs/"
```

#### GraphQL

GraphQL has the types `containerlab_hosts`, `node_mappings`, `interface_mappings`, `labs`, and `lab_nodes`. This
query gets each lab with its host, its status, and its nodes:

```graphql
query {
  labs {
    name
    host {
      name
    }
    status {
      name
    }
    lab_nodes {
      node_name
      mgmt_ipv4
    }
  }
}
```
