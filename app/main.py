import platform
import certifi
from urllib.parse import urlsplit
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, Query, HTTPException
import ipaddress
from fastapi.responses import HTMLResponse
import socket
import subprocess
import re
import requests
import urllib3
from urllib3.util import Timeout
app = FastAPI()
PUBLIC_ACCESS_ENABLED = False
ALLOW_PRIVATE_TARGETS = True
HTTP_PUBLIC_ONLY = True
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
def home():
    return {"message": "NetAssist is running"}

def validate_target(target: str):
    target = target.strip()

    if not target:
        raise HTTPException(
            status_code=400,
            detail="Target cannot be empty"
        )

    if len(target) > 253:
        raise HTTPException(
            status_code=400,
            detail="Target is too long"
        )

    try:
        ipaddress.ip_address(target)
        return target
    except ValueError:
        pass

    labels = target.split(".")

    if not all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in labels
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid domain or IP address"
        )

    return target

def validate_http_url(url: str):
    url = url.strip()

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid HTTP URL"
        )

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail="Only HTTP and HTTPS URLs are allowed"
        )

    if not hostname or parsed.username or parsed.password:
        raise HTTPException(
            status_code=400,
            detail="Invalid HTTP URL"
        )

    validate_target(hostname)

    if hostname.lower() == "localhost":
        raise HTTPException(
            status_code=400,
            detail="Localhost is not allowed"
        )

    try:
        ip = ipaddress.ip_address(hostname)

        if ip.is_loopback:
            raise HTTPException(
                status_code=400,
                detail="Loopback IP addresses are not allowed"
            )

        if not ip.is_global:
            raise HTTPException(
                status_code=400,
                detail="Non-public IP addresses are not allowed"
            )

    except ValueError:
        pass

    try:
        addresses = socket.getaddrinfo(
            hostname,
            None,
            type=socket.SOCK_STREAM
        )
    except socket.gaierror:
        raise HTTPException(
            status_code=400,
            detail="Unable to resolve hostname"
        )

    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="DNS resolution timed out"
        )

    for address in addresses:
        resolved_ip = ipaddress.ip_address(address[4][0])

        if not resolved_ip.is_global:
            raise HTTPException(
                status_code=400,
                detail="Hostname resolves to a non-public IP address"
            )

    return url


def safe_http_request(url: str):
    parsed = urlsplit(url)
    hostname = parsed.hostname

    if parsed.scheme == "https":
        port = parsed.port or 443
    else:
        port = parsed.port or 80

    addresses = socket.getaddrinfo(
        hostname,
        port,
        type=socket.SOCK_STREAM
    )

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])

        if not ip.is_global:
            raise HTTPException(
                status_code=400,
                detail="Non-public destination IP is not allowed"
            )

    return addresses


def connect_to_validated_ip(url: str):
    parsed = urlsplit(url)
    hostname = parsed.hostname

    addresses = safe_http_request(url)

    ipv4_addresses = [
        address[4][0]
        for address in addresses
        if address[0] == socket.AF_INET
    ]

    if not ipv4_addresses:
        raise HTTPException(
            status_code=400,
            detail="No validated IPv4 address available"
        )

    validated_ip = ipv4_addresses[0]

    return {
        "hostname": hostname,
        "validated_ip": validated_ip
    }


def request_validated_https(url: str):
    url = validate_http_url(url)

    parsed = urlsplit(url)

    connection_info = connect_to_validated_ip(url)

    hostname = connection_info["hostname"]
    validated_ip = connection_info["validated_ip"]
    port = parsed.port or 443

    pool = urllib3.HTTPSConnectionPool(
        host=validated_ip,
        port=port,
        server_hostname=hostname,
        assert_hostname=hostname,
        cert_reqs="CERT_REQUIRED",
        ca_certs=certifi.where(),
        timeout=Timeout(connect=3, read=5),
        retries=False
    )

    try:
        path = parsed.path or "/"

        if parsed.query:
            path += "?" + parsed.query

        response = pool.request(
            "GET",
            path,
            headers={"Host": parsed.netloc},
            redirect=False,
            preload_content=False
        )

        try:
            return {
                "url": url,
                "status_code": response.status,
                "validated_ip": validated_ip
            }
        finally:
            response.close()

    finally:
        pool.close()

def request_validated_http(url: str):
    url = validate_http_url(url)
    parsed = urlsplit(url)

    if parsed.scheme != "http":
        raise HTTPException(
            status_code=400,
            detail="This test supports HTTP only"
        )

    connection_info = connect_to_validated_ip(url)

    validated_ip = connection_info["validated_ip"]
    port = parsed.port or 80

    pool = urllib3.HTTPConnectionPool(
        host=validated_ip,
        port=port,
        timeout=Timeout(connect=3, read=5),
        retries=False
    )

    try:
        path = parsed.path or "/"

        if parsed.query:
            path += "?" + parsed.query

        response = pool.request(
            "GET",
            path,
            headers={"Host": parsed.netloc},
            redirect=False,
            preload_content=False
        )

        try:
            return {
                "url": url,
                "status_code": response.status,
                "validated_ip": validated_ip
            }
        finally:
            response.close()

    finally:
        pool.close()

@app.get("/http")
def http_check(url: str):
    url = validate_http_url(url)

    def classify_status(code):
        if 200 <= code < 300:
            return "success"
        elif 300 <= code < 400:
            return "redirect"
        elif 400 <= code < 500:
            return "client_error"
        elif 500 <= code < 600:
            return "server_error"
        return "other"

    if urlsplit(url).scheme == "https":
        try:
            result = request_validated_https(url)
            status_code = result["status_code"]

            return {
                "url": url,
                "status": classify_status(status_code),
                "status_code": status_code,
                "final_url": url
            }

        except urllib3.exceptions.HTTPError as e:
            print(
                f"HTTPS DEBUG ERROR: {type(e).__name__}: {e}",
                flush=True
            )
            return {
                "url": url,
                "status": "failed",
                "message": "HTTPS connection failed"
            }

        except socket.gaierror:
            return {
                "url": url,
                "status": "failed",
                "message": "DNS resolution failed"
            }

    try:
        result = request_validated_http(url)
        status_code = result["status_code"]

        return {
            "url": url,
            "status": classify_status(status_code),
            "status_code": status_code,
            "final_url": url
        }

    except urllib3.exceptions.HTTPError as e:
        print(
            f"HTTP DEBUG ERROR: {type(e).__name__}: {e}",
            flush=True
        )

        return {
            "url": url,
            "status": "failed",
            "message": "HTTP connection failed"
        }

    except socket.gaierror:
        return {
            "url": url,
            "status": "failed",
            "message": "DNS resolution failed"
        }

    except requests.exceptions.RequestException as e:
        print(
            f"HTTP DEBUG ERROR: {type(e).__name__}: {e}",
            flush=True
        )
        return {
            "url": url,
            "status": "failed",
            "message": "HTTP request failed"
        }



@app.get("/dns")
def dns_lookup(target: str):

    target = validate_target(target)

    try:
        ip_address = socket.gethostbyname(target)

        return {"target": target,"status": "success","ip_address": ip_address}

    except socket.gaierror:
        return {"target": target,"status": "failed","message": "DNS resolution failed"}

@app.get("/ping")
def ping_target(target: str):
    target = validate_target(target)

    if platform.system() == "Windows":
        command = ["ping", "-n", "1", "-w", "3000", target]
    else:
        command = ["ping", "-c", "1", "-W", "3", target]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=8
        )

        match = re.search(
            r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms",
            result.stdout,
            re.IGNORECASE
        )

        latency = float(match.group(1)) if match else None

        return {
            "target": target,
            "status": "success" if result.returncode == 0 else "failed",
            "reachable": result.returncode == 0,
            "latency_ms": latency
        }

    except (subprocess.TimeoutExpired, OSError):
        return {
            "target": target,
            "status": "failed",
            "reachable": False,
            "latency_ms": None
        }

@app.get("/traceroute")
def traceroute_target(target: str):

    target = validate_target(target)

    try:
        result = subprocess.run(
                ["tracert", "-d", "-h", "10", "-w", "1000", target]
    if platform.system() == "Windows"
    else ["traceroute", "-n", "-m", "10", "-w", "1", "-q", "1", target],
            capture_output=True,
            text=True,
            timeout=30
        )

        return {
            "target": target,
            "status": "success" if result.returncode == 0 else "failed",
            "output": result.stdout
        }
    except subprocess.TimeoutExpired:
        return {
            "target": target,
            "status": "failed",
            "message": "Traceroute timed out"
        }

    except OSError:
        return {
            "target": target,
            "status": "failed",
            "message": "Unable to execute traceroute"
        }

@app.get("/port")
def port_check(
    target: str,
    port: int = Query(..., ge=1, le=65535)
):

    target = validate_target(target)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex((target, port))
        sock.close()

        return {"target": target, "port": port,"status": "success", "open": result == 0}

    except socket.gaierror:
        return { "target": target,"port": port, "open": False, "message": "Target could not be resolved" }





@app.get("/diagnose")
def diagnose(target: str):

    target = validate_target(target)

    dns_result = dns_lookup(target)
    ping_result = ping_target(target)
    port_result = port_check(target, 443)


    if target.startswith("http://") or target.startswith("https://"):
        url = target
    else:
        url = "https://" + target

    http_result = http_check(url)

    if dns_result["status"] == "failed":
        summary = "DNS resolution failed"

    elif http_result.get("status_code") is not None:
        status_code = http_result["status_code"]

        if 200 <= status_code < 300:
                port_status = "Open" if port_result["open"] else "Closed or unreachable"

                latency = ping_result.get("latency_ms")
                ping_status = f"{latency} ms" if latency is not None else "Unavailable"

                summary = f"HTTPS reachable | HTTP {status_code} Success | TCP 443 {port_status} | Ping {ping_status}"

        elif 300 <= status_code < 400:
            port_status = "Open" if port_result["open"] else "Closed or unreachable"

            latency = ping_result.get("latency_ms")
            ping_status = f"{latency} ms" if latency is not None else "Unavailable"

            summary = f"HTTPS reachable | HTTP {status_code} Redirect | TCP 443 {port_status} | Ping {ping_status}"

        elif 400 <= status_code < 500:
            summary = "Web server is reachable - Client error"

        elif 500 <= status_code < 600:
            summary = "Web server is reachable - Server error"

        else:
            summary = "Web server is reachable"

    elif not port_result["open"]:
        summary = "Port 443 is not reachable"

    elif http_result.get("status") == "failed":
        summary = "HTTP request failed"

    elif ping_result["reachable"] == False:
        summary = "Ping failed, but further checks may still be available"

    else:
        summary = "Diagnostic completed"

    return {
        "target": target,
        "dns": dns_result,
        "ping": ping_result,
        "port_443": port_result,
        "http": http_result,
        "summary": summary
     }

@app.get("/ui", response_class=HTMLResponse)
def show_ui():
    return """
    <html>
        <head>
        <title>NetAssist</title>
        <link rel="stylesheet" href="/static/style.css?v=3">

    </head>

        <body>
         <div class="container">
            <h1>NetAssist</h1>
            <p>Network Diagnostic Tool</p>

            <label>Target:</label>
            <input type="text" id="target" placeholder="Enter IP or domain">

            <button type="button" onclick="runDiagnostic()">Run Diagnostic</button>

            <div class="diagnostic-card port-check-section">
    <h3>Custom TCP Port Check</h3>

    <input
        type="number"
        id="custom-port"
        placeholder="Enter port (e.g. 3389)"
        min="1"
        max="65535"
    >

    <button type="button" onclick="runCustomPortCheck()">
        Check Port
    </button>

    <p id="custom-port-result">No port checked yet.</p>
</div>

            <details class="json-details">
            <summary>View Raw JSON Results</summary>
            <pre id="result">No diagnostic results yet.</pre>
            </details>

            <div class="diagnostic-grid">

            <div class="diagnostic-card">
            <h3>DNS Lookup</h3>
            <div id="dns-result">Waiting for diagnostic...</div>
            </div>

            <div class="diagnostic-card">
            <h3>Ping Latency</h3>
             <div id="ping-result">Waiting for diagnostic...</div>
            </div>

            <div class="diagnostic-card">
            <h3>TCP Port Check</h3>
            <div id="port-result">Waiting for diagnostic...</div>
            </div>

            <div class="diagnostic-card">
            <h3>HTTP Status</h3>
            <div id="http-result">Waiting for diagnostic...</div>
            </div>

            <div class="diagnostic-card" id="summary-card">
            <h3>Diagnostic Summary</h3>
            <div id="summary-result">Waiting for diagnostic...</div>
            </div>

            <div class="diagnostic-card" id="traceroute-card">
            <h3>Traceroute</h3>

            <button type="button" onclick="runTraceroute()">
            Run Traceroute
            </button>

            <pre id="traceroute-result">Click Run Traceroute to start.</pre>
            </div>

            </div>



             <script>

                function validateTarget(target) {
    if (!target) {
        return "Please enter a target.";
    }

    if (target.includes(" ")) {
        return "Target must not contain spaces.";
    }

    if (target.includes("/") || target.includes(":")) {
        return "Please enter a domain or IP address, not a URL.";
    }

    if (!/^[a-zA-Z0-9.-]+$/.test(target)) {
        return "Target contains invalid characters.";
    }

    return null;
}


                async function runDiagnostic() {
                    const target = document.getElementById("target").value;

                    const validationError = validateTarget(target.trim());

if (validationError) {
    alert(validationError);
    return;
}


                    const response = await fetch(
                        "/diagnose?target=" + encodeURIComponent(target)
            );

                    const data = await response.json();

                    document.getElementById("result").textContent =
                    JSON.stringify(data, null, 2);

                    document.getElementById("dns-result").textContent =
                    "IP Address: " + (data.dns.ip_address || "Not resolved");

                    document.getElementById("ping-result").textContent =
                    data.ping.reachable
                    ? "Reachable - " + data.ping.latency_ms + " ms"
                    : "Ping failed";

                    document.getElementById("port-result").textContent =
                    data.port_443.open
                    ? "Port 443 - Open"
                    : "Port 443 - Closed or unreachable";

                    document.getElementById("http-result").textContent =
                    data.http.status === "redirect"
                    ? "HTTP " + data.http.status_code + " - Redirect"
                    : "HTTP " + data.http.status_code;

                    document.getElementById("summary-result").textContent =
                    data.summary;

                    document.getElementById("traceroute-result").textContent =
                    data.traceroute.output || data.traceroute.message || "No traceroute result";
               }

                async function runTraceroute() {
                    const target = document.getElementById("target").value.trim();

                    const validationError = validateTarget(target);

if (validationError) {
    alert(validationError);
    return;
}

                    const resultElement = document.getElementById("traceroute-result");

                    resultElement.textContent = "Running traceroute... Please wait.";


                    try {
                    const response = await fetch(
                    "/traceroute?target=" + encodeURIComponent(target)
                    );

                    if (!response.ok) {
                    throw new Error("Traceroute request failed");
                    }

                    const data = await response.json();

                    resultElement.textContent =
                    data.output || data.message || "No traceroute result";

                    } catch (error) {
                    console.error("Traceroute error:", error);
                    resultElement.textContent = "Unable to run traceroute.";
                    }
                }
                async function runCustomPortCheck() {
                    const target = document.getElementById("target").value.trim();
                     const port = Number(document.getElementById("custom-port").value);
                    const resultElement = document.getElementById("custom-port-result");

                    const validationError = validateTarget(target);

if (validationError) {
    resultElement.textContent = validationError;
    return;
}

                    if (!Number.isInteger(port) || port < 1 || port > 65535) {
                    resultElement.textContent = "Please enter a valid port (1-65535).";
                    return;
                }

                    resultElement.textContent = "Checking port...";

                    try {
                        const response = await fetch(
                    "/port?target=" + encodeURIComponent(target) + "&port=" + port
                    );

                    if (!response.ok) {
                    throw new Error("Port check request failed");
                    }

                    const data = await response.json();

                    resultElement.textContent = data.open
                    ? "Port " + port + " - Open"
                    : "Port " + port + " - Closed or unreachable";

                    } catch (error) {
                    console.error("Port check error:", error);
                    resultElement.textContent = "Unable to check port.";
                    }
                    }

            </script>
        </div>
    </body>
</html>
   """