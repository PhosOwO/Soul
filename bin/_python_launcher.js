const { spawnSync } = require("node:child_process");
const path = require("node:path");

function packageRoot() {
  return path.resolve(__dirname, "..");
}

function pythonCandidates() {
  const configured = process.env.SOUL_PYTHON || process.env.PYTHON;
  const candidates = [];
  if (configured) {
    candidates.push(configured);
  }
  if (process.platform === "win32") {
    candidates.push("py", "python", "python3");
  } else {
    candidates.push("python3", "python");
  }
  return candidates;
}

function runPythonModule(moduleName, argv) {
  const root = packageRoot();
  const env = {
    ...process.env,
    PYTHONPATH: [root, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    PYTHONDONTWRITEBYTECODE: process.env.PYTHONDONTWRITEBYTECODE || "1",
  };

  const attempts = [];
  for (const python of pythonCandidates()) {
    const args = python === "py" ? ["-3", "-m", moduleName, ...argv] : ["-m", moduleName, ...argv];
    const result = spawnSync(python, args, {
      cwd: process.cwd(),
      env,
      stdio: "inherit",
      windowsHide: true,
    });

    if (result.error && result.error.code === "ENOENT") {
      attempts.push(python);
      continue;
    }
    if (result.error) {
      console.error(`soul: failed to launch ${python}: ${result.error.message}`);
      process.exit(1);
    }
    process.exit(result.status === null ? 1 : result.status);
  }

  console.error(
    "soul: Python 3.11+ was not found. Set SOUL_PYTHON to a Python executable. Tried: " +
      attempts.join(", ")
  );
  process.exit(1);
}

module.exports = { runPythonModule };
