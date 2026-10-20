# Running tapo-web with Docker

The image holds the web app and ffmpeg. Animal detection is not in it yet, see below.

## With Docker Compose

```bash
git clone https://github.com/freeKC/tapo-web && cd tapo-web
mkdir -p config data
docker compose up -d --build
```

Then open http://localhost:8088 on the same machine.

The container runs as user id 1000. Create `config` and `data` yourself before the first start:
if Docker creates them, they belong to root and the app cannot write there. If your own user id
is not 1000 (`id -u`), give them to 1000: `sudo chown -R 1000:1000 config data`.

`docker-compose.yml` uses host networking. The app looks for the camera on the LAN (UDP
discovery on port 20002, then a scan of the subnet) and talks to it directly over RTSP, ONVIF,
the control API and the media port, which is simplest when the container shares the host's
network.

## With plain docker run

```bash
docker build -t tapo-web .
docker run -d --name tapo-web --network host --restart unless-stopped \
  -v "$PWD/config:/config" -v "$PWD/data:/data" tapo-web
```

## Docker Desktop on Windows or macOS

Host networking does not reach the LAN the same way there. In `docker-compose.yml`, remove
`network_mode: host`, uncomment `ports: ["8088:8088"]` and set `TAPO_SUBNET` to your LAN (the
app scans it address by address, which works through Docker's NAT). With docker run, that is
`-p 8088:8088 -e TAPO_SUBNET=192.168.1` instead of `--network host`.

The app only accepts settings changes from the machine it runs on. With published ports, it
sees requests coming from the Docker network rather than from 127.0.0.1. Set `TAPO_ADMIN_HOSTS`
to the address the requests come from (shown in `docker compose logs`, often `172.17.0.1`), or
change the files in `config/` and restart the container.

## Where things live

| On the host | In the container | What |
|---|---|---|
| `config/` | `/config` | `cameras.json` with the cameras and their accounts, `.env` with the other settings |
| `data/` | `/data` | `recordings/`: your recordings, the copies of SD card clips (`recordings/sd/`), thumbnails and the detection results. The first camera uses `recordings/` itself, the others a sub-folder each. |

Live view segments stay inside the container and are temporary. The server output goes to
`docker compose logs tapo-web`.

There is no login on the web UI: anyone who can reach port 8088 can watch the camera and the
recordings. Keep it on a network you trust.

## Setting up the cameras

Open the web UI: on the first start the settings open by themselves, look for cameras on the
network and walk you through the two accounts. More cameras are added there too.

The first camera can also be set up beforehand with environment variables, either in
`docker-compose.yml` or as lines in `config/.env` (same format as `.env.example`, and the
passwords stay out of the compose file). They are copied into `config/cameras.json` at the first
start, and from then on the web UI is where cameras change:

| Variable | What |
|---|---|
| `TAPO_HOST` | The camera's IP, for example 192.168.1.50. Optional, the app looks for it otherwise. |
| `TAPO_SUBNET` | The first three parts of your LAN addresses, for example 192.168.1 (default 192.168.0). |
| `TAPO_USER`, `TAPO_PASSWORD` | The camera account from the Tapo app (Advanced settings, Camera account), for the live view and ONVIF. |
| `TAPO_CLOUD_PASSWORD` | Your TP-Link account password, for the camera's control API and media port. It is only ever sent to the camera. |
| `TAPO_CONTROL_PROTOCOL` | `auto` (default), `4` or `3`, see [CAMERAS.md](CAMERAS.md). |
| `TAPO_ADMIN_HOSTS` | Other addresses allowed to change the settings, comma separated (see above). |

A variable set in `docker-compose.yml` wins over the same one in `config/.env`, so if you set
something there, change it there too. Once `cameras.json` exists, the camera variables are no
longer read.

The SD card calendar uses the camera's own time zone. The names of local recordings use the
container's clock, which is UTC unless you set `TZ` (for example `TZ: Europe/Paris`).

One copy of the app handles all your cameras. Do not run two copies against the same camera:
it handles one media session at a time, so they get in each other's way.

## Animal detection

Not in the image yet. It needs PyTorch, DeepFaune and the model weights (about 8 GB), set up
on Linux by `ml/setup.sh` outside Docker. Without it the rest of the app works normally and the
background analysis simply does not start.

## Updating

```bash
git pull
docker compose build --pull
docker compose up -d
```

Settings and recordings are in `config/` and `data/`, so they survive the new container. With
docker run: `docker build --pull -t tapo-web .`, `docker rm -f tapo-web`, then the same
`docker run` as above.
