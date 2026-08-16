#!/usr/bin/env node
const { runPythonModule } = require("./_python_launcher");

runPythonModule("soul.mcp", process.argv.slice(2));
