#!/usr/bin/env node
const { runPythonModule } = require("./_python_launcher");

const argv = process.argv.slice(2);
const command = argv[0];

if (command === "soul-mcp") {
  runPythonModule("soul.mcp", argv.slice(1));
} else if (command === "soul-api") {
  runPythonModule("soul.api", argv.slice(1));
} else {
  runPythonModule("soul.cli", argv);
}
