import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Vendored MapLibre bundles are minified worker/ABI shims checked into the
    // repo, not source. Linting them produced ~1100 warnings and buried any
    // real finding from app/.
    "public/**",
  ]),
  {
    // The postinstall patcher runs on bare Node before Next is involved, so it
    // is CommonJS by design rather than by accident.
    files: ["scripts/**/*.cjs"],
    rules: { "@typescript-eslint/no-require-imports": "off" },
  },
]);

export default eslintConfig;
