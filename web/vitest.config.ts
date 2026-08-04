import { defineConfig } from "vitest/config";

// tsconfig sets `jsx: preserve` for Next.js; vitest / oxc need an explicit
// JSX transform to load .tsx sources during tests. Scoped to test runs only.
export default defineConfig({
  oxc: {
    jsx: {
      runtime: "automatic",
    },
  },
});
