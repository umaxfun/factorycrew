# Docker Desktop hangs on Michael's Mac: observations

Running notes from the overnight session of 2026-11-02, for finding why Docker Desktop stops starting containers. Each entry says what was seen; conclusions are at the end, marked as such.

Host: 36 GB RAM, Docker Desktop with the Apple Virtualization framework and Rosetta for amd64, VM memory 8 GB, 14 CPUs, swap 1 GB, disk image 150 GB. The host disk is 95% full (46 GB free).

## Observations

- **2026-11-01 ~18:30-18:56.** Three test containers in a row (`fc-l1-turn-*`, `fc-l1-21-turn-*`, `fc-l2-turn-*`, all `--platform linux/amd64`) stayed in `Created` for 300 s until the scripts' timeout killed the `docker run`; `docker ps -a` kept them as `Created`. `docker info` answered at once. A Docker Desktop restart fixed it; the stuck containers had to be removed by hand.
- **2026-11-02 04:04.** The probe `docker run --rm hello-world` and the amd64 probe both failed their 60/90 s timeouts while an L1 container left by an interrupted run was starting. Load average 10 (the e2e and app gateways were also up). Host memory: 50% free, swap 765 MB used. Four seconds later `docker ps` showed that L1 container `Up 4 seconds`: the daemon was slow, not dead. 20 s later, with that container gone, both probes took under 1 s. No restart needed.

- **2026-11-02 05:05.** After 35 min with no test containers (the app e2e uses none), eight `docker run --rm --platform linux/amd64 -v <worktree>:/work factorycrew-test:2.0.77 true` in a row each hit their 120 s timeout and stayed `Created`. Then even a container with no mount and arm64 `hello-world` hung: the stuck starts block the ones after them. Docker Desktop's own log (`~/Library/Containers/com.docker.docker/Data/log/host/com.docker.backend.log*`) shows only the create, then nothing for the container: the start hangs inside the VM. After a restart the same three runs (no mount, empty dir mount, worktree mount) took 0.2 s each, nine times in a row.
- **Not reproduced on a fresh daemon:** a stand-like container (amd64, two published ports, living 60 s), its `docker rm -f`, and starts before, during and after it: all 0 s (`tools/docker_wedge_probe.sh`).
- **The Mac sleeps under the runs.** `pmset -g log`: "Clamshell Sleep" at 2026-11-01 20:04, on battery, while a full e2e was running (that run never reported back; its 30-min tool call timed out), then DarkWake/sleep every ~17 min until 23:08; again 00:42-03:53. The 04:04 stall came 11 min after the full wake at 03:53.

## Docker Desktop settings that bear on it

From `~/Library/Group Containers/group.com.docker/settings-store.json`: `UseVirtualizationFrameworkVirtioFS: false` (bind mounts go through the older gRPC FUSE file sharing, not VirtioFS), `UseResourceSaver: true` with `AutoPauseTimeoutSeconds: 300`, `UseVirtualizationFrameworkRosetta: true`, VM memory 8 GB, swap 1 GB.

## What this says (conclusions, with how sure)

- **Sure:** once one container start hangs, every later start queues behind it, whatever its image or mounts, until Docker Desktop restarts. The scripts' `timeout -k` kills only the client and leaves the container `Created`, so a test run that retries just adds more stuck starts. A restart always cleared it tonight.
- **Sure:** closing the lid on battery sleeps the Mac mid-run (20:04). A run in flight then dies or hangs, and the first starts after a long sleep can stall (04:04).
- **Likely, not proven:** the stall itself is in the VM's container start path after the VM sat idle or the host slept, with the legacy file sharing (VirtioFS off) and Rosetta both in that path. It did not reproduce on a fresh daemon in five minutes of trying.

## What to do

- Keep the probe before every Docker test (`scripts/lib/docker.sh` `docker_ok` already starts an amd64 container and fails in 60 s with "restart Docker Desktop"); on a failed probe restart Docker Desktop instead of retrying.
- Turn on VirtioFS (Docker Desktop → Settings → General → "VirtioFS" file sharing): it is Docker's default and the most likely single fix for stuck starts with bind mounts. Not changed tonight: it is your setting, and changing it restarts the VM.
- For long unattended runs keep the Mac on power with the lid open, or run them under `caffeinate -ims` (prevents idle sleep on AC; nothing prevents clamshell sleep on battery).

