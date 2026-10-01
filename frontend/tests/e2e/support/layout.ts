import { expect, type Locator } from "@playwright/test";

export async function countGridColumns(locator: Locator) {
  return locator.evaluate((element) => {
    const columns = getComputedStyle(element).gridTemplateColumns.trim();
    if (!columns || columns === "none") {
      return 0;
    }
    let depth = 0;
    let count = 0;
    let inToken = false;
    for (const char of columns) {
      if (char === "(") {
        depth += 1;
      } else if (char === ")") {
        depth = Math.max(0, depth - 1);
      }
      if (/\s/.test(char) && depth === 0) {
        if (inToken) {
          count += 1;
          inToken = false;
        }
      } else {
        inToken = true;
      }
    }
    return inToken ? count + 1 : count;
  });
}

export async function expectGridColumns(locator: Locator, count: number) {
  await expect(locator).toBeVisible();
  await expect
    .poll(async () => {
      const measured = await countGridColumns(locator);
      if (measured === count) {
        return count;
      }
      const style = await locator.evaluate((element) => {
        const computed = getComputedStyle(element);
        return `${computed.display} ${computed.gridTemplateColumns}`;
      });
      return `${measured} (${style})`;
    })
    .toBe(count);
}
