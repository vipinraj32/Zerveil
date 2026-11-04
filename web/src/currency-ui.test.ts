// Guards the currency rules of the UI: business amounts are USD ("$40.00", via format.ts); the payment asset (TAZ, testnet
// Zcash) may only be named in genuinely technical places. Reads every source file as text (Vite's import.meta.glob, so no
// Node types are needed) and checks the code with comments stripped.
import { describe, expect, it } from "vitest";

const sources = import.meta.glob(["./**/*.tsx", "./**/*.ts", "!./**/*.test.ts", "!./**/*.test.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Files allowed to contain a TAZ/ZEC literal. Keep this minimal; every entry must be technical. */
const ASSET_LITERAL_ALLOWLIST: Record<string, string> = {
  // The formatter module: the payment-asset default (set from /api/status), the legacy unit and legacy-transcript matching.
  "./format.ts": "formatter: payment asset default, LEGACY_ASSET, LEGACY_AMOUNT_RE",
  // API types: ZcashInfo.ticker is "TAZ" | "ZEC" (network metadata from /api/status).
  "./types.ts": "API type of the network ticker",
  // Payments components: only the wallet/network status panel's faucet line ("you claim TAZ yourself") names the asset literally;
  // the checkout's technical block takes the asset from the invoice.
  "./components/Payments.tsx": "faucet info in the wallet/network status panel",
};

/** Source without comments (block, JSX and line comments; "https://" is kept). */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`\\])\/\/.*$/gm, "$1");
}

const ASSET_RE = /\b(TAZ|ZEC)\b/;

describe("currency UI", () => {
  it("finds the source files", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(20);
    expect(Object.keys(sources)).toContain("./components/Payments.tsx");
  });

  it("names TAZ/ZEC only in the technical allowlist", () => {
    const offenders: string[] = [];
    for (const [file, src] of Object.entries(sources)) {
      if (file in ASSET_LITERAL_ALLOWLIST) continue;
      stripComments(src)
        .split("\n")
        .forEach((line, i) => {
          if (ASSET_RE.test(line)) offenders.push(`${file}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(offenders).toEqual([]);
  });

  it("allowlist entries still need to be there (no stale entries)", () => {
    for (const file of Object.keys(ASSET_LITERAL_ALLOWLIST)) {
      expect(sources[file], `${file} is missing`).toBeDefined();
      expect(ASSET_RE.test(stripComments(sources[file])), `${file} no longer names TAZ/ZEC: drop it from the allowlist`).toBe(true);
    }
  });

  it("never builds money text by hand ($ + toFixed / toLocaleString, or a USD suffix on an amount)", () => {
    const patterns = [
      /\$\$\{[^}]*\.(toFixed|toLocaleString)\(/, // `$${n.toFixed(2)}`
      /["'`]\$["'`]\s*\+/, // "$" + n
      />\s*\$\{?[^<]*\.toFixed\(/, // <span>${n.toFixed(2)}</span>
      /\.toFixed\(\d\)\s*\}?\s*(USD|TAZ|ZEC)\b/, // `${n.toFixed(2)} USD`
      /\.toFixed\(\d\)\}?\s*\$/, // n.toFixed(2)$
      /style:\s*["']currency["']/, // Intl currency formatting outside format.ts
    ];
    const offenders: string[] = [];
    for (const [file, src] of Object.entries(sources)) {
      if (file === "./format.ts") continue;
      stripComments(src)
        .split("\n")
        .forEach((line, i) => {
          if (patterns.some((p) => p.test(line))) offenders.push(`${file}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(offenders).toEqual([]);
  });

  it("never writes a USD suffix after a formatted amount", () => {
    const offenders: string[] = [];
    for (const [file, src] of Object.entries(sources)) {
      stripComments(src)
        .split("\n")
        .forEach((line, i) => {
          if (/(formatUsd|formatUSD|usd)\([^)]*\)\}?\s*(<span>)?\s*USD\b/.test(line)) offenders.push(`${file}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(offenders).toEqual([]);
  });
});
