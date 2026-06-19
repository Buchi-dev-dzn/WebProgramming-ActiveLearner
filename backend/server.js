const http = require("http");
const fs = require("fs");
const path = require("path");
const { Pool } = require("pg");
const { createClient } = require("redis");

const port = Number(process.env.PORT || 8080);
const logDir = process.env.LOG_DIR || "/tmp";
const logFile = path.join(logDir, "access.log");
const databaseUrl = process.env.DATABASE_URL;
const redisUrl = process.env.REDIS_URL;

const postgresPool = databaseUrl
  ? new Pool({ connectionString: databaseUrl })
  : null;
const redisClient = redisUrl ? createClient({ url: redisUrl }) : null;
let redisConnectPromise = null;

if (redisClient) {
  redisClient.on("error", (error) => {
    console.error("redis client error", error.message);
  });
}

function writeLog(entry) {
  try {
    fs.mkdirSync(logDir, { recursive: true });
    fs.appendFileSync(logFile, `${entry}\n`);
  } catch (error) {
    console.error("log write failed", error.message);
  }
}

async function ensureRedisConnected() {
  if (!redisClient) {
    return;
  }

  if (redisClient.isOpen) {
    return;
  }

  if (!redisConnectPromise) {
    redisConnectPromise = redisClient.connect().catch((error) => {
      redisConnectPromise = null;
      throw error;
    });
  }

  await redisConnectPromise;
}

async function runHealthChecks() {
  const checks = {
    postgres: { status: "not_configured" },
    redis: { status: "not_configured" },
  };

  let overallStatus = "ok";

  if (postgresPool) {
    try {
      await postgresPool.query("SELECT 1");
      checks.postgres = { status: "ok" };
    } catch (error) {
      overallStatus = "degraded";
      checks.postgres = { status: "error", detail: error.message };
    }
  }

  if (redisClient) {
    try {
      await ensureRedisConnected();
      await redisClient.ping();
      checks.redis = { status: "ok" };
    } catch (error) {
      overallStatus = "degraded";
      checks.redis = { status: "error", detail: error.message };
    }
  }

  return {
    statusCode: overallStatus === "ok" ? 200 : 503,
    payload: {
      service: "backend-api",
      status: overallStatus,
      checks,
    },
  };
}

function writeJson(res, statusCode, payload) {
  res.writeHead(statusCode, { "Content-Type": "application/json" });
  res.end(JSON.stringify(payload));
}

async function handleRequest(req, res) {
  const now = new Date().toISOString();
  const remote = req.socket.remoteAddress || "unknown";
  writeLog(`${now} ${remote} ${req.method} ${req.url}`);

  if (req.url === "/health") {
    const { statusCode, payload } = await runHealthChecks();
    writeJson(res, statusCode, payload);
    return;
  }

  if (req.url === "/api/health") {
    const { statusCode, payload } = await runHealthChecks();
    writeJson(res, statusCode, payload);
    return;
  }

  if (req.url === "/api/info") {
    writeJson(res, 200, {
      name: "security-ec-base",
      message: "backend reachable only through the reverse proxy",
      via: ["waf", "reverse-proxy", "backend-api"],
      dependencies: ["postgres", "redis"],
      networks: ["app_net", "db_net", "monitor_net"],
    });
    return;
  }

  writeJson(res, 404, { error: "not_found" });
}

const server = http.createServer((req, res) => {
  handleRequest(req, res).catch((error) => {
    console.error("request failed", error.message);
    writeJson(res, 500, {
      error: "internal_error",
      detail: error.message,
    });
  });
});

server.listen(port, "0.0.0.0", () => {
  console.log(`backend-api listening on ${port}`);
});
