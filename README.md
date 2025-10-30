# Captive Portal (DHCP Option 114 + RFC 8910 & 8908)

A minimal captive portal compatible with DHCP Option 114 (RFC 8910) and the Captive Portal API (RFC 8908). It serves a terms-and-conditions page and tracks acceptance so clients can be marked as not captive.

## Files
- `index.html` — Portal UI with checkbox and Accept button (redirects to DuckDuckGo).
- `index.js` — Node.js/Express HTTP server:
  - Serves Captive Portal API at `/.well-known/captive-portal`.
  - Serves UI at `/portal`.
  - Accept endpoint at `/accept` (marks client as accepted).

## Quick Start
1. Requirements: Node.js 16+
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the server:
   ```bash
   npm start
   ```
4. Portal UI:
   ```
   http://<server>:8000/portal
   ```
5. Captive Portal API URI (DHCP Option 114 value):
   ```
   http://<server>:8000/.well-known/captive-portal
   ```

Replace `<server>` with the IP/hostname reachable by clients (avoid 127.0.0.1 for remote devices).

## How It Works
- API returns JSON like:
  ```json
  {
    "captive": true,
    "user-portal-url": "http://<server>:8000/portal"
  }
  ```
- When the user accepts in `index.html`, the page POSTs to `/accept` and then redirects to DuckDuckGo.
- Acceptance is tracked in-memory by client IP (simple demo behavior).

### Request Logging (Node/Express server)
- The Node server logs every request as a JSON line to stdout, and optionally to a file.
- It will attempt to enrich logs with DHCP lease data (MAC, hostname, expiry) if a dnsmasq leases file is available.

Environment variables:
- `LOG_PATH` — path to append JSONL logs (optional). Example: `/var/log/captive-portal.log`.
- `DHCP_LEASES_PATH` — path to dnsmasq leases file (optional). Default: `/var/lib/misc/dnsmasq.leases`.

Example log line:
```json
{
  "ts": "2025-10-13T20:10:00.123Z",
  "method": "GET",
  "path": "/.well-known/captive-portal",
  "clientIP": "192.0.2.45",
  "host": "portal.example.org:8000",
  "ua": "curl/8.0.1",
  "referer": "",
  "status": 200,
  "ms": 2,
  "dhcp": { "source": "dnsmasq", "mac": "00:11:22:33:44:55", "hostname": "client-host", "expiryEpoch": 1760000000 }
}
```

Notes:
- dnsmasq lease format assumed: `<expiry> <mac> <ip> <hostname> <client-id>`.
- If running behind a reverse proxy, `server-node.js` sets `app.set('trust proxy', true)` to log the correct `req.ip`.

### MongoDB Logging (optional)
If `MONGO_URI` is set, the Node server will also write request logs to MongoDB using Mongoose.

Environment variables:
- `MONGO_URI` — Mongo connection string, e.g. `mongodb://localhost:27017/captive` or Atlas URI.
- `MONGO_DB` — Optional. Overrides the database name in the connection (if not included in URI).
- `MONGO_COLLECTION` — Optional. Defaults to `portal_requests`.

Schema fields (collection: `portal_requests` by default):
- `ts` (Date, indexed), `method`, `path`, `clientIP` (indexed), `xff`, `xffClientIP`, `host`, `ua`, `referer`, `status`, `ms`, `dhcp: { source, mac, hostname, expiryEpoch }`.

Example run:
```bash
export MONGO_URI="mongodb://localhost:27017/captive"
npm run start:node
```

Example query in Mongo shell:
```js
db.portal_requests.find({ clientIP: "192.0.2.45" }).sort({ ts: -1 }).limit(5)
```

## Configure DHCP Option 114
Set the option value to the Captive Portal API URI (NOT the landing page):
```
http://<server>:8000/.well-known/captive-portal
```
Examples:

- dnsmasq (`dnsmasq.conf`):
  ```
  dhcp-option=option:captive-portal,http://portal.example.org:8000/.well-known/captive-portal
  ```

- ISC dhcpd (`dhcpd.conf`):
  ```
  option captive-portal code 114 = text;
  option captive-portal "http://portal.example.org:8000/.well-known/captive-portal";
  ```

- Kea DHCP (JSON):
  ```json
  {
    "Dhcp4": {
      "option-data": [
        {
          "name": "captive-portal",
          "code": 114,
          "space": "dhcp4",
          "data": "http://portal.example.org:8000/.well-known/captive-portal"
        }
      ]
    }
  }
  ```

- Windows Server DHCP (PowerShell):
  ```powershell
  Add-DhcpServerv4OptionDefinition -Name "Captive Portal" -OptionId 114 -Type String -Description "RFC8910 Captive Portal API URI"
  Set-DhcpServerv4OptionValue -OptionId 114 -Value "http://portal.example.org:8000/.well-known/captive-portal"
  ```

## Testing
- Check API before acceptance:
  ```bash
  curl -H "Host: <server>:8000" http://<server>:8000/.well-known/captive-portal
  ```
  Should show `"captive": true`.

- Open the portal UI, check the box, click Accept:
  ```
  http://<server>:8000/portal
  ```

- Re-check API after acceptance:
  ```bash
  curl -H "Host: <server>:8000" http://<server>:8000/.well-known/captive-portal
  ```
  Should show `"captive": false` for your client IP.

## Production Considerations
- HTTPS/TLS for API and portal.
- Persist acceptance (database or cache) and add expiry.
- Identify clients more robustly (MAC address via integration, or session/token via cookies).
- Customize terms, branding, and post-accept redirect.

## Troubleshooting
- If clients still appear captive:
  - Verify DHCP Option 114 value is the API URI.
  - Confirm client can reach `/.well-known/captive-portal`.
  - Ensure clients and server see distinct client IPs (NAT may cause sharing).
- If the server restarts, acceptance state is lost (in-memory). Consider persistence.

### Apple (iOS/macOS) Detection Issues
Apple devices require **specific responses** to properly detect captive portals:

**Apple's Captive Portal Detection:**
1. **Domain**: Apple devices probe `captive.apple.com` and other Apple domains
2. **Endpoint**: `/hotspot-detect.html`
3. **Expected Response**:
   - **Not Captive** (after acceptance): HTTP 200 with exact HTML: `<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>`
   - **Captive** (before acceptance): HTTP 302 redirect to portal

**This server now correctly handles Apple detection** with a dedicated `/hotspot-detect.html` endpoint.

**Testing Apple Detection:**
```bash
# Test Apple captive portal endpoint (before acceptance)
curl -v http://your-portal-ip:8000/hotspot-detect.html
# Should return: 302 redirect to /portal

# After acceptance, should return:
# HTTP 200 with: <HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>
```

**Critical Requirements:**
- **DNS Interception**: Must redirect `captive.apple.com` to your portal server
- **HTTP Port 80**: Apple probes on port 80, so either run on port 80 or use port forwarding/NAT
- **No HTTPS Required**: Apple uses HTTP (not HTTPS) for initial detection

### Linux-Specific Issues
Linux systems (NetworkManager, systemd-networkd) may not detect your captive portal without proper network configuration:

**Requirements for Linux Detection:**
1. **DNS Interception**: Your network must intercept DNS queries and redirect them to your captive portal server. Without this, Linux will successfully resolve external domains (e.g., `connectivity-check.ubuntu.com`) and never hit your portal.

2. **HTTP Interception**: Your network firewall/router must intercept all HTTP traffic on port 80 and redirect it to your captive portal server.

3. **Connectivity Check Endpoints**: This server now includes handlers for Linux connectivity check paths:
   - `/connectivity-check`
   - `/connectivity-check.html`
   - `/check_network_status.txt`
   - `/static/hotspot.txt`

**Testing Linux Detection:**
```bash
# Test connectivity check endpoint
curl -v http://your-portal-ip:8000/connectivity-check

# Should return 302 redirect to portal (before acceptance)
# Should return 204 No Content (after acceptance)
```

**Common Linux NetworkManager URLs:**
- Ubuntu/Debian: `http://connectivity-check.ubuntu.com`
- Fedora/RHEL: `http://fedoraproject.org/static/hotspot.txt`
- Arch Linux: `http://www.archlinux.org/check_network_status.txt`

**Network Configuration Required:**
Without DNS/HTTP interception at the network level, Linux and Apple clients will bypass your captive portal entirely. This requires configuration on your DHCP server, router, or firewall (e.g., dnsmasq, iptables, etc.).
