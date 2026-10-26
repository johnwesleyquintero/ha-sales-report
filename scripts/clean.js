const fs = require('fs');
const path = require('path');

const projectRoot = path.join(__dirname, '..');

// Generated outputs only: raw exports under data/raw are never touched.
const generatedDirs = [
  path.join(projectRoot, 'data', 'processed'),
  path.join(projectRoot, 'reports'),
];

for (const dir of generatedDirs) {
  const label = path.relative(projectRoot, dir);
  if (fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true, force: true });
    console.log(`🧹 Cleaned ${label} folder.`);
  } else {
    console.log(`ℹ️ ${label} folder is already clean.`);
  }
}
