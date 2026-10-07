#!/usr/bin/env python3
"""
network-mapper: map a subnet and render a visual topology diagram.

Phase 1 figures out which hosts are alive using TCP connects (no root
needed for ICMP, so we lean on the connect/refused trick instead).
Phase 2 scans the common ports on every live host and guesses services.

Output is a self-contained map.html with an inline SVG diagram, plus a
plain text summary on stdout. Standard library only.
"""

import argparse
import concurrent.futures
import errno
import html
import json
import socket
import sys
import time
from ipaddress import ip_address, ip_network

COMMON_PORTS = [
    21, 22, 23, 25, 53, 80, 110, 135, 139, 143,
    443, 445, 993, 995, 1723, 3306, 3389, 5900, 8080, 8443,
]

DISCOVERY_PORTS = [80, 443, 22]

SERVICE_NAMES = {
    21: "ftp",
    22: "ssh",
    23: "telnet",
    25: "smtp",
    53: "dns",
    80: "http",
    110: "pop3",
    135: "msrpc",
    139: "netbios",
    143: "imap",
    443: "https",
    445: "smb",
    993: "imaps",
    995: "pop3s",
    1723: "pptp",
    3306: "mysql",
    3389: "rdp",
    5900: "vnc",
    8080: "http-alt",
    8443: "https-alt",
}


def parse_ports(spec):
    """Turn '22,80,443' or '1-1024' into a sorted list of ints."""
    ports = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, end = chunk.split("-", 1)
            start, end = int(start), int(end)
            if not (0 < start <= 65535 and 0 < end <= 65535 and start <= end):
                raise ValueError("bad port range: %r" % chunk)
            ports.update(range(start, end + 1))
        else:
            port = int(chunk)
            if not 0 < port <= 65535:
                raise ValueError("bad port: %r" % chunk)
            ports.add(port)
    return sorted(ports)


def parse_targets(target):
    """Accept a CIDR, a single IP/hostname, or a range like 10.0.0.1-10.0.0.20
    (short form 10.0.0.1-20 also works). Returns a list of IP strings."""
    target = target.strip()
    if "/" in target:
        net = ip_network(target, strict=False)
        return [str(h) for h in net.hosts()]
    if "-" in target:
        start_s, end_s = target.split("-", 1)
        start = ip_address(start_s.strip())
        end_s = end_s.strip()
        if "." not in end_s:
            prefix = str(start).rsplit(".", 1)[0]
            end = ip_address(prefix + "." + end_s)
        else:
            end = ip_address(end_s)
        if int(end) < int(start):
            raise ValueError("range end is before range start")
        return [str(ip_address(i)) for i in range(int(start), int(end) + 1)]
    return [str(ip_address(target))]


def host_is_alive(ip, ports, timeout):
    """A host is alive if any discovery port connects OR refuses the
    connection. Refused means something answered with RST, which only a
    live host does. Timeouts mean dead or filtered."""
    for port in ports:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            rc = s.connect_ex((ip, port))
            s.close()
            if rc == 0 or rc == errno.ECONNREFUSED:
                return True
        except OSError:
            continue
    return False


def scan_ports(ip, ports, timeout):
    found = []
    for port in ports:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            if s.connect_ex((ip, port)) == 0:
                found.append({
                    "port": port,
                    "service": SERVICE_NAMES.get(port, "unknown"),
                })
            s.close()
        except OSError:
            continue
    return found


def resolve_hostname(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return None


def map_host(ip, ports, timeout):
    return {
        "ip": ip,
        "hostname": resolve_hostname(ip),
        "open_ports": scan_ports(ip, ports, timeout),
    }


def node_color(open_count):
    if open_count == 0:
        return "#9aa0a6"
    if open_count <= 2:
        return "#34a853"
    if open_count <= 5:
        return "#fbbc04"
    return "#ea4335"


def build_html(target_label, hosts, elapsed, total_scanned):
    n = len(hosts)
    width, height = 900, 640
    cx, cy = width // 2, 300
    radius = min(260, max(150, n * 22)) if n else 0

    svg = []
    svg.append('<svg viewBox="0 0 %d %d" width="100%%" '
               'xmlns="http://www.w3.org/2000/svg" role="img">' % (width, height))

    import math
    positions = []
    for i in range(n):
        angle = -math.pi / 2 + (2 * math.pi * i / n) if n > 1 else 0
        x = cx + radius * math.cos(angle)
        y = cy + radius * 0.78 * math.sin(angle)
        positions.append((x, y))

    # spokes
    for x, y in positions:
        svg.append('<line x1="%d" y1="%d" x2="%.1f" y2="%.1f" '
                   'stroke="#dadce0" stroke-width="1.5"/>' % (cx, cy, x, y))

    # host nodes
    for host, (x, y) in zip(hosts, positions):
        color = node_color(len(host["open_ports"]))
        ports = ", ".join("%d (%s)" % (p["port"], p["service"])
                          for p in host["open_ports"]) or "none of the scanned ports open"
        label = host["ip"]
        sub = host["hostname"] or ""
        tip = "%s%s\n%s" % (host["ip"],
                            " (%s)" % host["hostname"] if host["hostname"] else "",
                            ports)
        svg.append('<g>')
        svg.append('<title>%s</title>' % html.escape(tip))
        svg.append('<circle cx="%.1f" cy="%.1f" r="16" fill="%s" '
                   'stroke="#ffffff" stroke-width="2"/>' % (x, y, color))
        svg.append('<text x="%.1f" y="%.1f" text-anchor="middle" '
                   'font-family="monospace" font-size="11">%s</text>'
                   % (x, y + 32, html.escape(label)))
        if sub:
            svg.append('<text x="%.1f" y="%.1f" text-anchor="middle" '
                       'font-family="sans-serif" font-size="9" fill="#5f6368">%s</text>'
                       % (x, y + 45, html.escape(sub)))
        svg.append('</g>')

    # center node: the scanner
    svg.append('<circle cx="%d" cy="%d" r="24" fill="#1a73e8" '
               'stroke="#ffffff" stroke-width="2"/>' % (cx, cy))
    svg.append('<text x="%d" y="%d" text-anchor="middle" '
               'font-family="sans-serif" font-size="11" fill="#ffffff">scan</text>'
               % (cx, cy + 4))
    svg.append('<text x="%d" y="%d" text-anchor="middle" '
               'font-family="sans-serif" font-size="10" fill="#5f6368">this machine</text>'
               % (cx, cy + 42))

    # legend
    legend = [("#9aa0a6", "alive, no scanned ports open"),
              ("#34a853", "1-2 open ports"),
              ("#fbbc04", "3-5 open ports"),
              ("#ea4335", "6+ open ports")]
    lx = 20
    for color, text in legend:
        svg.append('<circle cx="%d" cy="600" r="7" fill="%s"/>' % (lx, color))
        svg.append('<text x="%d" y="604" font-family="sans-serif" '
                   'font-size="11">%s</text>' % (lx + 14, text))
        lx += 14 + len(text) * 6 + 30

    svg.append("</svg>")
    svg_block = "\n".join(svg)

    # table of results under the diagram
    rows = []
    for host in hosts:
        ports = ", ".join("%d (%s)" % (p["port"], p["service"])
                          for p in host["open_ports"]) or "-"
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            html.escape(host["ip"]),
            html.escape(host["hostname"] or "-"),
            html.escape(ports)))
    table = ("\n".join(rows) if rows
             else '<tr><td colspan="3">No live hosts found.</td></tr>')

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Network map: %s</title>
<style>
body { font-family: sans-serif; max-width: 960px; margin: 2em auto; padding: 0 1em; color: #202124; }
h1 { font-size: 1.4em; }
p.meta { color: #5f6368; }
table { border-collapse: collapse; width: 100%%; margin-top: 1em; }
th, td { border: 1px solid #dadce0; padding: 6px 10px; text-align: left; font-size: 0.9em; }
th { background: #f1f3f4; }
td:first-child, th:first-child { font-family: monospace; }
</style>
</head>
<body>
<h1>Network map: %s</h1>
<p class="meta">Scanned %d hosts in %.1f seconds. %d alive. Hover a node for details.</p>
%s
<table>
<tr><th>IP</th><th>Hostname</th><th>Open ports</th></tr>
%s
</table>
</body>
</html>
""" % (html.escape(target_label), html.escape(target_label),
       total_scanned, elapsed, n, svg_block, table)


def print_summary(hosts):
    print("HOST             HOSTNAME             OPEN PORTS")
    print("----             --------             ----------")
    for host in hosts:
        ports = ", ".join("%d (%s)" % (p["port"], p["service"])
                          for p in host["open_ports"]) or "-"
        print("%-16s %-20s %s" % (host["ip"], host["hostname"] or "-", ports))


def main():
    parser = argparse.ArgumentParser(
        description="Map a subnet and render a visual network topology diagram.")
    parser.add_argument("target",
                        help="CIDR (192.168.1.0/24), single IP, or range (10.0.0.1-20)")
    parser.add_argument("--ports", default=",".join(str(p) for p in COMMON_PORTS),
                        help="ports to scan on live hosts, e.g. '22,80,443' or '1-1024'")
    parser.add_argument("--discovery-ports", default=",".join(str(p) for p in DISCOVERY_PORTS),
                        help="ports used for host discovery (default: 80,443,22)")
    parser.add_argument("--threads", type=int, default=100,
                        help="concurrent workers (default: 100)")
    parser.add_argument("--timeout", type=float, default=1.0,
                        help="per-connection timeout in seconds (default: 1.0)")
    parser.add_argument("--output", default="map.html",
                        help="HTML map file to write (default: map.html)")
    parser.add_argument("--json", action="store_true",
                        help="print machine-readable JSON instead of the text summary")
    args = parser.parse_args()

    try:
        targets = parse_targets(args.target)
        ports = parse_ports(args.ports)
        discovery_ports = parse_ports(args.discovery_ports)
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        sys.exit(2)

    if not targets:
        print("error: no hosts to scan", file=sys.stderr)
        sys.exit(2)

    start = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as pool:
        alive_flags = list(pool.map(
            lambda ip: host_is_alive(ip, discovery_ports, args.timeout), targets))
    alive = [ip for ip, flag in zip(targets, alive_flags) if flag]

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as pool:
        hosts = list(pool.map(
            lambda ip: map_host(ip, ports, args.timeout), alive))
    hosts.sort(key=lambda h: tuple(int(o) for o in h["ip"].split(".")))

    elapsed = time.time() - start

    html_doc = build_html(args.target, hosts, elapsed, len(targets))
    with open(args.output, "w") as f:
        f.write(html_doc)

    result = {
        "target": args.target,
        "hosts_scanned": len(targets),
        "hosts_alive": len(hosts),
        "elapsed_seconds": round(elapsed, 2),
        "hosts": hosts,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        print("Wrote %s" % args.output, file=sys.stderr)
    else:
        print("Network map: %s" % args.target)
        print("Scanned %d hosts in %.1f seconds, %d alive\n"
              % (len(targets), elapsed, len(hosts)))
        print_summary(hosts)
        print("\nWrote %s" % args.output)


if __name__ == "__main__":
    main()
