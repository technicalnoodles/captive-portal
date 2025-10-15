# Captive Portal (DHCP Option 114 + RFC 8908)

A minimal captive portal compatible with DHCP Option 114 (RFC 8910) and the Captive Portal API (RFC 8908). It serves a terms-and-conditions page and tracks acceptance so clients can be marked as not captive.

## Files
- `index.html` — Portal UI with checkbox and Accept button (redirects to DuckDuckGo).
- `server.py` — Python HTTP server:
  - Serves Captive Portal API at `/.well-known/captive-portal`.
  - Serves UI at `/portal`.
  - Accept endpoint at `/accept` (marks client as accepted).

## Quick Start

### Run with Python
1. Requirements: Python 3.8+
2. Start the server:
   ```bash
   python3 server.py
   ```
3. Portal UI:
   ```
   http://<server>:8000/portal
   ```
4. Captive Portal API URI (DHCP Option 114 value):
   ```
   http://<server>:8000/.well-known/captive-portal
   ```

### Run with Node.js
1. Requirements: Node.js 16+
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the server:
   ```bash
   npm run start:node
   # or
   node server-node.js
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
