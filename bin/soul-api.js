#!/usr/bin/env node
const { runPythonModule } = require("./_python_launcher");

runPythonModule("soul.api", process.argv.slice(2));
