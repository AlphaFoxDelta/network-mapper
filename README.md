# network-mapper

Point it at a subnet and get back a visual map of what's alive: an HTML
page with a topology diagram, every live host labeled with its IP,
hostname, and open ports. Standard library only.

## Why I built this

My other projects all print text. Scanners print tables, detectors print
reports, and that's fine for me, but the moment you need to show someone
else what's on a network, a wall of text doesn't cut it. I wanted
something I could pull up in a meeting and have everyone understand in
five seconds. A picture of the network beats a spreadsheet of the network.

It also scratched a curiosity itch. I'd used tools that draw network maps
but never thought about how the discovery actually works underneath.
Turns out it's simpler than I expected, and harder in exactly one place
(more on that below).

## What it does

- Takes a CIDR (192.168.1.0/24), a single IP, or a range (10.0.0.1-20)
- Phase 1, host discovery: threaded TCP connects against a few common
  ports per host. A connect or a refused connection both mean the host is
  alive. Only timeouts mean dead or filtered
- Phase 2, port scan: every live host gets a quick scan of common ports
  with service guesses, plus a reverse DNS lookup for a hostname
- Writes map.html: a self-contained page with an SVG topology diagram
  (radial layout, scanner in the middle, spokes out to each host),
  color-coded by how many ports are open, with a results table underneath
- Prints a text summary too, and --json gives you machine-readable output

## Usage

```bash
# map a /24
python3 mapper.py 192.168.1.0/24

# smaller range, custom ports
python3 mapper.py 10.0.0.1-20 --ports 22,80,443

# JSON output, custom file name
python3 mapper.py 192.168.1.0/24 --json --output office.html
```

### Options

| Flag              | Default            | Description                              |
|-------------------|--------------------|------------------------------------------|
| `target`          | (required)         | CIDR, single IP, or range like 10.0.0.1-20 |
| `--ports`         | 20 common ports    | ports to scan on live hosts              |
| `--discovery-ports` | 80,443,22        | ports used for host discovery            |
| `--threads`       | `100`              | concurrent workers                       |
| `--timeout`       | `1.0`              | per-connection timeout in seconds        |
| `--output`        | `map.html`         | HTML map file to write                   |
| `--json`          | off                | print machine-readable JSON to stdout    |

## What the map looks like

The page opens with a summary line (how many hosts scanned, how many
alive, how long it took), then the diagram: a blue node in the center
labeled "scanner" with lines radiating out to one node per live host.
Each host node shows its IP, with the hostname underneath if one
resolved. The color tells you how exposed a host is at a glance. Gray
means the host answered but none of the scanned ports were open. Green is
1-2 ports, amber is 3-5, red is 6 or more. Hovering any node pops up the
full port list. Under the diagram there's a plain table with every host,
hostname, and open ports, so you can copy from it.

## Requirements

Python 3.8+. Nothing to install.

## What tripped me up

The big one: no raw sockets without root. The textbook way to find live
hosts is a ping sweep with ICMP, but crafting ICMP packets needs raw
sockets, which need root. I didn't want a tool that only works with sudo,
so discovery is TCP-based instead. Try to connect to a few common ports
on each host. A successful connect means alive, obviously, but a refused
connection also means alive, because only a live host sends back a RST.
Timeouts are the only thing that means dead or filtered. That one insight
is the whole discovery engine.

The other thing was reverse DNS. `socket.gethostbyaddr()` is a blocking
call with no timeout parameter, and on some networks it just hangs for a
while before giving up. It runs inside the thread pool alongside the port
scans, so a slow resolver doesn't stall the whole run, but it was the
long pole on my first test against a bigger subnet. Lesson learned: DNS
is always the slow part.

## What I'd do differently

- Add OS guessing from TTL and TCP window sizes. Right now the map tells
  you what's open, not what's running underneath.
- Let the HTML page refresh itself for continuous monitoring instead of
  being a one-shot snapshot.
- Banner grabbing on open ports, like my port scanner does. I left it out
  to keep this project focused on the mapping part.
- An option to export the diagram as PNG for slide decks. SVG is great in
  a browser, less great pasted into PowerPoint.

## A note on using this

Only map networks you own or are explicitly authorized to scan. A
subnet-wide scan is noisy by design: you're knocking on every door.
That's fine in your own lab and a real problem on someone else's network.
Get permission first.
