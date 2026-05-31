#!/usr/bin/env node
/**
 * scripts/gen-bot-wallet.js
 *
 * Generate a new dedicated Solana bot wallet for Phase 1 execution.
 *
 * SECURITY RULES:
 *   - Prints public address and private key ONCE to stdout.
 *   - Does NOT write the private key to any git-tracked file.
 *   - Optionally appends BOT_WALLET_PRIVATE_KEY to .env if it is gitignored.
 *   - Never commits private keys to GitHub.
 *
 * Usage:
 *   node scripts/gen-bot-wallet.js
 *   node scripts/gen-bot-wallet.js --write-env   # append to .env (gitignored)
 */

import { Keypair } from "@solana/web3.js";
import bs58 from "bs58";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");
const ENV_FILE = path.join(ROOT, ".env");
const GITIGNORE_FILE = path.join(ROOT, ".gitignore");

const writeEnv = process.argv.includes("--write-env");

// ─── Generate keypair ─────────────────────────────────────────
const keypair = Keypair.generate();
const publicKey = keypair.publicKey.toBase58();
const privateKeyBase58 = bs58.encode(keypair.secretKey);

// ─── Safety: verify .env is gitignored ───────────────────────
function isEnvGitignored() {
  if (!fs.existsSync(GITIGNORE_FILE)) return false;
  const content = fs.readFileSync(GITIGNORE_FILE, "utf8");
  return content.split("\n").some((line) => {
    const trimmed = line.trim();
    return trimmed === ".env" || trimmed === "*.env" || trimmed === ".env*";
  });
}

// ─── Output ───────────────────────────────────────────────────
console.log(`
╔══════════════════════════════════════════════════════════════╗
║          MERIDIAN — Bot Wallet Generator                     ║
║                                                              ║
║  ⚠️  SECURITY WARNING                                        ║
║  This private key is shown ONCE. Store it securely.          ║
║  NEVER commit it to GitHub or share it publicly.             ║
╚══════════════════════════════════════════════════════════════╝

Bot Wallet Public Address:
  ${publicKey}

Bot Wallet Private Key (base58):
  ${privateKeyBase58}

─────────────────────────────────────────────────────────────
Add to your .env file (which is gitignored):

  BOT_WALLET_PRIVATE_KEY=${privateKeyBase58}

Also add to .env to enable live execution when ready:

  ALLOW_LIVE_EXECUTION=false

─────────────────────────────────────────────────────────────
Fund this wallet with a small amount of SOL before going live.
The wallet starts with zero funds — live execution is impossible
until you manually fund it and set ALLOW_LIVE_EXECUTION=true.
─────────────────────────────────────────────────────────────
`);

// ─── Optional: append to .env ────────────────────────────────
if (writeEnv) {
  if (!isEnvGitignored()) {
    console.error("ERROR: .env is not listed in .gitignore. Refusing to write private key.");
    console.error("Add '.env' to .gitignore first, then re-run with --write-env.");
    process.exit(1);
  }

  // Check if BOT_WALLET_PRIVATE_KEY already exists in .env
  if (fs.existsSync(ENV_FILE)) {
    const existing = fs.readFileSync(ENV_FILE, "utf8");
    if (existing.includes("BOT_WALLET_PRIVATE_KEY=")) {
      console.error("ERROR: BOT_WALLET_PRIVATE_KEY already exists in .env. Remove it first.");
      process.exit(1);
    }
  }

  const lines = [
    "",
    "# ── Bot Wallet (dedicated execution wallet — Phase 1) ──────────────────────",
    `BOT_WALLET_PRIVATE_KEY=${privateKeyBase58}`,
    "ALLOW_LIVE_EXECUTION=false",
    "",
  ].join("\n");

  fs.appendFileSync(ENV_FILE, lines, "utf8");
  console.log(`✅ Appended BOT_WALLET_PRIVATE_KEY to .env`);
  console.log(`   ALLOW_LIVE_EXECUTION=false (live execution disabled by default)`);
  console.log(`   Fund the wallet, then set ALLOW_LIVE_EXECUTION=true when ready.\n`);
} else {
  console.log("Tip: Run with --write-env to automatically append to .env (gitignored).\n");
}
