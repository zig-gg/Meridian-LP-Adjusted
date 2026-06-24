import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { loadPaperPositions, savePaperPositions, getMyPositions, closePosition } from "./tools/dlmm.js";

const dirname = path.dirname(fileURLToPath(import.meta.url));
const activePath = path.join(dirname, "data", "paper_active_positions.json");
const archivePath = path.join(dirname, "data", "paper_archive.json");

async function run() {
  process.env.DRY_RUN = "true";
  process.env.EXECUTION_MODE = "paper";

  // Reset
  if (fs.existsSync(activePath)) fs.unlinkSync(activePath);
  if (fs.existsSync(archivePath)) fs.unlinkSync(archivePath);

  // 1. Create a synthetic paper position
  const syntheticId = `paper_${Date.now()}_JUP2jxv`;
  const newPaperPosition = {
    position: syntheticId,
    pool: "JUP2jxvXaqu7NQY1GmWEBidKvFVMfMZi23NNacRsy7x",
    pair: "JUP-USDC",
    base_mint: "JUPyiwrYPRn4z32RFAwK6XkS1Kj3Z4jXUaD4XF3Wf4e",
    lower_bin: -10,
    upper_bin: 10,
    active_bin: 0,
    in_range: true,
    unclaimed_fees_usd: 0,
    total_value_usd: 150,
    total_value_true_usd: 150,
    collected_fees_usd: 0,
    collected_fees_true_usd: 0,
    pnl_usd: 0,
    pnl_true_usd: 0,
    pnl_pct: 0,
    pnl_pct_derived: 0,
    pnl_pct_diff: 0,
    pnl_pct_suspicious: false,
    unclaimed_fees_true_usd: 0,
    fee_per_tvl_24h: 0,
    age_minutes: 0
  };
  
  await savePaperPositions(activePath, [newPaperPosition]);
  console.log(`[1] Created synthetic position: ${syntheticId}`);
  
  // 2. Confirm file contains it
  const fileContents = fs.readFileSync(activePath, "utf8");
  console.log(`[2] File contains it: ${fileContents.includes(syntheticId)}`);
  
  // 3. Call getMyPositions()
  const positionsResult = await getMyPositions({ force: true, silent: true });
  console.log(`[3] getMyPositions returned object:\n`, JSON.stringify(positionsResult, null, 2));
  
  // 4. Call closePosition()
  const closeResult = await closePosition({ position_address: syntheticId, reason: "Manual verification" });
  console.log(`[4] closePosition returned:`, closeResult);
  
  // 5. Confirm removed from active
  const activeAfter = await loadPaperPositions(activePath);
  console.log(`[5] Active positions count after close: ${activeAfter.length}`);
  
  // 6. Confirm appears in archive
  const archiveAfter = await loadPaperPositions(archivePath);
  console.log(`[6] Archive contains closed position: ${archiveAfter.some(p => p.position === syntheticId && p.close_reason === "Manual verification")}`);
}

run().catch(console.error);
