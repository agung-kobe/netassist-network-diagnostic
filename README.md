# NetAssist — Network Diagnostic Web Application

NetAssist is a self-hosted network diagnostic web application built with Python and FastAPI. It provides a simple web dashboard for performing common network troubleshooting tasks.

## Features

- DNS Lookup — Resolve domain names to IPv4 addresses
- ICMP Ping — Check host reachability and latency
- TCP Port Check — Test connectivity to a specified TCP port
- HTTP/HTTPS Check — Inspect HTTP response status codes
- Traceroute — Display network hops to a destination
- Network Diagnosis — Run multiple diagnostic checks and display a summary
- Web Dashboard — Interactive interface with diagnostic results

## Technology Stack

- Python
- FastAPI
- HTML, CSS, JavaScript
- Docker
- Ubuntu Server
- Proxmox VE
- Tailscale VPN

## Architecture

Windows 11 (VS Code Development)
        |
        v
Ubuntu Server VM (Proxmox VE)
        |
        v
Docker Container (FastAPI)
        |
        v
NetAssist Web Dashboard

Private remote access is provided through Tailscale VPN and Tailscale Serve.

## Local Development

Install dependencies:

    pip install -r requirements.txt

Start the application:

    uvicorn app.main:app --reload

Open:

    http://127.0.0.1:8000/ui

## Docker Deployment

Build the Docker image:

    docker build -t netassist:1.0 .

Run locally:

    docker run -d --name netassist -p 127.0.0.1:8000:8000 netassist:1.0

## Security Notes

NetAssist is intended for controlled environments and trusted networks.

The diagnostic endpoints can initiate network connections to user-supplied targets. Do not expose this version directly to the public internet without authentication, rate limiting, target restrictions, and additional security review.

## Project Status

Version 1.0 — Functional prototype deployed successfully on Ubuntu Server using Docker.

## Planned Improvements

- Diagnostic history
- Latency monitoring and charts
- Export results
- Authentication and rate limiting
- Public portfolio demo with restricted diagnostic targets
