#!/usr/bin/env node

const express = require('express');
const path = require('path');
const os = require('os');
const mongoose = require('mongoose');

// In-memory acceptance state with IP and fingerprint
const ACCEPTED = new Map(); // Map<clientIP, { fingerprint, timestamp }>

const ROOT_DIR = __dirname;
const PORTAL_PATH = '/portal'; // human-facing portal UI path
const API_PATH = '/.well-known/captive-portal'; // RFC 8908 recommended well-known path
const ACCEPT_PATH = '/accept';
const MONGO_URI = process.env.MONGO_URI || '';
const MONGO_DB = process.env.MONGO_DB;
const MONGO_COLLECTION = process.env.MONGO_COLLECTION || 'portal_requests';

/**
 * Generate portal URL from host header
 */
function portalUrlFromHost(hostHeader) {
  const scheme = 'http';
  const host = hostHeader || `${getLocalIP()}:8000`;
  return `${scheme}://${host}${PORTAL_PATH}`;
}

/**
 * Get local IP address
 */
function getLocalIP() {
  const interfaces = os.networkInterfaces();
  for (const name of Object.keys(interfaces)) {
    for (const iface of interfaces[name]) {
      if (iface.family === 'IPv4' && !iface.internal) {
        return iface.address;
      }
    }
  }
  return '127.0.0.1';
}

/**
 * Add no-store cache header
 */
function noStore(res) {
  res.set('Cache-Control', 'no-store');
  return res;
}

/**
 * Get client IP from request (supports X-Forwarded-For)
 */
function getClientIP(req) {
  // Check X-Forwarded-For header first (for proxy support)
  const xff = req.headers['x-forwarded-for'];
  if (xff) {
    return xff.split(',')[0].trim();
  }
  return req.ip || req.connection.remoteAddress || '';
}

/**
 * Get XFF client IP
 */
function getXFFClientIP(req) {
  const xff = req.headers['x-forwarded-for'];
  return xff ? xff.split(',')[0].trim() : '';
}

const app = express();

// Trust first proxy (equivalent to ProxyFix x_for=1)
app.set('trust proxy', 1);

// JSON body parser for POST requests
app.use(express.json());

// Optional: MongoDB setup
let mongoCollection = null;

if (MONGO_URI) {
  mongoose.connect(MONGO_URI, {
    serverSelectionTimeoutMS: 3000,
    dbName: MONGO_DB || 'captive'
  })
    .then(() => {
      console.log('[mongo] connected');
      
      // Define schema for logging
      const portalRequestSchema = new mongoose.Schema({
        ts: { type: Date, default: Date.now },
        event: String,
        ip: String,
        xff_ip: String,
        ua: String,
        path: String,
        fingerprint: String,
        fingerprintData: Object
      }, { 
        collection: MONGO_COLLECTION,
        strict: false 
      });
      
      mongoCollection = mongoose.model('PortalRequest', portalRequestSchema);
    })
    .catch((err) => {
      console.error(`[mongo] connection error: ${err}`);
    });
}

/**
 * Log event to MongoDB (async, non-blocking)
 */
function logToMongo(req, event, additionalData = {}) {
  if (!mongoCollection) return;
  
  const doc = {
    ts: new Date(),
    event: event,
    ip: getClientIP(req),
    xff_ip: getXFFClientIP(req),
    ua: req.headers['user-agent'] || '',
    path: req.originalUrl,
    ...additionalData
  };
  
  // Async insert without blocking
  mongoCollection.create(doc).catch(() => {});
}

// Request timing middleware
app.use((req, res, next) => {
  req._startTime = Date.now();
  next();
});

// Request logging middleware
app.use((req, res, next) => {
  const originalSend = res.send;
  
  res.send = function(data) {
    const ms = Date.now() - (req._startTime || Date.now());
    const entry = {
      method: req.method,
      path: req.originalUrl,
      status: res.statusCode,
      ms: ms,
      clientIP: getClientIP(req),
      ua: req.headers['user-agent'] || ''
    };
    
    const line = JSON.stringify(entry);
    
    if (req.method === 'GET') {
      console.log(line);
      logToMongo(req, 'request');
    }
    
    if (res.statusCode >= 400) {
      console.error(line);
    }
    
    return originalSend.call(this, data);
  };
  
  next();
});

// Serve static files from imgs directory
app.use('/imgs', express.static(path.join(__dirname, 'imgs')));

// Serve FingerprintJS library from node_modules
app.get('/fpjs/v4.min.js', (req, res) => {
  const fpjsPath = path.join(__dirname, 'node_modules/@fingerprintjs/fingerprintjs/dist/fp.min.js');
  res.sendFile(fpjsPath, (err) => {
    if (err) {
      res.status(404).send('FingerprintJS library not found');
    }
  });
});

// API endpoint: /.well-known/captive-portal
app.get(API_PATH, (req, res) => {
  const clientIP = getClientIP(req);
  const captive = !ACCEPTED.has(clientIP);
  
  const payload = {
    captive: captive,
    'user-portal-url': portalUrlFromHost(req.headers.host)
  };
  
  noStore(res)
    .type('application/captive+json')
    .status(200)
    .json(payload);
});

// Portal page: /portal
app.get(PORTAL_PATH, (req, res) => {
  const filePath = path.join(ROOT_DIR, 'index.html');
  
  noStore(res).sendFile(filePath, (err) => {
    if (err) {
      res.status(404).send('index.html not found');
    }
  });
});

// Root redirect
app.get('/', (req, res) => {
  noStore(res).redirect(302, PORTAL_PATH);
});

// Apple-specific captive portal detection
// iOS/macOS expects specific HTML response with "Success"
app.get('/hotspot-detect.html', (req, res) => {
  const clientIP = getClientIP(req);
  
  if (ACCEPTED.has(clientIP)) {
    // Client has accepted - return Apple's expected success page
    const successHTML = '<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>';
    noStore(res).status(200).send(successHTML);
  } else {
    // Client hasn't accepted - redirect to portal
    noStore(res).redirect(302, portalUrlFromHost(req.headers.host));
  }
});

// Android/iOS/Linux connectivity check handlers
// These endpoints are probed by mobile devices and Linux to detect captive portals
const connectivityCheckPaths = [
  // Android
  '/generate_204',
  '/gen_204',
  // Windows
  '/ncsi.txt',
  '/success.txt',
  // Linux NetworkManager (Ubuntu, Debian, Fedora)
  '/connectivity-check',
  '/connectivity-check.html',
  '/check_network_status.txt',
  '/static/hotspot.txt'
];

app.get(connectivityCheckPaths, (req, res) => {
  const clientIP = getClientIP(req);
  
  if (ACCEPTED.has(clientIP)) {
    // Client has accepted - return success response
    noStore(res).status(204).send('');
  } else {
    // Client hasn't accepted - redirect to portal
    // Use 302 redirect for Android compatibility
    noStore(res).redirect(302, portalUrlFromHost(req.headers.host));
  }
});

// Catch-all for any domain's connectivity check
// This handles requests to connectivitycheck.gstatic.com, clients3.google.com, 
// connectivity-check.ubuntu.com, fedoraproject.org, captive.apple.com, etc.
app.use((req, res, next) => {
  const path = req.path;
  const clientIP = getClientIP(req);
  
  // Apple hotspot-detect needs special handling
  if (path === '/hotspot-detect.html' || path.includes('hotspot-detect')) {
    if (ACCEPTED.has(clientIP)) {
      const successHTML = '<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>';
      return noStore(res).status(200).send(successHTML);
    } else {
      return noStore(res).redirect(302, portalUrlFromHost(req.headers.host));
    }
  }
  
  // Other connectivity check probes
  if (path.includes('generate_204') || path.includes('gen_204') || 
      path === '/ncsi.txt' || path === '/success.txt' || 
      path.includes('connectivity-check') ||
      path === '/check_network_status.txt' ||
      path.includes('/static/hotspot')) {
    
    if (ACCEPTED.has(clientIP)) {
      return noStore(res).status(204).send('');
    } else {
      return noStore(res).redirect(302, portalUrlFromHost(req.headers.host));
    }
  }
  
  next();
});

// Accept terms endpoint
app.post(ACCEPT_PATH, (req, res) => {
  const clientIP = getClientIP(req);
  const fingerprint = req.body?.visitorId || null;
  
  // Store acceptance with fingerprint
  ACCEPTED.set(clientIP, {
    fingerprint: fingerprint,
    timestamp: new Date()
  });
  
  // Log to MongoDB with fingerprint (visitorId only, no components)
  logToMongo(req, 'accept', {
    fingerprint: fingerprint
  });
  
  console.log(`[accept] IP: ${clientIP}, Fingerprint: ${fingerprint}`);
  
  noStore(res).status(204).send('');
});

app.get('/aws-health', (req, res) => {
  res.status(200).send('some text');
  }
);

// Start server
if (require.main === module) {
  const port = parseInt(process.env.PORT || '8000', 10);
  
  app.listen(port, '0.0.0.0', () => {
    console.log(`Captive Portal server (Express) running on http://0.0.0.0:${port}`);
    console.log(`API: ${API_PATH}`);
    console.log(`Portal: ${PORTAL_PATH}`);
    console.log(`Accept endpoint: ${ACCEPT_PATH}`);
  });
}

module.exports = app;
