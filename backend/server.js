const http = require("http");
const fs = require("fs");
const path = require("path");

const port = Number(process.env.PORT || 8080);
const logDir = process.env.LOG_DIR || "/tmp";
const logFile = path.join(logDir, "access.log");

function writeLog(entry) {
  try {
    fs.mkdirSync(logDir, { recursive: true });
    fs.appendFileSync(logFile, `${entry}\n`);
  } catch (error) {
    console.error("log write failed", error.message);
  }
}

const server = http.createServer((req, res) => {
  const now = new Date().toISOString();
  const remote = req.socket.remoteAddress || "unknown";
  writeLog(`${now} ${remote} ${req.method} ${req.url}`);

  if (req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok", service: "backend-api" }));
    return;
  }

  if (req.url === "/api/info") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        name: "security-ec-base",
        message: "backend reachable only through the reverse proxy",
        networks: ["app_net", "db_net", "monitor_net"],
      }),
    );
    return;
  }

  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "not_found" }));
});

server.listen(port, "0.0.0.0", () => {
  console.log(`backend-api listening on ${port}`);
});
