# Docker Desktop hangs on Michael's Mac: observations

Running notes from the overnight session of 2026-11-02, for finding why Docker Desktop stops starting containers. Each entry says what was seen; conclusions are at the end, marked as such.

Host: 36 GB RAM, Docker Desktop with the Apple Virtualization framework and Rosetta for amd64, VM memory 8 GB, 14 CPUs, swap 1 GB, disk image 150 GB. The host disk is 95% full (46 GB free).

## Observations

- **2026-11-01 ~18:30-18:56.** Three test containers in a row (`fc-l1-turn-*`, `fc-l1-21-turn-*`, `fc-l2-turn-*`, all `--platform linux/amd64`) stayed in `Created` for 300 s until the scripts' timeout killed the `docker run`; `docker ps -a` kept them as `Created`. `docker info` answered at once. A Docker Desktop restart fixed it; the stuck containers had to be removed by hand.
- **2026-11-02 04:04.** The probe `docker run --rm hello-world` and the amd64 probe both failed their 60/90 s timeouts while an L1 container left by an interrupted run was starting. Load average 10 (the e2e and app gateways were also up). Host memory: 50% free, swap 765 MB used. Four seconds later `docker ps` showed that L1 container `Up 4 seconds`: the daemon was slow, not dead. 20 s later, with that container gone, both probes took under 1 s. No restart needed.

## Leads (not conclusions yet)

- Both episodes had a container being started while an earlier `docker run` of the same image had just been killed by `timeout -k`: the scripts kill the client, not the container start, so a start can still be in flight in the VM when the next one comes.
- amd64 images run through Rosetta; a stuck start was only ever seen on amd64 containers so far.
