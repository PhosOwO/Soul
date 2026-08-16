#!/usr/bin/env node
const { runPythonModule } = require("./_python_launcher");

runPythonModule("soul.cli", process.argv.slice(2));
