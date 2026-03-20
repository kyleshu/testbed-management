# testbed-management

Tools for managing GPU testbeds on Lambda Cloud.

## `lambda_grab.py`

Polls Lambda Cloud for GPU instance availability and auto-launches instances the moment capacity appears. Notifies you via Slack and/or email when instances are grabbed. For multi-instance jobs, prints private network setup instructions so nodes can communicate over Lambda's internal 10.x.x.x network.

### Requirements

- Python 3.10+
- No external dependencies (stdlib only)

### Setup

```bash
export LAMBDA_API_KEY="your_api_key"          # required
export SLACK_WEBHOOK_URL="https://hooks.slack.com/..."  # optional
```

Add these to `~/.bashrc` to persist across sessions.

### Usage

#### List available instance types

```bash
python lambda_grab.py list
```

Shows all instance types, GPU/vCPU/RAM specs, and which regions currently have capacity.

#### Grab instances

```bash
python lambda_grab.py grab \
    --instance-types gpu_8x_h100_sxm4 gpu_8x_a100 \
    --count 2 \
    --ssh-keys key1 key2 \
    --name my-experiment \
    [--region us-west-1] \
    [--poll-interval 30] \
    [--filesystems fs-abc123] \
    [--dry-run]
```

- `--instance-types` — ordered preference list; the first type with available capacity wins
- `--count` — number of instances to launch
- `--ssh-keys` — one or more SSH key names registered in your Lambda Cloud account
- `--region` — preferred region; falls back to any available region if not found
- `--poll-interval` — seconds between API polls (default: 30)
- `--name` — base name for launched instances (default: `exp-<timestamp>`)
- `--filesystems` — Lambda filesystem names to attach (optional)
- `--dry-run` — print what would happen without actually launching

### Notifications

| Method | How to enable |
|--------|--------------|
| Terminal bell + stdout | Always on |
| Slack | Set `SLACK_WEBHOOK_URL` env var |
| Email | Set `NOTIFY_EMAIL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` |

### Private networking

When multiple instances are launched, the script prints `/etc/hosts` entries and a one-liner to run on each node. Lambda instances in the same region share a `10.x.x.x` private network automatically — no VPN or overlay needed.

```
Private network setup
────────────────────────────────────────
All instances share Lambda's private 10.x.x.x network.
Add the following to /etc/hosts on EACH instance:

  10.10.1.1  my-experiment-0
  10.10.1.2  my-experiment-1

Run this one-liner on each instance to populate /etc/hosts:

  sudo tee -a /etc/hosts << 'EOF'
  10.10.1.1  my-experiment-0
  10.10.1.2  my-experiment-1
  EOF
```
