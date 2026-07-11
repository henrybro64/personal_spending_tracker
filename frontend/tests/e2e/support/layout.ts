import type { Locator } from "@playwright/test";

export async function countGridColumns(locator: Locator) {
  return locator.evaluate((element) => {
    const columns = getComputedStyle(element).gridTemplateColumns.trim();
    if (!columns || columns === "none") {
      return 0;
    }
    return columns.split(/\s+/).filter(Boolean).length;
  });
}
