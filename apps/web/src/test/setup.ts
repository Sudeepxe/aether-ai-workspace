import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import "@testing-library/jest-dom/vitest";

// RTL's own auto-cleanup registers via the global `afterEach`, which
// isn't installed since this project keeps `globals: false` (explicit
// imports everywhere else) — so it's wired explicitly here instead.
afterEach(() => {
  cleanup();
});
